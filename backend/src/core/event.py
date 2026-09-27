"""
定义运行时引擎对外广播的事件。
用于流式输出、日志记录、UI 更新。
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict


class EventType(str, Enum):
    """事件类型"""

    # 任务事件
    TASK_START = "task_start"
    TASK_END = "task_end"
    TASK_FAILED = "task_failed"

    # 步骤事件
    STEP_START = "step_start"
    STEP_END = "step_end"
    STEP_FAILED = "step_failed"

    # 工具事件
    TOOL_CALL_REQUEST = "tool_call_request"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_END = "tool_execution_end"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"

    # 权限事件
    PERMISSION_CHECK = "permission_check"
    APPROVAL_REQUEST = "approval_request"
    DENIAL_REQUEST = "denial_request"


@dataclass
class Event:
    """事件类"""

    type: EventType
    source: str
    content: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """将事件对象转换为字典"""
        return {
            "type": self.type.value,
            "source": self.source,
            "content": self.content,
            "event_id": self.event_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBus(ABC):
    """事件总线（端口）：发射方不关心谁在听

    通知型交互的唯一通道（双通道铁律中「不要回答的用消息」一侧）。
    需要裁决的场景（如权限放行/拦截）不在此——那是 ExecutionHook
    （见 tool.py 的 HookVerdict）。

    实现：runtime/bus.py 的 InMemoryEventBus；将来 observability 模块
    以订阅者身份接入，发射方零改动。
    """

    @abstractmethod
    def publish(self, event: Event) -> None:
        """发布事件。实现方须保证：单个订阅者异常不影响其他订阅者与发射方"""
        pass

    @abstractmethod
    def subscribe(self, handler: Callable[[Event], None]) -> None:
        """订阅全部事件；按类型过滤由订阅者自行处理"""
        pass


class EventEmitter:
    """事件发射助手：各编排类发射事件的统一入口

    放在 core 的原因：tools、runtime、security 等相互独立的功能层都需要
    发射事件，跨层共用的代码只能向内收敛到 core（星型依赖方向）。

    职责只有三样（请勿加宽）：
    1. bus 为 None 时静默——「事件是通知，不是刚需」的物理载体；
    2. 打 source 标签——发射方身份的统一出处；
    3. 构造 Event 并 publish——消除每个发射类重复编写的样板。

    过滤、批处理、异步投递等不属于这里：那是总线实现或订阅者的事。
    """

    def __init__(self, bus: EventBus | None, source: str):
        self._bus = bus
        self._source = source

    def emit(self, event_type: EventType, content: str, **metadata: Any) -> None:
        """发射一个事件；构造时 bus 为 None 则完全静默"""
        if self._bus is not None:
            self._bus.publish(
                Event(
                    type=event_type,
                    source=self._source,
                    content=content,
                    metadata=metadata,
                )
            )
