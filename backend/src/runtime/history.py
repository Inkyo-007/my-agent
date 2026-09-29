"""会话历史：Message 列表的追加与读取

职责被刻意压到最小：追加与只读视图。持久化、压缩等管理策略是
memory 层的事（协作方式见 docs/design/backend/runtime.md）。
"""

from typing import List, Sequence

from ..core import Message, ProviderMessage
from .convert import to_provider_messages


class MessageHistory:
    """会话历史：主循环的贴身工作状态"""

    def __init__(self, messages: Sequence[Message] | None = None):
        # 初始历史用于会话恢复：由 application 从 memory 读出后传入
        self._messages: List[Message] = list(messages) if messages else []

    def append(self, message: Message) -> None:
        self._messages.append(message)

    @property
    def messages(self) -> List[Message]:
        """只读视图：返回副本，外部修改不影响内部状态"""
        return list(self._messages)

    def to_provider_messages(self) -> List[ProviderMessage]:
        """转换为喂模型的协议消息（委托 convert 层）"""
        return to_provider_messages(self._messages)
