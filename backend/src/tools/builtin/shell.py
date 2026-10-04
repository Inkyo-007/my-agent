"""内置 shell 工具：shell_command

约定（详见 docs/design/backend/tools.md「内置工具」一节）：
- 方言无关：工具不感知 bash / pwsh 的差异，只按注入的 ShellProgram
  （可执行文件 + 参数模板）拼接调用；方言探测与映射在 application 层；
- 安全裁决上移：本工具不设任何护栏——危险命令拦截、工作区外 cwd 审批
  都由 security 层在执行前完成（command 参数标注 format: "command"、
  cwd 标注 format: "path"，供 security 声明式提取）；
- 失败即结果：非零退出码、超时、启动失败都返回 success=False 加具体
  文案，不抛异常。
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ...core import Tool, ToolDefinition, ToolInputSchema, ToolResult

# 输出（stdout 与 stderr 合并后）的最大长度：足以覆盖常见构建/测试日志的
# 尾部，又不至于一次撑爆上下文；截断时标注总长度
MAX_OUTPUT_CHARS = 30_000


@dataclass(frozen=True)
class ShellProgram:
    """如何调用一个 shell：可执行文件 + 参数模板（命令串追加在末尾）

    例如 bash → ShellProgram("bash", ["-c"])，
    pwsh → ShellProgram("pwsh", ["-NoProfile", "-Command"])。
    工具不认识方言枚举（避免 tools → security 的依赖），方言到调用方式
    的映射由 application 层的探测函数完成。
    """

    program: str
    args: List[str]

    def describe(self) -> str:
        """给模型看的方言提示（写进工具描述）"""
        return f"{self.program} {' '.join(self.args)} <命令>"


class ShellCommandTool(Tool):
    """执行一条 shell 命令（危险命令与工作区外 cwd 由 security 裁决）"""

    def __init__(self, shell: ShellProgram, root_dir: str | Path):
        self._shell = shell
        self._root = Path(root_dir).resolve()
        # definition 是实例属性而非类属性：描述需告知模型当前方言，
        # 模型据此写出语法正确的命令
        self.definition = ToolDefinition(
            name="shell_command",
            description=(
                f"执行一条 shell 命令（解释器：{shell.describe()}），"
                "返回合并后的标准输出与错误输出及退出码。"
                "命令默认在工作区根目录下执行，可用 cwd 指定其他目录。"
            ),
            input_schema=ToolInputSchema(
                properties={
                    "command": {
                        "type": "string",
                        "format": "command",
                        "description": "要执行的 shell 命令",
                    },
                    "cwd": {
                        "type": "string",
                        "format": "path",
                        "description": "工作目录（相对工作区根目录，或绝对路径；默认工作区根目录）",
                    },
                },
                required=["command"],
            ),
            permission_required=["shell:execute"],
            timeout_seconds=120,
        )

    async def call(self, params: Dict[str, Any]) -> ToolResult:
        command = str(params.get("command", ""))
        if not command.strip():
            return ToolResult(
                success=False, content="参数 command 不能为空", execution_time=0.0
            )
        cwd = self._resolve_cwd(str(params.get("cwd", "")))
        if cwd is None:
            return ToolResult(
                success=False,
                content=f"工作目录不存在或不是目录：{params.get('cwd')}",
                execution_time=0.0,
            )
        return await self._run(command, cwd)

    def _resolve_cwd(self, cwd: str) -> Path | None:
        """解析工作目录：空 = 工作区根目录；相对路径基于根目录

        不做边界检查：工作区外 cwd 的裁决在 security 层（经审批放行后
        应能真实执行），本工具只负责「解析并验证目录存在」。
        """
        target = (self._root / cwd).resolve() if cwd else self._root
        if not target.is_dir():
            return None
        return target

    async def _run(self, command: str, cwd: Path) -> ToolResult:
        start = asyncio.get_running_loop().time()
        try:
            process = await asyncio.create_subprocess_exec(
                self._shell.program,
                *self._shell.args,
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # 合并：保持输出的时序
                cwd=str(cwd),
            )
        except OSError as e:
            return ToolResult(
                success=False,
                content=f"shell 启动失败：{type(e).__name__}: {e}",
                execution_time=0.0,
            )

        try:
            stdout, _ = await process.communicate()
        except asyncio.CancelledError:
            # executor 超时通过取消传达；取消时必须杀掉子进程，否则泄漏
            process.kill()
            await process.wait()
            raise

        elapsed = asyncio.get_running_loop().time() - start
        output = stdout.decode("utf-8", errors="replace").rstrip()
        if len(output) > MAX_OUTPUT_CHARS:
            output = (
                output[:MAX_OUTPUT_CHARS] + f"\n…（输出共 {len(output)} 字符，已截断）"
            )

        if process.returncode == 0:
            return ToolResult(
                success=True,
                content=output or "（命令执行成功，无输出）",
                execution_time=elapsed,
            )
        body = f"退出码 {process.returncode}"
        if output:
            body += f"\n{output}"
        return ToolResult(success=False, content=body, execution_time=elapsed)
