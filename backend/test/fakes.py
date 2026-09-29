"""跨测试目录共享的 fake 工具与响应工厂

工厂函数与测试替身放在普通模块中显式导入，而不是 conftest.py：
多个同名 conftest.py 在 pytest 的导入机制下会发生模块名冲突，
且工厂本就需要显式 import，走普通模块语义更直白。

conftest.py 只保留 fixture（自动可见、无需 import），
目录专属的 fixture 放对应子目录的 conftest.py（如 models/conftest.py）。
"""

from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List

from src.core import (
    BaseProvider,
    Event,
    EventBus,
    ProviderMessage,
    ProviderResponse,
    StreamProviderResponse,
    Tool,
    ToolCall,
    ToolDefinition,
    ToolInputSchema,
    ToolResult,
)
from src.models import OpenAIProvider


class WeatherTool(Tool):
    """测试用的虚拟工具（类名不能以 Test 开头，否则会被 pytest 误收集）"""

    definition = ToolDefinition(
        name="get_weather",
        description="查询指定城市的天气",
        input_schema=ToolInputSchema(
            properties={"city": {"type": "string", "description": "城市名"}},
            required=["city"],
        ),
    )

    async def call(self, params: Dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, content="晴", execution_time=0.0)


def make_response(
    content: str | None = "ok",
    thinking: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = "stop",
    total_tokens: int = 10,
    model: str = "deepseek-chat",
):
    """伪造一个 client.chat.completions.create 的返回对象

    total_tokens 传 0 可模拟 usage 缺失（返回 None）的情况。
    """
    message = SimpleNamespace(
        content=content,
        reasoning_content=thinking,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(total_tokens=total_tokens) if total_tokens else None
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def make_tool_call(call_id="call_1", name="get_weather", arguments='{"city": "北京"}'):
    """伪造一个 tool_call 对象（arguments 为 JSON 字符串，与真实 API 一致）"""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class StubbedOpenAIProvider(OpenAIProvider):
    """OpenAIProvider 的打桩版：声明测试附加的属性，使类型检查可见

    - captured：complete()/stream() 实际组装的请求参数
    - stubbed_response：测试可替换的伪造响应
    """

    captured: Dict[str, Any]
    stubbed_response: Any


class CollectingBus(EventBus):
    """收集全部事件的测试总线（不做过滤，断言时自行筛选）"""

    def __init__(self):
        self.events: List[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)

    def subscribe(self, handler) -> None:
        pass


def make_provider_response(
    content: str = "",
    tool_calls: List[ToolCall] | None = None,
    finish_reason: str = "stop",
    token_used: int = 10,
    thinking: str | None = None,
    thinking_signature: str | None = None,
    redacted_thinking: str | None = None,
) -> ProviderResponse:
    """构造一个 ProviderResponse（runtime 测试的脚本化响应）"""
    return ProviderResponse(
        content=content,
        model="test-model",
        finish_reason=finish_reason,
        thinking=thinking,
        thinking_signature=thinking_signature,
        redacted_thinking=redacted_thinking,
        token_used=token_used,
        tool_calls=tool_calls or [],
    )


class ScriptedProvider(BaseProvider):
    """脚本化 Provider：按预设队列依次返回响应，并记录每次收到的消息

    - 队列元素为 ProviderResponse 时原样返回；为 Exception 时抛出（模拟调用失败）
    - calls：每次 complete() 收到的 ProviderMessage 列表，用于断言
      「第 N 次调用模型时喂进去的上下文长什么样」
    """

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.calls: List[List[ProviderMessage]] = []

    async def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("ScriptedProvider 的响应队列已耗尽")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> AsyncGenerator[StreamProviderResponse, None]:
        raise NotImplementedError("ScriptedProvider 不支持流式")
