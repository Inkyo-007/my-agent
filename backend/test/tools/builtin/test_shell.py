"""tools/builtin/shell.py 的单元测试

真实拉起子进程（纯本地，不消耗网络）：验证输出捕获、退出码、cwd 解析
（含工作区外——围栏已上移 security，工具层不再拦截）、空命令与坏目录、
取消时子进程被回收。

bash 用例用 Git Bash（application.detect_shell 的探测结果），本机没有
Git Bash 时跳过；pwsh 用例验证参数模板，本机没有 pwsh 时跳过。
"""

import asyncio
import shutil

import pytest

from src.application import detect_shell
from src.security import ShellDialect
from src.tools.builtin import ShellCommandTool, ShellProgram


def _bash_program() -> ShellProgram:
    shell = detect_shell()
    if shell is None or shell.dialect is not ShellDialect.BASH:
        pytest.skip("本机未安装 Git Bash")
    return shell.program


def _pwsh_program() -> ShellProgram:
    found = shutil.which("pwsh")
    if not found:
        pytest.skip("本机未安装 pwsh")
    return ShellProgram(found, ["-NoProfile", "-Command"])


class Test定义与标注:
    def test_权限标签与format标注(self, tmp_path):
        tool = ShellCommandTool(ShellProgram("bash", ["-c"]), tmp_path)

        assert tool.definition.permission_required == ["shell:execute"]
        props = tool.definition.input_schema.properties
        assert props is not None
        assert props["command"]["format"] == "command"
        assert props["cwd"]["format"] == "path"

    def test_描述中告知模型当前方言(self, tmp_path):
        tool = ShellCommandTool(
            ShellProgram("pwsh", ["-NoProfile", "-Command"]), tmp_path
        )

        assert "pwsh -NoProfile -Command" in tool.definition.description


class TestBash执行:
    async def test_捕获标准输出(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "echo hello"})

        assert result.success is True
        assert "hello" in result.content

    async def test_合并错误输出并保持时序(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "echo out; echo err >&2"})

        assert result.success is True
        assert "out" in result.content
        assert "err" in result.content

    async def test_非零退出码(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "echo oops; exit 3"})

        assert result.success is False
        assert "退出码 3" in result.content
        assert "oops" in result.content

    async def test_无输出时的占位文案(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "true"})

        assert result.success is True
        assert "无输出" in result.content

    async def test_默认工作目录为工作区根(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "touch created.txt"})

        assert result.success is True
        assert (tmp_path / "created.txt").exists()

    async def test_相对cwd(self, tmp_path):
        (tmp_path / "sub").mkdir()
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "touch created.txt", "cwd": "sub"})

        assert result.success is True
        assert (tmp_path / "sub" / "created.txt").exists()

    async def test_工作区外cwd不被工具拦截(self, tmp_path, tmp_path_factory):
        # 界外 cwd 的审批在 security 层；工具只负责忠实执行
        outside = tmp_path_factory.mktemp("outside")
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "touch out.txt", "cwd": str(outside)})

        assert result.success is True
        assert (outside / "out.txt").exists()

    async def test_cwd不存在(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "echo hi", "cwd": "不存在目录"})

        assert result.success is False
        assert "工作目录不存在" in result.content

    async def test_空命令(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "   "})

        assert result.success is False
        assert "不能为空" in result.content

    async def test_超长输出截断并标注(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        result = await tool.call({"command": "seq 1 20000"})

        assert result.success is True
        assert "已截断" in result.content
        assert len(result.content) < 40_000

    async def test_取消时子进程被回收(self, tmp_path):
        tool = ShellCommandTool(_bash_program(), tmp_path)

        task = asyncio.create_task(tool.call({"command": "sleep 30"}))
        await asyncio.sleep(0.2)  # 让子进程先起来
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # kill + wait 已 awaited：进程已回收，task 立即结束而非悬挂


class TestPwsh参数模板:
    async def test_NoProfile_Command模板可执行(self, tmp_path):
        tool = ShellCommandTool(_pwsh_program(), tmp_path)

        result = await tool.call({"command": "Write-Output hello"})

        assert result.success is True
        assert "hello" in result.content
