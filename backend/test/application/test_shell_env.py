"""application/shell_env.py 的单元测试

探测结果分两部分断言：
- 纯逻辑（WSL bash 识别、优先级、全部缺失）用 monkeypatch 离线验证；
- 真实探测只断言「若探测到则调用方式与方言一致且程序真实存在」，
  不断言本机一定装了某个 shell。
"""

import os

from src.application import shell_env
from src.application.shell_env import detect_shell
from src.security import ShellDialect


class TestWslBash识别:
    def test_system32下的bash是WSL(self):
        assert shell_env._is_wsl_bash(r"C:\Windows\System32\bash.exe") is True

    def test_WindowsApps下的bash是WSL别名(self):
        assert (
            shell_env._is_wsl_bash(
                r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe"
            )
            is True
        )

    def test_GitBash不是WSL(self):
        assert shell_env._is_wsl_bash(r"C:\Program Files\Git\bin\bash.exe") is False


class Test探测优先级:
    def test_GitBash优先(self, monkeypatch):
        monkeypatch.setattr(shell_env, "_find_git_bash", lambda: r"C:\Git\bash.exe")
        monkeypatch.setattr(
            shell_env.shutil, "which", lambda name: rf"C:\bin\{name}.exe"
        )

        shell = detect_shell()

        assert shell is not None
        assert shell.dialect is ShellDialect.BASH
        assert shell.program.program == r"C:\Git\bash.exe"
        assert shell.program.args == ["-c"]

    def test_无GitBash时降级pwsh(self, monkeypatch):
        monkeypatch.setattr(shell_env, "_find_git_bash", lambda: None)
        monkeypatch.setattr(
            shell_env.shutil,
            "which",
            lambda name: r"C:\bin\pwsh.exe" if name == "pwsh" else None,
        )

        shell = detect_shell()

        assert shell is not None
        assert shell.dialect is ShellDialect.PWSH
        assert shell.program.args == ["-NoProfile", "-Command"]

    def test_pwsh缺失时降级powershell(self, monkeypatch):
        monkeypatch.setattr(shell_env, "_find_git_bash", lambda: None)
        monkeypatch.setattr(
            shell_env.shutil,
            "which",
            lambda name: r"C:\bin\powershell.exe" if name == "powershell" else None,
        )

        shell = detect_shell()

        assert shell is not None
        assert shell.dialect is ShellDialect.PWSH
        assert "powershell" in shell.program.program

    def test_全部缺失时返回None(self, monkeypatch):
        monkeypatch.setattr(shell_env, "_find_git_bash", lambda: None)
        monkeypatch.setattr(shell_env.shutil, "which", lambda name: None)

        assert detect_shell() is None


class TestPATH中bash的WSL排除:
    def test_候选路径优先于PATH(self, monkeypatch):
        monkeypatch.setattr(
            shell_env, "_git_bash_candidates", lambda: [r"C:\Git\bin\bash.exe"]
        )
        monkeypatch.setattr(shell_env.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(
            shell_env.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe"
        )

        assert shell_env._find_git_bash() == r"C:\Git\bin\bash.exe"

    def test_候选缺失时PATH中的WSL被排除(self, monkeypatch):
        monkeypatch.setattr(shell_env, "_git_bash_candidates", lambda: [])
        monkeypatch.setattr(
            shell_env.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe"
        )

        assert shell_env._find_git_bash() is None


class Test真实探测:
    def test_探测结果自洽(self):
        shell = detect_shell()

        if shell is None:
            return  # 本机没有任何 shell，合法结果
        assert os.path.isfile(shell.program.program)
        if shell.dialect is ShellDialect.BASH:
            assert shell.program.args == ["-c"]
        else:
            assert shell.program.args == ["-NoProfile", "-Command"]
