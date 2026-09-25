"""tools/validator.py 的离线单元测试"""

from fakes import WeatherTool

from src.core import ToolCall, ToolInputSchema
from src.tools import ToolRegistry
from src.tools.validator import check_tool_exists, validate_tool_input


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(WeatherTool())  # name="get_weather"，required=["city"]
    return registry


class Test工具存在性检查:
    def test_工具存在时返回None(self):
        result = check_tool_exists(
            ToolCall(id="1", name="get_weather"), make_registry()
        )
        assert result is None

    def test_工具不存在时文案含可用工具清单(self):
        result = check_tool_exists(ToolCall(id="1", name="xyzzy"), make_registry())
        assert result is not None
        assert "未注册" in result
        assert "get_weather" in result  # 可用清单中列出

    def test_相近名字时给出模糊建议(self):
        result = check_tool_exists(ToolCall(id="1", name="get_weathe"), make_registry())
        assert result is not None
        assert "您是想要调用 'get_weather' 吗？" in result

    def test_相差太远的名字不给建议(self):
        result = check_tool_exists(
            ToolCall(id="1", name="delete_database"), make_registry()
        )
        assert result is not None
        assert "您是想要" not in result


class Test参数校验:
    SCHEMA = ToolInputSchema(
        properties={"city": {"type": "string", "description": "城市名"}},
        required=["city"],
    )

    def test_参数合法时通过(self):
        errors = validate_tool_input(
            ToolCall(id="1", name="get_weather", input={"city": "北京"}), self.SCHEMA
        )
        assert errors == []

    def test_缺少必填参数时报出字段名(self):
        errors = validate_tool_input(
            ToolCall(id="1", name="get_weather", input={}), self.SCHEMA
        )
        assert len(errors) == 1
        assert "缺少必填参数 'city'" in errors[0]

    def test_参数类型错误时报错(self):
        errors = validate_tool_input(
            ToolCall(id="1", name="get_weather", input={"city": 123}), self.SCHEMA
        )
        assert len(errors) == 1
        assert "city" in errors[0]
        assert "类型错误" in errors[0]

    def test_无required时空input通过(self):
        errors = validate_tool_input(
            ToolCall(id="1", name="noop", input={}), ToolInputSchema()
        )
        assert errors == []

    def test_多传的参数被允许(self):
        # 与两家 API 行为一致：schema 未声明的额外参数不拦截
        errors = validate_tool_input(
            ToolCall(id="1", name="get_weather", input={"city": "北京", "extra": 1}),
            self.SCHEMA,
        )
        assert errors == []
