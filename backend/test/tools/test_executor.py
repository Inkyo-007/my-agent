"""tools/executor.py 的离线单元测试

异步测试由 pyproject.toml 的 asyncio_mode = "auto" 自动识别，无需标记。
"""

import asyncio
from typing import Any, Dict, List

from fakes import CollectingBus

from src.core import (
    EventBus,
    EventType,
    HookVerdict,
    Tool,
    ToolCall,
    ToolDefinition,
    ToolInputSchema,
    ToolResult,
)
from src.tools import ToolExecutor, ToolRegistry
from src.tools.executor import ExecutionHook


class StubTool(Tool):
    """可控制的测试工具：delay 模拟耗时，error 模拟内部异常"""

    def __init__(
        self,
        name: str = "get_weather",
        delay: float = 0.0,
        error: Exception | None = None,
        timeout_seconds: float = 30,
    ):
        self.definition = ToolDefinition(
            name=name,
            description="测试工具",
            input_schema=ToolInputSchema(
                properties={"city": {"type": "string"}},
                required=["city"],
            ),
            timeout_seconds=timeout_seconds,
        )
        self.called = False
        self._delay = delay
        self._error = error

    async def call(self, params: Dict[str, Any]) -> ToolResult:
        self.called = True
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return ToolResult(success=True, content="晴", execution_time=0.0)


def make_tool(
    name: str = "get_weather",
    delay: float = 0.0,
    error: Exception | None = None,
    timeout_seconds: float = 30,
) -> StubTool:
    return StubTool(name, delay, error, timeout_seconds)


def make_executor(
    tool: Tool,
    hooks: List[ExecutionHook] | None = None,
    bus: EventBus | None = None,
) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, hooks=hooks, event_bus=bus)


def weather_call() -> ToolCall:
    return ToolCall(id="call_1", name="get_weather", input={"city": "北京"})


class Test正常执行:
    async def test_成功执行返回工具结果(self):
        result = await make_executor(make_tool()).execute(weather_call())
        assert result.success is True
        assert result.content == "晴"

    async def test_成功时事件序列为START加END(self):
        bus = CollectingBus()
        await make_executor(make_tool(), bus=bus).execute(weather_call())
        assert [e.type for e in bus.events] == [
            EventType.TOOL_EXECUTION_START,
            EventType.TOOL_EXECUTION_END,
        ]
        assert all(e.source == "tool_executor" for e in bus.events)
        assert bus.events[0].metadata["tool_name"] == "get_weather"


class Test前置拦截:
    async def test_工具不存在(self):
        result = await make_executor(make_tool()).execute(
            ToolCall(id="c", name="get_weathe", input={})
        )
        assert result.success is False
        assert result.error_type == "tool_not_found"
        assert "您是想要调用 'get_weather' 吗？" in result.content

    async def test_参数非法时工具不被调用(self):
        tool = make_tool()
        result = await make_executor(tool).execute(
            ToolCall(id="c", name="get_weather", input={})  # 缺必填 city
        )
        assert result.error_type == "invalid_input"
        assert "缺少必填参数 'city'" in result.content
        assert tool.called is False

    async def test_失败时事件序列为START加FAILED(self):
        bus = CollectingBus()
        await make_executor(make_tool(), bus=bus).execute(
            ToolCall(id="c", name="get_weather", input={})
        )
        assert [e.type for e in bus.events] == [
            EventType.TOOL_EXECUTION_START,
            EventType.TOOL_EXECUTION_FAILED,
        ]
        assert bus.events[1].metadata["error_type"] == "invalid_input"


class Test钩子链:
    async def test_钩子否决时工具不被调用(self):
        tool = make_tool()

        def deny_hook(tool_call, definition):
            return HookVerdict(allowed=False, reason="当前会话为只读模式")

        result = await make_executor(tool, hooks=[deny_hook]).execute(weather_call())
        assert result.success is False
        assert result.error_type == "permission_denied"
        assert result.content == "当前会话为只读模式"  # reason 会被喂回模型
        assert tool.called is False

    async def test_空钩子链默认放行(self):
        result = await make_executor(make_tool()).execute(weather_call())
        assert result.success is True

    async def test_首个否决即短路(self):
        calls = []

        def deny_hook(tc, d):
            calls.append("deny")
            return HookVerdict(allowed=False, reason="拒绝")

        def spy_hook(tc, d):
            calls.append("spy")
            return HookVerdict(allowed=True)

        await make_executor(make_tool(), hooks=[deny_hook, spy_hook]).execute(
            weather_call()
        )
        assert calls == ["deny"]  # 第二个钩子未被调用


class Test执行异常:
    async def test_超时(self):
        tool = make_tool(delay=0.2, timeout_seconds=0.05)
        result = await make_executor(tool).execute(weather_call())
        assert result.success is False
        assert result.error_type == "timeout"
        assert "超时" in result.content
        assert result.execution_time > 0

    async def test_超时时发射FAILED事件(self):
        bus = CollectingBus()
        tool = make_tool(delay=0.2, timeout_seconds=0.05)
        await make_executor(tool, bus=bus).execute(weather_call())
        assert [e.type for e in bus.events] == [
            EventType.TOOL_EXECUTION_START,
            EventType.TOOL_EXECUTION_FAILED,
        ]
        assert bus.events[1].metadata["error_type"] == "timeout"

    async def test_工具内部异常(self):
        tool = make_tool(error=RuntimeError("网络抖动"))
        result = await make_executor(tool).execute(weather_call())
        assert result.success is False
        assert result.error_type == "execution_error"
        assert "RuntimeError" in result.content
        assert "网络抖动" in result.content

    async def test_无事件总线时静默正常(self):
        # event_bus=None：事件是通知不是刚需，流程不受影响
        result = await make_executor(make_tool(), bus=None).execute(weather_call())
        assert result.success is True
