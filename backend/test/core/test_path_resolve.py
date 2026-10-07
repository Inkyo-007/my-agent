"""core/paths.py 的离线单元测试

覆盖：MSYS 盘符路径纠正（本模块存在的核心理由）、NFC 归一化、
非法输入（空串、超长、空字节）、相对/绝对路径的 resolve 语义。
"""

import os
import unicodedata
from pathlib import Path

import pytest

from src.core import normalize_path, resolve_path


def _msys(path: Path) -> str:
    """把 Windows 绝对路径转成 Git Bash 风格：D:\\x\\y → /d/x/y"""
    s = str(path.resolve()).replace("\\", "/")
    return f"/{s[0].lower()}{s[2:]}"


class TestMSYS盘符纠正:
    def test_标准形态(self):
        assert normalize_path("/d/Project/x.txt") == "D:/Project/x.txt"

    def test_大写盘符(self):
        assert normalize_path("/D/Project") == "D:/Project"

    def test_仅盘符(self):
        assert normalize_path("/c") == "C:/"

    def test_单字母后非斜杠不纠正(self):
        # /etc、/tmp 不是盘符形态，保持原样（交给 pathlib 按当前盘解析）
        assert normalize_path("/etc/passwd") == "/etc/passwd"

    def test_中间段落不纠正(self):
        # 只处理开头的盘符形态
        assert normalize_path("sub/d/x") == "sub/d/x"

    def test_普通路径不受影响(self):
        assert normalize_path("D:\\Project\\x") == "D:\\Project\\x"
        assert normalize_path("relative/path.txt") == "relative/path.txt"


class TestNFC归一化:
    def test_NFD输入归一化为NFC(self):
        nfd = unicodedata.normalize("NFD", "配置.txt")
        assert normalize_path(nfd) == "配置.txt"


@pytest.mark.skipif(os.name != "nt", reason="MSYS 形态仅在 Windows 出现")
class TestResolve:
    def test_相对路径基于root(self, tmp_path: Path):
        assert resolve_path(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()

    def test_绝对路径忽略root(self, tmp_path: Path):
        outside = tmp_path.parent / "outside.txt"
        assert resolve_path(tmp_path, str(outside)) == outside.resolve()

    def test_MSYS绝对路径解析到真实文件(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        assert (
            resolve_path(tmp_path, _msys(tmp_path / "a.txt"))
            == (tmp_path / "a.txt").resolve()
        )

    def test_路径不存在也能解析(self, tmp_path: Path):
        assert (
            resolve_path(tmp_path, "new/file.txt")
            == (tmp_path / "new/file.txt").resolve()
        )

    def test_非法输入返回None(self, tmp_path: Path):
        assert resolve_path(tmp_path, "") is None
        assert resolve_path(tmp_path, "a\x00b") is None
        assert resolve_path(tmp_path, "x" * 5000) is None
