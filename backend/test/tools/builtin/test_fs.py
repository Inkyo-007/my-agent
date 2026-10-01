"""tools/builtin/fs.py 的离线单元测试

用 tmp_path 隔离真实文件系统，重点覆盖：路径围栏越界、截断分页、
各类失败语义（失败即结果，不抛异常）。
"""

from src.tools import ToolRegistry
from src.tools.builtin import ReadFileTool, WriteFileTool


class TestReadFile:
    async def test_读取文件带行号(self, tmp_path):
        (tmp_path / "a.txt").write_text("第一行\n第二行\n第三行", encoding="utf-8")
        result = await ReadFileTool(tmp_path).call({"path": "a.txt"})
        assert result.success is True
        assert "1 | 第一行" in result.content
        assert "3 | 第三行" in result.content

    async def test_offset与limit分页(self, tmp_path):
        (tmp_path / "a.txt").write_text("一\n二\n三\n四\n五", encoding="utf-8")
        tool = ReadFileTool(tmp_path)
        result = await tool.call({"path": "a.txt", "offset": 2, "limit": 2})
        assert result.success is True
        assert "2 | 二" in result.content
        assert "3 | 三" in result.content
        assert "一" not in result.content
        # 未覆盖全文时标注已显示范围
        assert "共 5 行" in result.content
        assert "offset=4" in result.content

    async def test_超出默认行数时截断并标注(self, tmp_path):
        big = "\n".join(f"第{i}行" for i in range(1, 2101))
        (tmp_path / "big.txt").write_text(big, encoding="utf-8")
        result = await ReadFileTool(tmp_path).call({"path": "big.txt"})
        assert result.success is True
        assert "共 2100 行" in result.content
        assert "2000 | 第2000行" in result.content
        assert "第2001行" not in result.content

    async def test_文件不存在(self, tmp_path):
        result = await ReadFileTool(tmp_path).call({"path": "不存在.txt"})
        assert result.success is False
        assert "不存在" in result.content

    async def test_路径是目录(self, tmp_path):
        result = await ReadFileTool(tmp_path).call({"path": "."})
        assert result.success is False
        assert "目录" in result.content

    async def test_相对路径越界被拦截(self, tmp_path):
        result = await ReadFileTool(tmp_path).call({"path": "../outside.txt"})
        assert result.success is False
        assert "越出工作区" in result.content

    async def test_绝对路径越界被拦截(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        result = await ReadFileTool(tmp_path).call({"path": str(outside)})
        assert result.success is False
        assert "越出工作区" in result.content

    async def test_非UTF8文件(self, tmp_path):
        (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
        result = await ReadFileTool(tmp_path).call({"path": "bin.dat"})
        assert result.success is False
        assert "UTF-8" in result.content

    async def test_offset超出总行数(self, tmp_path):
        (tmp_path / "a.txt").write_text("只有一行", encoding="utf-8")
        result = await ReadFileTool(tmp_path).call({"path": "a.txt", "offset": 99})
        assert result.success is False
        assert "超出" in result.content

    async def test_非法offset(self, tmp_path):
        result = await ReadFileTool(tmp_path).call({"path": "a.txt", "offset": 0})
        assert result.success is False


class TestWriteFile:
    async def test_写入新文件并自动创建父目录(self, tmp_path):
        result = await WriteFileTool(tmp_path).call(
            {"path": "sub/dir/b.txt", "content": "你好\n世界"}
        )
        assert result.success is True
        assert (tmp_path / "sub/dir/b.txt").read_text(encoding="utf-8") == "你好\n世界"
        assert "字节" in result.content

    async def test_覆盖已有文件(self, tmp_path):
        (tmp_path / "a.txt").write_text("旧内容", encoding="utf-8")
        result = await WriteFileTool(tmp_path).call(
            {"path": "a.txt", "content": "新内容"}
        )
        assert result.success is True
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "新内容"

    async def test_越界写入被拦截(self, tmp_path):
        result = await WriteFileTool(tmp_path).call(
            {"path": "../evil.txt", "content": "x"}
        )
        assert result.success is False
        assert "越出工作区" in result.content
        assert not (tmp_path.parent / "evil.txt").exists()

    async def test_目标是目录时失败(self, tmp_path):
        (tmp_path / "adir").mkdir()
        result = await WriteFileTool(tmp_path).call({"path": "adir", "content": "x"})
        assert result.success is False
        assert "写入失败" in result.content


class Test读写往返:
    async def test_write后read内容一致(self, tmp_path):
        content = "alpha\nbeta\n"
        await WriteFileTool(tmp_path).call({"path": "rt.txt", "content": content})
        result = await ReadFileTool(tmp_path).call({"path": "rt.txt"})
        assert result.success is True
        assert "1 | alpha" in result.content
        assert "2 | beta" in result.content


class Test注册与标签:
    def test_可注册且权限标签保守诚实(self, tmp_path):
        registry = ToolRegistry()
        registry.register(ReadFileTool(tmp_path))
        registry.register(WriteFileTool(tmp_path))
        read_tool = registry.get("read_file")
        write_tool = registry.get("write_file")
        assert read_tool is not None and write_tool is not None
        assert read_tool.definition.permission_required == ["fs:read"]
        assert write_tool.definition.permission_required == ["fs:write"]
