"""runtime/convert.py 的离线单元测试

覆盖：响应/工具结果 → 历史消息的 metadata 约定、历史 → 协议消息的无损重建、
序列化往返后再转换的一致性。
"""

import json

from fakes import make_provider_response

from src.core import (
    Message,
    MessageRole,
    MessageType,
    ToolCall,
    ToolCallMessage,
    ToolResult,
    ToolResultMessage,
)
from src.runtime import (
    message_from_response,
    message_from_tool_result,
    to_provider_messages,
)


class Test响应转消息:
    def test_纯文本响应(self):
        msg = message_from_response(make_provider_response(content="你好"))
        assert type(msg) is Message
        assert msg.role == MessageRole.ASSISTANT
        assert msg.type == MessageType.TEXT
        assert msg.content == "你好"
        assert msg.metadata == {}

    def test_思考与签名进入metadata(self):
        msg = message_from_response(
            make_provider_response(
                content="答", thinking="想", thinking_signature="sig_1"
            )
        )
        assert msg.thinking == "想"
        assert msg.metadata["thinking_signature"] == "sig_1"

    def test_加密思考数据进入metadata(self):
        msg = message_from_response(
            make_provider_response(content="答", redacted_thinking="enc-1")
        )
        assert msg.metadata["redacted_thinking"] == "enc-1"

    def test_工具调用转为ToolCallMessage且metadata可JSON序列化(self):
        msg = message_from_response(
            make_provider_response(
                content="查一下",
                tool_calls=[
                    ToolCall(id="c1", name="get_weather", input={"city": "北京"}),
                    ToolCall(id="c2", name="get_weather", input={"city": "上海"}),
                ],
                finish_reason="tool_calls",
            )
        )
        assert isinstance(msg, ToolCallMessage)
        assert msg.type == MessageType.TOOL_USE
        assert msg.tool_calls == [
            {"id": "c1", "name": "get_weather", "input": {"city": "北京"}},
            {"id": "c2", "name": "get_weather", "input": {"city": "上海"}},
        ]
        # metadata 必须 JSON 可序列化（持久化的前提）
        json.dumps(msg.metadata)


class Test工具结果转消息:
    def test_成功结果(self):
        tc = ToolCall(id="c1", name="get_weather", input={"city": "北京"})
        msg = message_from_tool_result(
            tc, ToolResult(success=True, content="晴", execution_time=0.1)
        )
        assert isinstance(msg, ToolResultMessage)
        assert msg.role == MessageRole.TOOL
        assert msg.content == "晴"
        assert msg.metadata["tool_call_id"] == "c1"
        assert msg.metadata["tool_name"] == "get_weather"
        assert msg.is_success is True

    def test_失败结果status取error_type(self):
        tc = ToolCall(id="c1", name="get_weather", input={})
        msg = message_from_tool_result(
            tc,
            ToolResult(
                success=False,
                content="超时",
                execution_time=30.0,
                error_type="timeout",
            ),
        )
        assert msg.status == "timeout"
        assert msg.is_success is False

    def test_失败结果无error_type时兜底(self):
        tc = ToolCall(id="c1", name="get_weather", input={})
        msg = message_from_tool_result(
            tc, ToolResult(success=False, content="失败", execution_time=0.0)
        )
        assert msg.status == "error"


class Test历史转协议消息:
    def test_各角色映射(self):
        msgs = [
            Message(role=MessageRole.SYSTEM, type=MessageType.TEXT, content="系统"),
            Message(role=MessageRole.USER, type=MessageType.TEXT, content="用户"),
            Message(role=MessageRole.ASSISTANT, type=MessageType.TEXT, content="助手"),
            ToolResultMessage(
                role=MessageRole.TOOL,
                type=MessageType.TOOL_RESULT,
                content="结果",
                metadata={"tool_call_id": "c1"},
            ),
        ]
        pms = to_provider_messages(msgs)
        assert [p.role for p in pms] == ["system", "user", "assistant", "tool"]
        assert pms[3].tool_call_id == "c1"

    def test_assistant协议字段无损重建(self):
        msg = message_from_response(
            make_provider_response(
                content="查一下",
                thinking="想",
                thinking_signature="sig_1",
                redacted_thinking="enc-1",
                tool_calls=[
                    ToolCall(id="c1", name="get_weather", input={"city": "北京"})
                ],
            )
        )
        (pm,) = to_provider_messages([msg])
        assert pm.thinking == "想"
        assert pm.thinking_signature == "sig_1"
        assert pm.redacted_thinking == "enc-1"
        assert pm.tool_calls == [
            ToolCall(id="c1", name="get_weather", input={"city": "北京"})
        ]

    def test_无签名thinking原样保留(self):
        """转换层无损：是否回传无签名 thinking 由具体 Provider 决定"""
        msg = Message(
            role=MessageRole.ASSISTANT,
            type=MessageType.TEXT,
            content="答",
            thinking="无签名的思考",
        )
        (pm,) = to_provider_messages([msg])
        assert pm.thinking == "无签名的思考"
        assert pm.thinking_signature is None

    def test_序列化往返后再转换结果一致(self):
        """to_dict/from_dict 往返不丢协议字段（持久化恢复的前提）"""
        original = message_from_response(
            make_provider_response(
                content="查一下",
                thinking="想",
                thinking_signature="sig_1",
                tool_calls=[
                    ToolCall(id="c1", name="get_weather", input={"city": "北京"})
                ],
            )
        )
        restored = Message.from_dict(original.to_dict())
        assert isinstance(restored, ToolCallMessage)
        assert to_provider_messages([restored]) == to_provider_messages([original])
