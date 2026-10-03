"""security 层 · 命令护栏规则表（数据，与 guardrails.py 的引擎分离）

规则即数据：token 前缀匹配（大小写不敏感），pattern 首词与规范化后的
argv[0] 比较。每条规则带 match / not_match 自测样例（借鉴 Codex
execpolicy），在 pytest 中逐条校验，防止规则表腐烂。

档位语义：BLOCK = 硬阻断（无合法场景，任何模式拒绝）；FLAG = 标注
（进审批提示与审计，不改变裁决）。
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple


class ShellDialect(Enum):
    """shell 方言（Git Bash 主方言，pwsh 降级）"""

    BASH = "bash"
    PWSH = "pwsh"


class RuleVerdict(Enum):
    """规则命中后的处置档位"""

    BLOCK = "block"  # 硬阻断：无合法场景，任何模式拒绝
    FLAG = "flag"  # 标注：进审批提示与审计，不改变裁决


@dataclass(frozen=True)
class CommandRule:
    """一条命令规则：token 前缀匹配（大小写不敏感）

    pattern 首词与规范化后的 argv[0] 比较，其余位置与原始 token 比较。
    """

    name: str
    pattern: Tuple[str, ...]
    verdict: RuleVerdict
    reason: str  # 用户可读理由（进审批提示 / 喂回模型）
    dialects: Tuple[ShellDialect, ...]
    match: Tuple[str, ...] = ()  # 自测样例：应当匹配的命令行
    not_match: Tuple[str, ...] = ()  # 自测样例：不应匹配的命令行


_BASH = ShellDialect.BASH
_PWSH = ShellDialect.PWSH
_BOTH = (_BASH, _PWSH)
_BLOCK = RuleVerdict.BLOCK
_FLAG = RuleVerdict.FLAG

# fmt: off
RULES: List[CommandRule] = [
    # —— 硬阻断（BLOCK）：无合法场景 ——
    *[
        CommandRule(
            name="rm-root",
            pattern=("rm", flags, target),
            verdict=_BLOCK,
            reason="递归强制删除根目录或家目录，不可逆",
            dialects=(_BASH,),
            match=(f"rm {flags} {target}",),
            not_match=("rm -rf ./build",),
        )
        for flags in ("-rf", "-fr")
        for target in ("/", "/*", "~")
    ],
    CommandRule("rm-no-preserve-root", ("rm", "-rf", "--no-preserve-root"), _BLOCK,
                "显式关闭根目录保护后递归删除", (_BASH,),
                match=("rm -rf --no-preserve-root /",), not_match=("rm -rf /tmp/x",)),
    CommandRule("mkfs", ("mkfs",), _BLOCK, "磁盘格式化", (_BASH,),
                match=("mkfs /dev/sda", "mkfs -t ext4 /dev/sda1"), not_match=("mkfsutil",)),
    CommandRule("fdisk", ("fdisk",), _BLOCK, "磁盘分区操作", (_BASH,),
                match=("fdisk /dev/sda",), not_match=()),
    CommandRule("parted", ("parted",), _BLOCK, "磁盘分区操作", (_BASH,),
                match=("parted /dev/sda",), not_match=()),
    CommandRule("shutdown", ("shutdown",), _BLOCK, "系统关机/重启", _BOTH,
                match=("shutdown /s", "shutdown -h now"), not_match=()),
    CommandRule("reboot", ("reboot",), _BLOCK, "系统重启", (_BASH,),
                match=("reboot",), not_match=()),
    CommandRule("halt", ("halt",), _BLOCK, "系统停机", (_BASH,), match=("halt",), not_match=()),
    CommandRule("poweroff", ("poweroff",), _BLOCK, "系统关机", (_BASH,),
                match=("poweroff",), not_match=()),
    CommandRule("format", ("format",), _BLOCK, "磁盘格式化", _BOTH,
                match=("format C:",), not_match=("ruff format .",)),
    CommandRule("diskpart", ("diskpart",), _BLOCK, "磁盘分区操作", _BOTH,
                match=("diskpart",), not_match=()),
    CommandRule("stop-computer", ("stop-computer",), _BLOCK, "系统关机", (_PWSH,),
                match=("Stop-Computer",), not_match=()),
    CommandRule("restart-computer", ("restart-computer",), _BLOCK, "系统重启", (_PWSH,),
                match=("Restart-Computer -Force",), not_match=()),
    CommandRule("format-volume", ("format-volume",), _BLOCK, "磁盘格式化", (_PWSH,),
                match=("Format-Volume -DriveLetter D",), not_match=()),
    CommandRule("clear-disk", ("clear-disk",), _BLOCK, "清空磁盘数据", (_PWSH,),
                match=("Clear-Disk -Number 1",), not_match=()),
    # —— 标注（FLAG）：不改变裁决，进审批提示与审计 ——
    CommandRule("rm-recursive", ("rm", "-r"), _FLAG, "递归删除，不可恢复", (_BASH,),
                match=("rm -r build",), not_match=("rm file.txt",)),
    CommandRule("rm-recursive", ("rm", "-rf"), _FLAG, "递归强制删除，不可恢复", (_BASH,),
                match=("rm -rf build",), not_match=("rm file.txt",)),
    CommandRule("rm-recursive", ("rm", "-fr"), _FLAG, "递归强制删除，不可恢复", (_BASH,),
                match=("rm -fr build",), not_match=("rm file.txt",)),
    CommandRule("remove-item-recurse", ("remove-item", "-recurse"), _FLAG,
                "递归删除，不可恢复", (_PWSH,),
                match=("Remove-Item -Recurse build", "rm -Recurse build"), not_match=("Remove-Item a.txt",)),
    CommandRule("remove-item-recurse", ("remove-item", "-r"), _FLAG,
                "递归删除（参数缩写），不可恢复", (_PWSH,),
                match=("Remove-Item -r build",), not_match=("Remove-Item a.txt",)),
    CommandRule("git-push-force", ("git", "push", "--force"), _FLAG,
                "强制推送，可能覆盖远端历史", _BOTH,
                match=("git push --force",), not_match=("git push origin main", "git push --force-with-lease")),
    CommandRule("git-push-force", ("git", "push", "-f"), _FLAG,
                "强制推送，可能覆盖远端历史", _BOTH,
                match=("git push -f origin main",), not_match=("git push origin main",)),
    CommandRule("git-reset-hard", ("git", "reset", "--hard"), _FLAG,
                "丢弃全部未提交更改", _BOTH,
                match=("git reset --hard HEAD~1",), not_match=("git reset --soft HEAD~1",)),
    CommandRule("git-clean-force", ("git", "clean", "-f"), _FLAG,
                "删除未跟踪文件，不可恢复", _BOTH,
                match=("git clean -f",), not_match=("git clean -n",)),
    CommandRule("git-clean-force", ("git", "clean", "-fd"), _FLAG,
                "删除未跟踪文件与目录，不可恢复", _BOTH,
                match=("git clean -fd",), not_match=("git clean -nd",)),
    CommandRule("network-fetch", ("curl",), _FLAG,
                "访问网络；下载内容可能随后被执行", (_BASH,),
                match=("curl https://example.com",), not_match=("echo curl",)),
    CommandRule("network-fetch", ("wget",), _FLAG,
                "访问网络；下载内容可能随后被执行", (_BASH,),
                match=("wget https://example.com",), not_match=("echo wget",)),
    CommandRule("network-fetch", ("invoke-webrequest",), _FLAG,
                "访问网络；下载内容可能随后被执行", (_PWSH,),
                match=("Invoke-WebRequest https://example.com", "curl https://example.com"),
                not_match=("echo curl",)),
    CommandRule("dd", ("dd",), _FLAG,
                "原始块写入，写设备文件则不可逆", (_BASH,),
                match=("dd if=a of=b",), not_match=()),
    CommandRule("sudo", ("sudo",), _FLAG, "提权执行", (_BASH,),
                match=("sudo apt update",), not_match=("sudoedit /etc/hosts",)),
]
# fmt: on
