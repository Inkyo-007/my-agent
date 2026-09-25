"""tools/registry.py 的离线单元测试"""

from typing import Any, Dict

import pytest

from src.core import Tool, ToolDefinition, ToolInputSchema, ToolResult
from src.tools import ToolRegistry


def make_tool(name: str = "get_weather", description: str = "查询天气") -> Tool:
    """构造一个合法工具（每次调用生成独立类，避免测试间相互干扰）"""

    class _Tool(Tool):
        definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=ToolInputSchema(),
        )

        async def call(self, params: Dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, content="ok", execution_time=0.0)

    return _Tool()


@pytest.fixture
def registry():
    return ToolRegistry()


class Test注册与查找:
    def test_注册成功并可按名取回同一实例(self, registry):
        tool = make_tool()
        registry.register(tool)
        assert registry.get("get_weather") is tool

    def test_list_tools返回全部已注册工具(self, registry):
        registry.register(make_tool(name="tool_a"))
        registry.register(make_tool(name="tool_b"))
        names = [t.definition.name for t in registry.list_tools()]
        assert sorted(names) == ["tool_a", "tool_b"]

    def test_get不存在的名字返回None而不抛异常(self, registry):
        # 模型幻觉工具名是常态，None 由 executor 包装为 ToolResult 喂回模型
        assert registry.get("hallucinated_tool") is None


class TestSchema查询:
    def test_存在时返回输入schema(self, registry):
        tool = make_tool()
        registry.register(tool)
        assert registry.get_schema("get_weather") is tool.definition.input_schema

    def test_不存在时返回None(self, registry):
        assert registry.get_schema("hallucinated_tool") is None


class Test注册校验:
    def test_缺少definition时拒绝注册(self, registry):
        class NoDefinition(Tool):
            async def call(self, params: Dict[str, Any]) -> ToolResult:
                return ToolResult(success=True, content="ok", execution_time=0.0)

        with pytest.raises(ValueError, match="definition"):
            registry.register(NoDefinition())

    def test_name为空时拒绝注册(self, registry):
        with pytest.raises(ValueError, match="name"):
            registry.register(make_tool(name=""))

    def test_description为空时拒绝注册(self, registry):
        with pytest.raises(ValueError, match="description"):
            registry.register(make_tool(description=""))

    def test_input_schema类型错误时拒绝注册(self, registry):
        tool = make_tool()
        # 故意赋值为 dict 以验证注册时的类型拦截
        tool.definition.input_schema = {"type": "object"}  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(ValueError, match="input_schema"):
            registry.register(tool)

    def test_同名重复注册时报错而非静默覆盖(self, registry):
        first = make_tool()
        registry.register(first)
        with pytest.raises(ValueError, match="重复注册"):
            registry.register(make_tool())
        # 原工具未被顶替
        assert registry.get("get_weather") is first
