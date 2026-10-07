"""路径解析原语：tools 与 security 共用的路径规范化与 resolve

为什么放在 core：工具执行（tools）与安全判定（security）必须对同一个
路径字符串解析出同一个文件，否则会出现「审批的是 A、执行的是 B」的
错位。两端共用一份解析实现，从机制上消除这种不一致。
"""

import re
import unicodedata
from pathlib import Path

# Git Bash/MSYS 风格的盘符路径：/d/... → D:/...
# shell 是 Git Bash 时，模型很容易从 pwd 输出中学到这种形态；
# pathlib 会把它错拼成 <root 所在盘>:\d\...，必须在 resolve 之前纠正。
# 仅匹配「单字母 + / 或结尾」——/etc、/tmp 这类不以盘符开头的形态不受影响
_MSYS_DRIVE_RE = re.compile(r"^/([a-zA-Z])(?:/|$)")

# 路径长度上限：输入卫生，防止异常超长输入
_MAX_PATH_LENGTH = 4096


def normalize_path(path: str) -> str:
    """规范化路径字符串的表示形式（不触碰文件系统）

    - MSYS 盘符路径：`/d/Project` → `D:/Project`（正斜杠避免与后续
      路径段的分隔符混杂）；
    - NFC 归一化：消除 Unicode 形态差异（如 macOS 的 NFD 文件名），
      保证「判定比较」与「文件系统访问」看到的是同一形态。
    """
    path = _MSYS_DRIVE_RE.sub(lambda m: f"{m.group(1).upper()}:/", path)
    return unicodedata.normalize("NFC", path)


def resolve_path(root: Path, path: str) -> Path | None:
    """规范化并 resolve 为绝对路径；非法路径返回 None

    - 相对路径基于 root 解析，绝对路径原样 resolve（pathlib 语义：
      右侧为绝对路径时忽略左侧）；
    - 空串、超长、含空字节 → None；
    - resolve() 展开符号链接；非 strict，不要求路径存在（写入新文件
      的场景适用）；
    - root 须由调用方预先 resolve。
    """
    if not path or len(path) > _MAX_PATH_LENGTH or "\x00" in path:
        return None
    try:
        return (root / normalize_path(path)).resolve()
    except (OSError, ValueError):
        return None
