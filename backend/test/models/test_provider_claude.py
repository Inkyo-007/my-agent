"""ClaudeProvider 的离线单元测试

打桩点在 SDK 边界（client.messages.create），只验证自己的
「请求组装」和「响应解析」两层逻辑，不测 SDK 与网络。
"""

from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fakes import WeatherTool

from src.core import ProviderMessage, StreamType, ToolCall
from src.models import (
    ClaudeProvider,
    ModelConfig,
    ModelProviderType,
)

# ---------- fake 对象工厂 ----------


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def thinking_block(thinking, signature="sig_xxx"):
    return SimpleNamespace(type="thinking", thinking=thinking, signature=signature)


def redacted_block(data="encrypted-data"):
    return SimpleNamespace(type="redacted_thinking", data=data)


def tool_use_block(call_id="toolu_1", name="get_weather", input=None):
    return SimpleNamespace(
        type="tool_use",
        id=call_id,
        name=name,
        input=input if input is not None else {"city": "北京"},
    )


def make_claude_response(
    blocks, stop_reason="end_turn", input_tokens=10, output_tokens=20, with_usage=True
):
    """伪造一个 client.messages.create 的返回对象

    with_usage=False 可模拟 usage 缺失（返回 None）的情况。
    """
    usage = (
        SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        if with_usage
        else None
    )
    return SimpleNamespace(
        content=blocks,
        model="claude-test",
        stop_reason=stop_reason,
        usage=usage,
    )


class FakeStream:
    """伪造的流对象：支持 async with 协议与异步迭代（与 SDK 的 AsyncStream 行为一致）"""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


def ev_message_start(input_tokens=10, model="claude-test"):
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            model=model,
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=1),
        ),
    )


def ev_block_start(index, block):
    return SimpleNamespace(type="content_block_start", index=index, content_block=block)


def ev_delta(index, delta):
    return SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def ev_message_delta(stop_reason="end_turn", output_tokens=30):
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason, stop_sequence=None),
        usage=SimpleNamespace(output_tokens=output_tokens),
    )


# ---------- fixture ----------


class StubbedClaudeProvider(ClaudeProvider):
    """ClaudeProvider 的打桩版：声明测试附加的属性，使类型检查可见

    - captured：complete()/stream() 实际组装的请求参数
    - stubbed_response：测试可替换的伪造响应
    """

    captured: Dict[str, Any]
    stubbed_response: Any


def make_stubbed_provider(**config_overrides) -> StubbedClaudeProvider:
    """构造一个带自定义配置、只捕获请求参数的打桩 Provider"""
    p = StubbedClaudeProvider(
        ModelConfig(
            provider=ModelProviderType.CLAUDE,
            model_id="claude-test",
            api_key="fake-key",
            **config_overrides,
        )
    )
    p.captured = {}

    async def fake_create(**kw):
        p.captured.update(kw)
        return make_claude_response([text_block("ok")])

    p.client.messages.create = fake_create
    return p


@pytest.fixture
def provider():
    """打桩后的 ClaudeProvider：messages.create 被替换为参数捕获器"""
    p = StubbedClaudeProvider(
        ModelConfig(
            provider=ModelProviderType.CLAUDE,
            model_id="claude-test",
            api_key="fake-key",
        )
    )
    p.captured = {}
    p.stubbed_response = make_claude_response([text_block("你好")])

    async def fake_create(**kwargs):
        p.captured = kwargs
        return p.stubbed_response

    p.client.messages.create = fake_create
    return p


async def ask(p, content="hi"):
    return await p.complete([ProviderMessage(role="user", content=content)])


# ---------- 请求组装 ----------


class Test请求组装:
    async def test_max_tokens必填传入(self, provider):
        await ask(provider)
        assert provider.captured["max_tokens"] == 4096
        assert provider.captured["model"] == "claude-test"

    async def test_system消息剥离为顶层参数(self, provider):
        msgs = [
            ProviderMessage(role="system", content="你是一个助手"),
            ProviderMessage(role="user", content="hi"),
        ]
        await provider.complete(msgs)
        assert provider.captured["system"] == "你是一个助手"
        # messages 中不应再出现 system 角色
        assert all(m["role"] != "system" for m in provider.captured["messages"])

    async def test_多条system消息合并为顶层参数(self, provider):
        msgs = [
            ProviderMessage(role="system", content="第一部分"),
            ProviderMessage(role="system", content="第二部分"),
            ProviderMessage(role="user", content="hi"),
        ]
        await provider.complete(msgs)
        assert provider.captured["system"] == "第一部分\n\n第二部分"

    async def test_extra_body原样透传(self, provider):
        provider.config.extra_body = {"some_vendor_flag": True}
        await ask(provider)
        assert provider.captured["extra_body"] == {"some_vendor_flag": True}

    async def test_开启扩展思考(self):
        p = make_stubbed_provider(max_tokens=8000, thinking_budget=5000)
        await ask(p)
        assert p.captured["thinking"] == {"type": "enabled", "budget_tokens": 5000}

    async def test_thinking预算不小于max_tokens时抛错(self):
        p = make_stubbed_provider(max_tokens=1000, thinking_budget=5000)
        with pytest.raises(ValueError):
            await ask(p)

    async def test_thinking预算小于1024时抛错(self):
        p = make_stubbed_provider(max_tokens=8000, thinking_budget=512)
        with pytest.raises(ValueError, match="1024"):
            await ask(p)

    async def test_reasoning_effort触发adaptive思考(self):
        p = make_stubbed_provider(reasoning_effort="high")
        await ask(p)
        # 显式声明 adaptive，不依赖 API 默认行为
        assert p.captured["thinking"] == {"type": "adaptive"}
        assert p.captured["output_config"] == {"effort": "high"}

    async def test_thinking预算优先于adaptive(self):
        # 同时设置 budget 和 effort 时，走手动预算模式，不发 adaptive
        p = make_stubbed_provider(
            max_tokens=8000,
            thinking_budget=5000,
            reasoning_effort="high",
        )
        await ask(p)
        assert p.captured["thinking"] == {"type": "enabled", "budget_tokens": 5000}
        assert p.captured["output_config"] == {"effort": "high"}

    async def test_默认不开启思考(self, provider):
        await ask(provider)
        assert "thinking" not in provider.captured
        assert "output_config" not in provider.captured

    async def test_tools转换为Anthropic格式(self, provider):
        await provider.complete(
            [ProviderMessage(role="user", content="hi")], tools=[WeatherTool()]
        )
        tools = provider.captured["tools"]
        # 扁平结构，无 "type": "function" 包装
        assert tools == [
            {
                "name": "get_weather",
                "description": "查询指定城市的天气",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "城市名"}},
                    "required": ["city"],
                },
            }
        ]


# ---------- 消息序列化 ----------


class Test消息序列化:
    async def test_assistant消息重建为block列表(self, provider):
        msgs = [
            ProviderMessage(role="user", content="北京天气？"),
            ProviderMessage(
                role="assistant",
                content="我帮你查",
                thinking="先想想",
                thinking_signature="sig_abc",
                tool_calls=[
                    ToolCall(id="toolu_1", name="get_weather", input={"city": "北京"})
                ],
            ),
        ]
        await provider.complete(msgs)
        blocks = provider.captured["messages"][1]["content"]
        assert blocks == [
            {"type": "thinking", "thinking": "先想想", "signature": "sig_abc"},
            {"type": "text", "text": "我帮你查"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {"city": "北京"},
            },
        ]

    async def test_redacted_thinking原样回传(self, provider):
        msgs = [
            ProviderMessage(role="user", content="hi"),
            ProviderMessage(
                role="assistant", content="答", redacted_thinking="encrypted-data"
            ),
        ]
        await provider.complete(msgs)
        blocks = provider.captured["messages"][1]["content"]
        assert blocks[0] == {"type": "redacted_thinking", "data": "encrypted-data"}
        assert blocks[1] == {"type": "text", "text": "答"}

    async def test_无签名的thinking不回传(self, provider):
        """Anthropic 对无签名的 thinking block 报 400：有思考但无签名时跳过该块"""
        msgs = [
            ProviderMessage(role="user", content="hi"),
            ProviderMessage(role="assistant", content="答", thinking="无签名的思考"),
        ]
        await provider.complete(msgs)
        blocks = provider.captured["messages"][1]["content"]
        assert blocks == [{"type": "text", "text": "答"}]

    async def test_tool角色消息转为user消息的tool_result(self, provider):
        msgs = [
            ProviderMessage(role="user", content="北京天气？"),
            ProviderMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="toolu_1", name="get_weather", input={"city": "北京"})
                ],
            ),
            ProviderMessage(role="tool", content="晴", tool_call_id="toolu_1"),
        ]
        await provider.complete(msgs)
        sent = provider.captured["messages"]
        assert sent[2] == {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "晴"}
            ],
        }

    async def test_连续tool消息合并为一条user消息(self, provider):
        msgs = [
            ProviderMessage(role="user", content="天气？"),
            ProviderMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="toolu_1", name="get_weather", input={"city": "北京"}),
                    ToolCall(id="toolu_2", name="get_weather", input={"city": "上海"}),
                ],
            ),
            ProviderMessage(role="tool", content="晴", tool_call_id="toolu_1"),
            ProviderMessage(role="tool", content="多云", tool_call_id="toolu_2"),
        ]
        await provider.complete(msgs)
        sent = provider.captured["messages"]
        assert len(sent) == 3  # 两条 tool 合并后总消息数不变多
        assert sent[2]["role"] == "user"
        assert sent[2]["content"] == [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "晴"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "多云"},
        ]


# ---------- 响应解析 ----------


class Test响应解析:
    async def test_多个text块拼接(self, provider):
        provider.stubbed_response = make_claude_response(
            [text_block("你好"), text_block("世界")]
        )
        resp = await ask(provider)
        assert resp.content == "你好世界"

    async def test_thinking与signature提取(self, provider):
        provider.stubbed_response = make_claude_response(
            [thinking_block("想想", "sig_1"), text_block("答")]
        )
        resp = await ask(provider)
        assert resp.thinking == "想想"
        assert resp.thinking_signature == "sig_1"

    async def test_redacted_thinking提取(self, provider):
        provider.stubbed_response = make_claude_response(
            [redacted_block("enc-123"), text_block("答")]
        )
        resp = await ask(provider)
        assert resp.redacted_thinking == "enc-123"
        assert resp.thinking is None  # 加密思考没有明文

    async def test_tool_use解析为ToolCall(self, provider):
        provider.stubbed_response = make_claude_response(
            [text_block("查一下"), tool_use_block()], stop_reason="tool_use"
        )
        resp = await ask(provider)
        assert resp.tool_calls == [
            ToolCall(id="toolu_1", name="get_weather", input={"city": "北京"})
        ]
        assert resp.finish_reason == "tool_use"

    async def test_token_used为input与output之和(self, provider):
        provider.stubbed_response = make_claude_response(
            [text_block("答")], input_tokens=100, output_tokens=50
        )
        resp = await ask(provider)
        assert resp.token_used == 150

    async def test_usage缺失时token_used为0(self, provider):
        provider.stubbed_response = make_claude_response(
            [text_block("答")], with_usage=False
        )
        resp = await ask(provider)
        assert resp.token_used == 0


# ---------- 流式输出 ----------


class Test流式输出:
    def _stream_provider(self, events):
        p = ClaudeProvider(
            ModelConfig(
                provider=ModelProviderType.CLAUDE,
                model_id="claude-test",
                api_key="fake-key",
            )
        )

        async def fake_create(**kw):
            return FakeStream(events)

        p.client.messages.create = fake_create
        return p

    async def _collect(self, p):
        return [e async for e in p.stream([ProviderMessage(role="user", content="hi")])]

    async def test_思考与正文的完整事件序列(self):
        events = [
            ev_message_start(input_tokens=10),
            ev_block_start(
                0, SimpleNamespace(type="thinking", thinking="", signature="")
            ),
            ev_delta(0, SimpleNamespace(type="thinking_delta", thinking="想想")),
            ev_delta(0, SimpleNamespace(type="signature_delta", signature="sig_1")),
            SimpleNamespace(type="content_block_stop", index=0),
            ev_block_start(1, SimpleNamespace(type="text", text="")),
            ev_delta(1, SimpleNamespace(type="text_delta", text="你好")),
            SimpleNamespace(type="content_block_stop", index=1),
            ev_message_delta(stop_reason="end_turn", output_tokens=30),
            SimpleNamespace(type="message_stop"),
        ]
        p = self._stream_provider(events)
        stream_events = await self._collect(p)

        types = [e.type for e in stream_events]
        assert types == [
            StreamType.START,
            StreamType.THINKING_DELTA,  # 思考内容
            StreamType.THINKING_DELTA,  # 签名
            StreamType.TEXT_DELTA,
            StreamType.STOP,
        ]
        assert stream_events[0].delta == {"role": "assistant", "model": "claude-test"}
        assert stream_events[1].delta == {"thinking": "想想"}
        assert stream_events[2].delta == {"signature": "sig_1"}
        assert stream_events[3].delta == {"content": "你好"}
        assert stream_events[4].delta == {"finish_reason": "end_turn"}
        assert stream_events[4].token_used == 40  # 10 input + 30 output

    async def test_工具调用事件(self):
        events = [
            ev_message_start(input_tokens=10),
            ev_block_start(
                0,
                SimpleNamespace(
                    type="tool_use", id="toolu_1", name="get_weather", input={}
                ),
            ),
            ev_delta(
                0, SimpleNamespace(type="input_json_delta", partial_json='{"city":')
            ),
            ev_delta(
                0, SimpleNamespace(type="input_json_delta", partial_json=' "北京"}')
            ),
            SimpleNamespace(type="content_block_stop", index=0),
            ev_message_delta(stop_reason="tool_use", output_tokens=20),
        ]
        p = self._stream_provider(events)
        stream_events = await self._collect(p)

        tc_events = [e for e in stream_events if e.type == StreamType.TOOL_CALL_DELTA]
        # start 事件携带 id/name，delta 事件携带 input 碎片（partial_json）
        assert tc_events[0].delta == {
            "index": 0,
            "id": "toolu_1",
            "name": "get_weather",
            "input": None,
        }
        assert tc_events[1].delta == {
            "index": 0,
            "id": None,
            "name": None,
            "input": '{"city":',
        }
        assert tc_events[2].delta["input"] == ' "北京"}'
        assert stream_events[-1].delta == {"finish_reason": "tool_use"}

    async def test_redacted_thinking在start事件一次性给出(self):
        events = [
            ev_message_start(input_tokens=10),
            ev_block_start(
                0, SimpleNamespace(type="redacted_thinking", data="enc-123")
            ),
            SimpleNamespace(type="content_block_stop", index=0),
            ev_message_delta(),
        ]
        p = self._stream_provider(events)
        stream_events = await self._collect(p)

        thinking_events = [
            e for e in stream_events if e.type == StreamType.THINKING_DELTA
        ]
        assert len(thinking_events) == 1
        assert thinking_events[0].delta == {"redacted_thinking": "enc-123"}

    async def test_message_delta无usage时输出token计0(self):
        """usage 缺失时 output_tokens 兜底为 0，STOP 只携带 input_tokens"""
        events = [
            ev_message_start(input_tokens=10),
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="end_turn", stop_sequence=None),
                usage=None,
            ),
        ]
        p = self._stream_provider(events)
        stream_events = await self._collect(p)
        assert stream_events[-1].type == StreamType.STOP
        assert stream_events[-1].token_used == 10
