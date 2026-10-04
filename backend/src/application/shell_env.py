"""shell 环境探测：找到可用的 shell 并给出调用方式与安全分析方言

探测顺序（Git Bash 主方言，pwsh 降级，见 docs/design/backend/security.md）：
1. Git Bash：先查常见安装路径，再查 PATH——PATH 中的 bash.exe 可能是
   WSL 启动器（System32\\bash.exe / WindowsApps 下的别名 stub），
   它在 Linux 文件系统上下文中运行，Windows 风格的 cwd 与路径参数
   会全部失真，必须排除；
2. pwsh（PowerShell 7+）；
3. powershell（Windows PowerShell 5.1，与 pwsh 共用护栏方言）。

返回 None 表示本机没有可用 shell：不注册 shell_command 工具即可，
其余工具不受影响。
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..security import ShellDialect
from ..tools.builtin import ShellProgram


@dataclass(frozen=True)
class DetectedShell:
    """一次探测的结果：工具的调用方式 + 护栏的分析方言（两者必须一致）"""

    program: ShellProgram
    dialect: ShellDialect


def _git_bash_candidates() -> List[str]:
    """Git Bash 的常见安装路径（先于此处 PATH 中的 WSL bash）"""
    candidates = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(str(Path(base) / "Git" / "bin" / "bash.exe"))
    return candidates


def _is_wsl_bash(path: str) -> bool:
    """PATH 中的 bash.exe 是否为 WSL 启动器（而非真正的 Git Bash）"""
    lowered = path.lower()
    return "system32" in lowered or "windowsapps" in lowered


def _find_git_bash() -> str | None:
    for candidate in _git_bash_candidates():
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("bash")
    if found and not _is_wsl_bash(found):
        return found
    return None


def detect_shell() -> DetectedShell | None:
    """探测本机可用的 shell；None 表示没有可用 shell"""
    git_bash = _find_git_bash()
    if git_bash:
        return DetectedShell(ShellProgram(git_bash, ["-c"]), ShellDialect.BASH)
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return DetectedShell(
                ShellProgram(found, ["-NoProfile", "-Command"]), ShellDialect.PWSH
            )
    return None
