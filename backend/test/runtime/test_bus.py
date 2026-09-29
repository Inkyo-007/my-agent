"""runtime/bus.py 的离线单元测试

覆盖：正常投递、订阅者异常隔离（契约：单订阅者异常不影响他人与发射方）。
"""

import logging

from src.core import Event, EventType
from src.runtime import InMemoryEventBus


def make_event() -> Event:
    return Event(type=EventType.TASK_START, source="test", content="测试事件")


class Test投递:
    def test_所有订阅者都收到同一事件(self):
        bus = InMemoryEventBus()
        received_a = []
        received_b = []
        bus.subscribe(received_a.append)
        bus.subscribe(received_b.append)

        event = make_event()
        bus.publish(event)

        assert received_a == [event]
        assert received_b == [event]

    def test_订阅者异常不影响其他订阅者与发射方(self, caplog):
        bus = InMemoryEventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("订阅者炸了")

        bus.subscribe(bad_handler)
        bus.subscribe(received.append)

        with caplog.at_level(logging.ERROR):
            bus.publish(make_event())  # 不向外抛异常

        assert len(received) == 1  # 后续订阅者正常收到
        assert "订阅者炸了" in caplog.text  # 异常被记录日志

    def test_按订阅顺序投递(self):
        bus = InMemoryEventBus()
        order = []
        bus.subscribe(lambda e: order.append("a"))
        bus.subscribe(lambda e: order.append("b"))

        bus.publish(make_event())

        assert order == ["a", "b"]

    def test_无订阅者时静默(self):
        InMemoryEventBus().publish(make_event())  # 不抛异常即通过
