"""内置文件工具：read_file / write_file

约定（详见 docs/design/backend/tools.md「内置工具」一节）：
- 路径解析：root_dir 构造时注入，相对路径基于根目录 resolve（含符号
  链接）；本模块不做边界检查——路径内/外的裁决在 security 层
  （工作区外经审批放行），工具只负责忠实执行；
- 输出截断：read_file 单次默认最多返回 MAX_LINES 行，截断时标注总行数
  与已显示范围，模型可据 offset/limit 分页再读；
- 失败即结果：文件不存在、非 UTF-8 等都返回 success=False
  加具体文案，不抛异常。
"""

import asyncio
from pathlib import Path
from typing import Any, Dict

from ...core import Tool, ToolDefinition, ToolInputSchema, ToolResult

# read_file 单次返回的默认最大行数：足以覆盖大多数源码文件，
# 又不至于一次撑爆上下文
MAX_LINES = 2000


def _resolve(root: Path, path: str) -> Path:
    """将工具参数路径解析为绝对路径（相对路径基于工作区根目录）

    resolve() 会展开符号链接。不做边界检查：路径内/外由 security
    统一裁决（工作区外经审批放行），见 docs/design/backend/security.md。
    """

    return (root / path).resolve()


def _fail(message: str) -> ToolResult:
    return ToolResult(success=False, content=message, execution_time=0.0)


class ReadFileTool(Tool):
    """读取文件内容（带行号，超限截断）；界外读取由 security 审批"""

    definition = ToolDefinition(
        name="read_file",
        description=(
            "读取文件的内容，返回带行号的文本。路径相对工作区根目录解析"
            "（也可是绝对路径）；访问工作区外的文件会先经用户审批。"
            "文件较大时只返回 limit 指定的行数并标注截断，可用 offset/limit 分页读取。"
        ),
        input_schema=ToolInputSchema(
            properties={
                "path": {
                    "type": "string",
                    "format": "path",
                    "description": "文件路径（相对工作区根目录，或绝对路径）",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（从 1 开始，默认 1）",
                },
                "limit": {
                    "type": "integer",
                    "description": f"最多读取的行数（默认 {MAX_LINES}）",
                },
            },
            required=["path"],
        ),
        permission_required=["fs:read"],
        timeout_seconds=10,
    )

    def __init__(self, root_dir: str | Path):
        self._root = Path(root_dir).resolve()

    async def call(self, params: Dict[str, Any]) -> ToolResult:
        path = str(params.get("path", ""))
        offset = params.get("offset", 1)
        limit = params.get("limit", MAX_LINES)
        if not isinstance(offset, int) or offset < 1:
            return _fail("参数 offset 必须是从 1 开始的整数")
        if not isinstance(limit, int) or limit < 1:
            return _fail("参数 limit 必须是正整数")
        return await asyncio.to_thread(self._read, path, offset, limit)

    def _read(self, path: str, offset: int, limit: int) -> ToolResult:
        target = _resolve(self._root, path)
        if not target.exists():
            return _fail(f"文件不存在：{path}")
        if target.is_dir():
            return _fail(f"路径是目录而非文件：{path}")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _fail(f"无法以 UTF-8 解码（可能是二进制文件）：{path}")
        except OSError as e:
            return _fail(f"读取失败：{type(e).__name__}: {e}")

        lines = text.splitlines()
        total = len(lines)
        if offset > total and total > 0:
            return _fail(f"起始行 {offset} 超出文件总行数（共 {total} 行）")

        window = lines[offset - 1 : offset - 1 + limit]
        # 将文件片段按行号格式化，行号右对齐 6 个字符，行内容在竖线后显示
        body = "\n".join(
            f"{lineno:>6} | {line}" for lineno, line in enumerate(window, start=offset)
        )

        end = offset - 1 + len(window)
        if end < total:
            body += (
                f"\n（已截断：文件共 {total} 行，仅显示第 {offset}-{end} 行，"
                f"可用 offset={end + 1} 继续读取）"
            )
        return ToolResult(success=True, content=body, execution_time=0.0)


class WriteFileTool(Tool):
    """写入文件（不存在则创建，已存在则覆盖，自动创建父目录）；界外写入由 security 审批"""

    definition = ToolDefinition(
        name="write_file",
        description=(
            "将内容写入文件：不存在则创建（自动创建父目录），已存在则整体覆盖。"
            "路径相对工作区根目录解析（也可是绝对路径）；写入工作区外的文件"
            "会先经用户审批。如需局部修改，先 read_file 再整体重写。"
        ),
        input_schema=ToolInputSchema(
            properties={
                "path": {
                    "type": "string",
                    "format": "path",
                    "description": "文件路径（相对工作区根目录，或绝对路径）",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整内容",
                },
            },
            required=["path", "content"],
        ),
        permission_required=["fs:write"],
        timeout_seconds=10,
    )

    def __init__(self, root_dir: str | Path):
        self._root = Path(root_dir).resolve()

    async def call(self, params: Dict[str, Any]) -> ToolResult:
        path = str(params.get("path", ""))
        content = str(params.get("content", ""))
        return await asyncio.to_thread(self._write, path, content)

    def _write(self, path: str, content: str) -> ToolResult:
        target = _resolve(self._root, path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # write_bytes 而非 write_text：避免 Windows 文本模式把 \n 转成 \r\n，
            # 保证写入内容与模型产出逐字节一致（成功消息的字节数也因此准确）
            target.write_bytes(content.encode("utf-8"))
        except OSError as e:
            return _fail(f"写入失败：{type(e).__name__}: {e}")
        # 界外路径无法 relative_to（会抛 ValueError），此时展示绝对路径
        try:
            shown = str(target.relative_to(self._root))
        except ValueError:
            shown = str(target)
        return ToolResult(
            success=True,
            content=f"已写入 {shown}（{len(content.encode('utf-8'))} 字节）",
            execution_time=0.0,
        )
