"""
定义运行时引擎对外广播的事件。
用于流式输出、日志记录、UI 更新。
"""

from dataclasses import dataclass, field
from typing import Any, Dict
from enum import Enum
from datetime import datetime
import uuid


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