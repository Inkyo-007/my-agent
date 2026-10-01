"""runtime/stream.py 的单元测试：StreamAccumulator 的碎片聚合

覆盖：正文/思考/签名拼接、工具调用按 index 分桶与乱序到达、
input 碎片拼接与非法 JSON 降级、STOP 的 finish_reason 与 token 统计。
"""

import json

from src.core import ProviderResponse, StreamProviderResponse, StreamType, ToolCall
from src.runtime.stream import StreamAccumulator


def start(model: str = "test-model") -> StreamProviderResponse:
    return StreamProviderResponse(
        type=StreamType.START, delta={"role": "assistant", "model": model}
    )


def text(content: str) -> StreamProviderResponse:
    return StreamProviderResponse(
        type=StreamType.TEXT_DELTA, delta={"content": content}
    )


def thinking(delta: dict) -> StreamProviderResponse:
    return StreamProviderResponse(type=StreamType.THINKING_DELTA, delta=delta)


def tool_delta(
    index: int,
    call_id: str | None = None,
    name: str | None = None,
    input: str | None = None,
) -> StreamProviderResponse:
    return StreamProviderResponse(
        type=StreamType.TOOL_CALL_DELTA,
        delta={"index": index, "id": call_id, "name": name, "input": input},
    )


def stop(finish_reason: str = "stop", token_used: int = 42) -> StreamProviderResponse:
    return StreamProviderResponse(
        type=StreamType.STOP,
        delta={"finish_reason": finish_reason},
        token_used=token_used,
    )


def assemble(events) -> ProviderResponse:
    acc = StreamAccumulator()
    for e in events:
        acc.feed(e)
    return acc.response()


class Test文本与思考:
    def test_正文拼接与STOP统计(self):
        resp = assemble([start(), text("你好"), text("，世界"), stop(token_used=42)])
        assert resp.content == "你好，世界"
        assert resp.model == "test-model"
        assert resp.finish_reason == "stop"
        assert resp.token_used == 42

    def test_思考签名与加密思考分别留存(self):
        resp = assemble(
            [
                start(),
                thinking({"thinking": "想一"}),
                thinking({"thinking": "想二"}),
                thinking({"signature": "sig_1"}),
                thinking({"redacted_thinking": "加密数据"}),
                stop(),
            ]
        )
        assert resp.thinking == "想一" + "想二"
        assert resp.thinking_signature == "sig_1"
        assert resp.redacted_thinking == "加密数据"

    def test_无思考时字段为None(self):
        resp = assemble([start(), text("答"), stop()])
        assert resp.thinking is None
        assert resp.thinking_signature is None
        assert resp.redacted_thinking is None


class Test工具调用:
    def test_碎片拼装为ToolCall(self):
        resp = assemble(
            [
                start(),
                tool_delta(0, call_id="call_1", name="get_weather"),
                tool_delta(0, input='{"city":'),
                tool_delta(0, input=' "北京"}'),
                stop(finish_reason="tool_calls"),
            ]
        )
        assert resp.tool_calls == [
            ToolCall(id="call_1", name="get_weather", input={"city": "北京"})
        ]

    def test_多工具按index升序且乱序到达不影响(self):
        resp = assemble(
            [
                start(),
                tool_delta(1, call_id="call_2", name="tool_b"),
                tool_delta(0, call_id="call_1", name="tool_a"),
                tool_delta(1, input=json.dumps({"b": 2})),
                tool_delta(0, input=json.dumps({"a": 1})),
                stop(finish_reason="tool_calls"),
            ]
        )
        assert [tc.name for tc in resp.tool_calls] == ["tool_a", "tool_b"]
        assert resp.tool_calls[0].input == {"a": 1}
        assert resp.tool_calls[1].input == {"b": 2}

    def test_无input碎片的工具调用input为空字典(self):
        resp = assemble([start(), tool_delta(0, call_id="c", name="noop"), stop()])
        assert resp.tool_calls[0].input == {}

    def test_input非法JSON时容错为空字典(self):
        resp = assemble(
            [start(), tool_delta(0, call_id="c", name="bad", input="{坏掉的"), stop()]
        )
        assert resp.tool_calls[0].input == {}


class Test边界:
    def test_空流只剩START和STOP(self):
        resp = assemble([start(), stop(finish_reason="length", token_used=7)])
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.finish_reason == "length"
        assert resp.token_used == 7

    def test_STOP缺finish_reason时为unknown(self):
        resp = assemble(
            [start(), StreamProviderResponse(type=StreamType.STOP, delta={})]
        )
        assert resp.finish_reason == "unknown"

    def test_START缺model时为空字符串(self):
        resp = assemble(
            [
                StreamProviderResponse(
                    type=StreamType.START, delta={"role": "assistant"}
                ),
                stop(),
            ]
        )
        assert resp.model == ""
