"""security/path_validator.py 的离线单元测试

覆盖：内/外/非法分类、touches_outside 的 fail-closed 约定、
format 标注提取、参数缺失/非字符串时的跳过。
"""

from pathlib import Path

from src.core import ToolCall, ToolDefinition, ToolInputSchema
from src.security import PathLocation, validate_call_paths


def _definition(path_params: list[str]) -> ToolDefinition:
    """构造一个工具定义：path_params 中的参数以 format 标注为路径"""
    return ToolDefinition(
        name="demo",
        description="演示工具",
        input_schema=ToolInputSchema(
            properties={
                name: {"type": "string", "format": "path"} for name in path_params
            }
        ),
    )


def _call(params: dict) -> ToolCall:
    return ToolCall(id="c1", name="demo", input=params)


class Test路径分类:
    def test_工作区内(self, tmp_path: Path):
        report = validate_call_paths(
            _call({"path": "src/a.py"}), _definition(["path"]), tmp_path
        )
        assert report.locations == {"path": PathLocation.INSIDE}
        assert report.touches_outside is False

    def test_工作区外(self, tmp_path: Path):
        report = validate_call_paths(
            _call({"path": "../secret.txt"}), _definition(["path"]), tmp_path
        )
        assert report.locations == {"path": PathLocation.OUTSIDE}
        assert report.touches_outside is True

    def test_非法路径按界外处理(self, tmp_path: Path):
        report = validate_call_paths(
            _call({"path": "a\x00b"}), _definition(["path"]), tmp_path
        )
        assert report.locations == {"path": PathLocation.INVALID}
        assert report.touches_outside is True  # fail-closed

    def test_多参数任一界外即触及(self, tmp_path: Path):
        report = validate_call_paths(
            _call({"src": "a.py", "dst": "../b.py"}),
            _definition(["src", "dst"]),
            tmp_path,
        )
        assert report.locations == {
            "src": PathLocation.INSIDE,
            "dst": PathLocation.OUTSIDE,
        }
        assert report.touches_outside is True


class Test边界情况:
    def test_未标注路径参数时为空报告(self, tmp_path: Path):
        definition = ToolDefinition(
            name="demo",
            description="演示工具",
            input_schema=ToolInputSchema(
                properties={"path": {"type": "string"}}  # 无 format 标注
            ),
        )
        report = validate_call_paths(_call({"path": "../x"}), definition, tmp_path)
        assert report.locations == {}
        assert report.touches_outside is False

    def test_参数缺失时跳过(self, tmp_path: Path):
        report = validate_call_paths(_call({}), _definition(["path"]), tmp_path)
        assert report.locations == {}

    def test_非字符串参数跳过(self, tmp_path: Path):
        # 参数类型合法性由 tools 层 schema 校验兜底，本层只分析字符串路径
        report = validate_call_paths(
            _call({"path": 123}), _definition(["path"]), tmp_path
        )
        assert report.locations == {}
