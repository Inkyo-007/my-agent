"""
定义 Agent 系统中所有信息的传递格式。
无论是用户输入、模型回复、工具结果，都要包装成 Message。
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict


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
    """通用消息类

    thinking 记录模型的思考内容（仅 assistant 消息可能有值），用于 UI 展示与审计。
    """

    role: MessageRole
    type: MessageType
    content: str
    thinking: str | None = None
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """将消息对象转换为字典"""
        return {
            "role": self.role.value,
            "type": self.type.value,
            "content": self.content,
            "thinking": self.thinking,
            "message_id": self.message_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_json(self) -> str:
        """将消息对象转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典创建消息对象

        按 type 字段还原具体子类（TOOL_USE → ToolCallMessage 等），
        保证序列化往返后子类的属性访问器（tool_name、is_success 等）仍然有效。
        """
        msg_type = MessageType(data["type"])
        target_cls = _TYPE_TO_MESSAGE_CLASS.get(msg_type, Message)
        return target_cls(
            role=MessageRole(data["role"]),
            type=msg_type,
            content=data["content"],
            thinking=data.get("thinking"),
            message_id=data.get("message_id", str(uuid.uuid4())),
            metadata=data.get("metadata", {}),
            timestamp=datetime.fromisoformat(
                data.get("timestamp", datetime.now().isoformat())
            ),
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


# from_dict 的类型分发表：消息类型 → 具体消息类。
# 定义在子类之后（运行时才被查表，不存在前向引用问题）。
_TYPE_TO_MESSAGE_CLASS = {
    MessageType.TOOL_USE: ToolCallMessage,
    MessageType.TOOL_RESULT: ToolResultMessage,
}
