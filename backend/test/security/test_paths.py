"""security/path_validator.py 解析机制（resolve_in_root）的离线单元测试

覆盖：内/外判定、.. 穿越、绝对路径越界、非法路径（空串、超长、空字节）、
符号链接逃逸（Windows 需开发者模式或管理员权限，不可用则跳过）。
"""

import os
import unicodedata
from pathlib import Path

import pytest

from src.security import resolve_in_root


class Test内外判定:
    def test_工作区内相对路径(self, tmp_path: Path):
        result = resolve_in_root(tmp_path, "src/main.py")
        assert result is not None
        assert result.within_root is True
        assert result.path == (tmp_path / "src/main.py").resolve()

    def test_工作区内绝对路径(self, tmp_path: Path):
        result = resolve_in_root(tmp_path, str(tmp_path / "a.txt"))
        assert result is not None
        assert result.within_root is True

    def test_点分穿越判为界外(self, tmp_path: Path):
        result = resolve_in_root(tmp_path, "../outside.txt")
        assert result is not None
        assert result.within_root is False

    def test_伪装成界内的穿越(self, tmp_path: Path):
        # 先下钻再穿出：规范化后仍在界外
        result = resolve_in_root(tmp_path, "sub/../../outside.txt")
        assert result is not None
        assert result.within_root is False

    def test_界外绝对路径(self, tmp_path: Path):
        outside = Path(tmp_path).anchor + "definitely_not_inside"
        result = resolve_in_root(tmp_path, outside)
        assert result is not None
        assert result.within_root is False

    def test_路径不存在也能判定(self, tmp_path: Path):
        # resolve 非 strict：写入新文件的场景适用
        result = resolve_in_root(tmp_path, "new_dir/new_file.txt")
        assert result is not None
        assert result.within_root is True


class Test编码与形态:
    @pytest.mark.skipif(os.name != "nt", reason="反斜杠仅在 Windows 是路径分隔符")
    def test_反斜杠穿越判为界外(self, tmp_path: Path):
        # 向量 5：Windows 下 \\ 与 / 同为分隔符，resolve 同样规范化
        result = resolve_in_root(tmp_path, "..\\..\\outside.txt")
        assert result is not None
        assert result.within_root is False

    def test_NFD输入归一化后与NFC判定一致(self, tmp_path: Path):
        # 向量 4：NFD（分解形）与 NFC（合成形）须解析到同一路径，
        # 否则 macOS 等 NFD 文件系统上会出现判定与访问不一致
        nfc = "café.txt"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd
        by_nfc = resolve_in_root(tmp_path, nfc)
        by_nfd = resolve_in_root(tmp_path, nfd)
        assert by_nfc is not None and by_nfd is not None
        assert by_nfc.path == by_nfd.path
        assert by_nfd.within_root is True

    def test_相似Unicode字符不被当作分隔符(self, tmp_path: Path):
        # 向量 4 的变体：U+2044（FRACTION SLASH）不是路径分隔符，
        # NFC/NFKC 都不会把它变成 /——它只是一个怪文件名的组成部分
        result = resolve_in_root(tmp_path, "etc\u2044passwd")
        assert result is not None
        assert result.within_root is True


class Test非法路径:
    def test_空串(self, tmp_path: Path):
        assert resolve_in_root(tmp_path, "") is None

    def test_超长路径(self, tmp_path: Path):
        assert resolve_in_root(tmp_path, "a" * 5000) is None

    def test_含空字节(self, tmp_path: Path):
        assert resolve_in_root(tmp_path, "a\x00b") is None


class Test符号链接:
    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="平台不支持符号链接")
    def test_符号链接逃逸被拦截(self, tmp_path: Path):
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            pytest.skip("无创建符号链接的权限（Windows 需开发者模式）")
        result = resolve_in_root(tmp_path, "link/secret.txt")
        assert result is not None
        assert result.within_root is False
