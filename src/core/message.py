"""
定义 Agent 系统中所有信息的传递格式。
无论是用户输入、模型回复、工具结果，都要包装成 Message。
"""

from dataclasses import dataclass, field
from typing import Any, Dict
from enum import Enum
from datetime import datetime
import uuid
import json

class MessageRole(str, Enum):
    """消息角色"""

    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"
    TOOL = "tool"

class MessageType(str, Enum):
    """消息类型"""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    EVENT = "event"

@dataclass
class Message:
    """通用消息类"""

    role: MessageRole
    type: MessageType
    content: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """将消息对象转换为字典"""
        return {
            "role": self.role.value,
            "type": self.type.value,
            "content": self.content,
            "message_id": self.message_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_json(self) -> str:
        """将消息对象转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典创建消息对象"""
        return cls(
            role=MessageRole(data["role"]),
            type=MessageType(data["type"]),
            content=data["content"],
            message_id=data.get("message_id", str(uuid.uuid4())),
            metadata=data.get("metadata", {}),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
        )

@dataclass
class ToolCallMessage(Message):
    """工具调用消息类"""

    @property
    def tool_name(self) -> str:
        value = self.metadata.get("tool_name")
        return str(value) if value is not None else ""

    @property
    def tool_params(self) -> Dict[str, Any]:
        value = self.metadata.get("tool_params", {})
        return value if isinstance(value, dict) else {}

@dataclass
class ToolResultMessage(Message):
    """工具调用结果消息类"""

    @property
    def status(self) -> str:
        return str(self.metadata.get("status", "unknown"))

    @property
    def is_success(self) -> bool:
        return self.status == "success"