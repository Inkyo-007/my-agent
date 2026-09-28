"""core/message.py 的离线单元测试

覆盖：thinking 字段的序列化往返、from_dict 的子类类型分发。
"""

from src.core import (
    Message,
    MessageRole,
    MessageType,
    ToolCallMessage,
    ToolResultMessage,
)


class TestThinking字段:
    def test_思考内容参与序列化往返(self):
        msg = Message(
            role=MessageRole.ASSISTANT,
            type=MessageType.TEXT,
            content="北京今天晴",
            thinking="用户在问天气，我应该调用工具",
        )
        restored = Message.from_dict(msg.to_dict())
        assert restored.thinking == "用户在问天气，我应该调用工具"
        assert restored.content == "北京今天晴"

    def test_默认为None(self):
        msg = Message(role=MessageRole.USER, type=MessageType.TEXT, content="hi")
        assert msg.thinking is None
        # 序列化也显式包含该键，保证存储格式稳定
        assert msg.to_dict()["thinking"] is None

    def test_兼容无thinking键的旧数据(self):
        """历史存档中没有 thinking 键时，反序列化不应报错"""
        data = {
            "role": "assistant",
            "type": "text",
            "content": "旧消息",
            "message_id": "abc",
            "metadata": {},
            "timestamp": "2026-01-01T00:00:00",
        }
        msg = Message.from_dict(data)
        assert msg.thinking is None


class TestFromDict类型分发:
    def test_tool_use还原为ToolCallMessage(self):
        original = ToolCallMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.TOOL_USE,
            content="调用天气工具",
            metadata={
                "tool_calls": [
                    {"id": "c1", "name": "get_weather", "input": {"city": "北京"}}
                ]
            },
        )
        restored = Message.from_dict(original.to_dict())
        assert isinstance(restored, ToolCallMessage)
        # 子类属性访问器在往返后仍然有效
        assert restored.tool_calls == [
            {"id": "c1", "name": "get_weather", "input": {"city": "北京"}}
        ]

    def test_tool_result还原为ToolResultMessage(self):
        original = ToolResultMessage(
            role=MessageRole.TOOL,
            type=MessageType.TOOL_RESULT,
            content="晴",
            metadata={"status": "success"},
        )
        restored = Message.from_dict(original.to_dict())
        assert isinstance(restored, ToolResultMessage)
        assert restored.is_success is True

    def test_普通消息不受分发影响(self):
        original = Message(role=MessageRole.USER, type=MessageType.TEXT, content="你好")
        restored = Message.from_dict(original.to_dict())
        assert type(restored) is Message

    def test_往返保持id与元数据(self):
        original = ToolCallMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.TOOL_USE,
            content="调用",
            metadata={"tool_calls": [{"id": "c1", "name": "get_weather", "input": {}}]},
        )
        restored = Message.from_dict(original.to_dict())
        assert restored.message_id == original.message_id
        assert restored.metadata == original.metadata
        assert restored.timestamp == original.timestamp
