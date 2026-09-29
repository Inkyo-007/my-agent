"""事件总线的内存实现

订阅者是普通函数，同步投递。契约要求单个订阅者异常不影响其他
订阅者与发射方：异常被捕获、记录日志后继续投递，不向外抛——
事件是通知，订阅者的故障不能反过来打垮发射方。
"""

import logging
from typing import Callable, List

from ..core import Event, EventBus

logger = logging.getLogger(__name__)


class InMemoryEventBus(EventBus):
    """进程内事件总线"""

    def __init__(self):
        self._handlers: List[Callable[[Event], None]] = []

    def publish(self, event: Event) -> None:
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "事件订阅者处理异常（已跳过，不影响其他订阅者）: %s",
                    event.type.value,
                )

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        self._handlers.append(handler)
