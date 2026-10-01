"""runtime 包：运行时引擎层

- loop.py     AgentLoop 主循环、RunResult 结果类型
- history.py  MessageHistory 会话历史
- convert.py  Message ↔ ProviderMessage 双向转换（纯函数）
- bus.py      InMemoryEventBus 事件总线内存实现
- stream.py   StreamAccumulator 流式增量 → ProviderResponse 组装器

本层只依赖 core 端口，具体实现（Provider、ToolExecutor）的接线
在 application 层完成。
"""

from .bus import InMemoryEventBus
from .convert import (
    message_from_response,
    message_from_tool_result,
    to_provider_messages,
)
from .history import MessageHistory
from .loop import AgentLoop, ContextTransform, RunResult, RunStatus
from .stream import StreamAccumulator

__all__ = [
    "AgentLoop",
    "ContextTransform",
    "InMemoryEventBus",
    "MessageHistory",
    "RunResult",
    "RunStatus",
    "StreamAccumulator",
    "message_from_response",
    "message_from_tool_result",
    "to_provider_messages",
]
