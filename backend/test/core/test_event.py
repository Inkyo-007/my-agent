"""core/event.py 的离线单元测试

覆盖：Event 的序列化字段完整性与默认值。
"""

from src.core import Event, EventType


class TestEvent序列化:
    def test_to_dict包含全部字段(self):
        event = Event(
            type=EventType.TOOL_EXECUTION_START,
            source="tool_executor",
            content="开始执行工具 get_weather",
            metadata={"tool_name": "get_weather"},
        )
        data = event.to_dict()
        assert data["type"] == "tool_execution_start"  # 枚举序列化为其值
        assert data["source"] == "tool_executor"
        assert data["content"] == "开始执行工具 get_weather"
        assert data["metadata"] == {"tool_name": "get_weather"}
        assert data["event_id"] == event.event_id
        assert data["timestamp"] == event.timestamp.isoformat()

    def test_默认生成id与空元数据(self):
        event = Event(type=EventType.TASK_START, source="runtime", content="")
        assert event.event_id
        assert event.metadata == {}
