"""OpenAIProvider 非流式模块的离线单元测试

特点：不联网、不消耗 API 额度、结果确定，每次改完代码都应运行。
打桩点在 SDK 边界（client.chat.completions.create），只验证自己的
「请求组装」和「响应解析」两层逻辑，不测 SDK 与网络。
"""

from types import SimpleNamespace

from fakes import StubbedOpenAIProvider, WeatherTool, make_response, make_tool_call

from src.core import ProviderMessage, StreamType, ToolCall
from src.models import (
    ModelConfig,
    ModelProviderType,
)


def make_stubbed_provider(**config_overrides) -> StubbedOpenAIProvider:
    """构造一个带自定义配置、只捕获请求参数的打桩 Provider"""
    p = StubbedOpenAIProvider(
        ModelConfig(
            provider=ModelProviderType.OPENAI,
            model_id="test-model",
            api_key="fake-key",
            **config_overrides,
        )
    )
    p.captured = {}

    async def fake_create(**kw):
        p.captured.update(kw)
        return make_response()

    p.client.chat.completions.create = fake_create
    return p


async def ask(p, content="hi"):
    return await p.complete([ProviderMessage(role="user", content=content)])


class Test请求组装:
    """验证 complete() 发往 API 的参数"""

    async def test_默认发送max_tokens(self, provider):
        await ask(provider)
        assert provider.captured["max_tokens"] == 4096
        assert "max_completion_tokens" not in provider.captured
        assert "reasoning_effort" not in provider.captured
        assert "extra_body" not in provider.captured

    async def test_o系列使用max_completion_tokens(self):
        p = make_stubbed_provider(
            max_tokens=8000,
            max_tokens_param="max_completion_tokens",
            reasoning_effort="high",
        )
        await ask(p)
        assert p.captured["max_completion_tokens"] == 8000
        assert "max_tokens" not in p.captured
        assert p.captured["reasoning_effort"] == "high"

    async def test_extra_body原样透传(self):
        thinking_on = {"thinking": {"type": "enabled"}}
        p = make_stubbed_provider(extra_body=thinking_on)
        await ask(p)
        assert p.captured["extra_body"] == thinking_on

    async def test_历史消息回传reasoning_content(self, provider):
        msgs = [
            ProviderMessage(role="user", content="北京天气？"),
            ProviderMessage(role="assistant", content="晴", thinking="需要查天气工具"),
        ]
        await provider.complete(msgs)
        sent = provider.captured["messages"]
        assert sent[1]["reasoning_content"] == "需要查天气工具"
        assert "reasoning_content" not in sent[0]  # 无思考的消息不带该字段

    async def test_assistant消息回传tool_calls(self, provider):
        msgs = [
            ProviderMessage(role="user", content="北京天气？"),
            ProviderMessage(
                role="assistant",
                content="",
                thinking="需要查天气工具",
                tool_calls=[
                    ToolCall(id="call_1", name="get_weather", input={"city": "北京"})
                ],
            ),
        ]
        await provider.complete(msgs)
        sent = provider.captured["messages"][1]
        assert (
            sent["tool_calls"]
            == [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "北京"}',  # arguments 重新序列化为 JSON 字符串
                    },
                }
            ]
        )

    async def test_tool角色消息回传tool_call_id(self, provider):
        msgs = [
            ProviderMessage(role="user", content="北京天气？"),
            ProviderMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="get_weather", input={"city": "北京"})
                ],
            ),
            ProviderMessage(role="tool", content="晴", tool_call_id="call_1"),
        ]
        await provider.complete(msgs)
        tool_msg = provider.captured["messages"][2]
        assert tool_msg == {"role": "tool", "content": "晴", "tool_call_id": "call_1"}
        assert "tool_calls" not in tool_msg
        assert "reasoning_content" not in tool_msg

    async def test_tools转换为OpenAI格式(self, provider):
        await provider.complete(
            [ProviderMessage(role="user", content="hi")], tools=[WeatherTool()]
        )
        tools = provider.captured["tools"]
        assert tools == [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询指定城市的天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名"}
                        },
                        "required": ["city"],
                    },
                },
            }
        ]

    async def test_stream请求开启usage统计(self, provider):
        async def fake_create(**kw):
            provider.captured.update(kw)
            return FakeStream([])

        provider.client.chat.completions.create = fake_create
        async for _ in provider.stream([ProviderMessage(role="user", content="hi")]):
            pass
        assert provider.captured["stream"] is True
        assert provider.captured["stream_options"] == {"include_usage": True}


class Test响应解析:
    """验证 complete() 对 API 响应的处理"""

    async def test_基本字段解析(self, provider):
        provider.stubbed_response = make_response(
            content="答", thinking="想", total_tokens=42
        )
        resp = await ask(provider)
        assert resp.content == "答"
        assert resp.thinking == "想"
        assert resp.token_used == 42
        assert resp.finish_reason == "stop"

    async def test_content为None时转为空字符串(self, provider):
        provider.stubbed_response = make_response(content=None)
        resp = await ask(provider)
        assert resp.content == ""

    async def test_tool_calls解析为ToolCall对象(self, provider):
        provider.stubbed_response = make_response(
            content=None,
            tool_calls=[make_tool_call()],
            finish_reason="tool_calls",
        )
        resp = await ask(provider)
        assert resp.tool_calls == [
            ToolCall(id="call_1", name="get_weather", input={"city": "北京"})
        ]

    async def test_arguments非法JSON时容错为空字典(self, provider):
        provider.stubbed_response = make_response(
            content=None,
            tool_calls=[make_tool_call(arguments="{坏掉的json")],
        )
        resp = await ask(provider)
        assert resp.tool_calls[0].input == {}

    async def test_usage缺失时token_used为0(self, provider):
        provider.stubbed_response = make_response(total_tokens=0)  # usage=None
        resp = await ask(provider)
        assert resp.token_used == 0

    async def test_无思考内容时thinking为None(self, provider):
        provider.stubbed_response = make_response(content="答", thinking=None)
        resp = await ask(provider)
        assert resp.thinking is None


# ---------- 流式输出 ----------


class FakeStream:
    """伪造的流对象：支持 async with 协议与异步迭代（与 SDK 的 AsyncStream 行为一致）"""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


def make_chunk(
    role=None,
    content=None,
    thinking=None,
    tool_calls=None,
    finish_reason=None,
    total_tokens=None,
    empty_choices=False,
):
    """伪造一个流式 chunk

    empty_choices=True 模拟官方 OpenAI 的独立 usage chunk（choices 为空数组）。
    """
    usage = SimpleNamespace(total_tokens=total_tokens) if total_tokens else None
    if empty_choices:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(
        role=role,
        content=content,
        reasoning_content=thinking,  # 兼容服务扩展字段
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def make_tool_delta(index=0, call_id=None, name=None, arguments=None):
    """伪造流式 tool_calls delta：id/name 仅在首个碎片出现，与真实 API 一致"""
    function = None
    if name is not None or arguments is not None:
        function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, type="function", function=function)


async def stream_events(provider, chunks):
    async def fake_create(**kw):
        return FakeStream(chunks)

    provider.client.chat.completions.create = fake_create
    return [e async for e in provider.stream([ProviderMessage(role="user", content="hi")])]


class Test流式输出:
    async def test_完整事件序列(self, provider):
        chunks = [
            make_chunk(role="assistant"),
            make_chunk(thinking="想想"),
            make_chunk(content="你"),
            make_chunk(content="好"),
            make_chunk(
                finish_reason="stop", total_tokens=42
            ),  # DeepSeek 形态：usage 挂在 finish chunk
        ]
        events = await stream_events(provider, chunks)
        types = [e.type for e in events]
        assert types == [
            StreamType.START,
            StreamType.THINKING_DELTA,
            StreamType.TEXT_DELTA,
            StreamType.TEXT_DELTA,
            StreamType.STOP,
        ]
        assert events[-1].delta == {"finish_reason": "stop"}
        assert events[-1].token_used == 42

    async def test_官方形态usage在finish之后到达(self, provider):
        """官方 OpenAI：usage 是 finish chunk 之后的独立空 choices chunk，
        STOP 必须等流结束再发，保证 token_used 完整"""
        chunks = [
            make_chunk(role="assistant"),
            make_chunk(content="你好"),
            make_chunk(finish_reason="stop"),  # finish chunk 无 usage
            make_chunk(empty_choices=True, total_tokens=42),  # usage chunk 随后才到
        ]
        events = await stream_events(provider, chunks)
        assert events[-1].type == StreamType.STOP
        assert events[-1].delta == {"finish_reason": "stop"}
        assert events[-1].token_used == 42  # 不是 0！

    async def test_工具调用碎片透传(self, provider):
        chunks = [
            make_chunk(role="assistant"),
            make_chunk(
                tool_calls=[
                    make_tool_delta(call_id="call_1", name="get_weather", arguments="")
                ]
            ),
            make_chunk(tool_calls=[make_tool_delta(arguments='{"city": "北')]),
            make_chunk(tool_calls=[make_tool_delta(arguments='京"}')]),
            make_chunk(finish_reason="tool_calls", total_tokens=10),
        ]
        events = await stream_events(provider, chunks)
        tc_events = [e for e in events if e.type == StreamType.TOOL_CALL_DELTA]
        # 首个碎片携带 id/name，后续碎片只携带 input 片段
        assert tc_events[0].delta == {
            "index": 0,
            "id": "call_1",
            "name": "get_weather",
            "input": "",
        }
        assert tc_events[1].delta == {
            "index": 0,
            "id": None,
            "name": None,
            "input": '{"city": "北',
        }
        assert tc_events[2].delta["input"] == '京"}'
        assert events[-1].delta == {"finish_reason": "tool_calls"}

    async def test_无finish_reason时STOP标记为unknown(self, provider):
        """流异常中断（finish_reason 从未出现）时，STOP 仍需兜底收尾"""
        chunks = [make_chunk(role="assistant"), make_chunk(content="你好")]
        events = await stream_events(provider, chunks)
        assert events[-1].type == StreamType.STOP
        assert events[-1].delta == {"finish_reason": "unknown"}
        assert events[-1].token_used == 0
