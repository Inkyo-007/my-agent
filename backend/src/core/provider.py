"""Provider 契约（端口）：模型服务的抽象接口与统一消息格式

本模块定义的是「契约」而非「实现」：
- runtime 等编排层只依赖这里的接口与数据格式，不认识任何具体厂商
- 具体实现（OpenAIProvider、ClaudeProvider）在 models/ 包中，实现本模块的 BaseProvider

契约包含：
- ProviderMessage / ProviderResponse：非流式的统一输入/输出格式
- StreamType / StreamProviderResponse：流式的统一事件格式
- BaseProvider：所有模型 Provider 必须实现的抽象基类
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Generator, List

from .tool import Tool, ToolCall


@dataclass
class ProviderMessage:
    """喂给模型 / 从模型解析的统一消息格式（协议层）

    与应用层的 core.Message 分工不同：本类承载协议保真所需的字段
    （思考签名、加密思考数据、tool_call 配对），Message 承载应用记录
    所需的字段（id、时间戳、元数据）。两者之间的转换由运行时转换层负责。
    """

    role: str
    content: str | None = None
    thinking: str | None = None
    thinking_signature: str | None = None  # 签名（Anthropic 特有，多轮回传校验用）
    redacted_thinking: str | None = None   # 加密思考数据（Anthropic 特有，原样回传）
    tool_calls: List[ToolCall] = field(default_factory=list)  # assistant 发出的工具调用
    tool_call_id: str | None = None  # role="tool" 时：对应哪一次调用


@dataclass
class ProviderResponse:
    """模型非流式响应的统一格式"""

    content: str
    model: str
    finish_reason: str
    thinking: str | None = None
    thinking_signature: str | None = None  # 签名（Anthropic 特有）
    redacted_thinking: str | None = None   # 加密思考数据（Anthropic 特有）
    token_used: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)


class StreamType(Enum):
    """流式事件类型"""

    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"  # 思考内容增量
    TOOL_CALL_DELTA = "tool_call_delta"
    START = "start"
    STOP = "stop"


@dataclass
class StreamProviderResponse:
    """模型流式响应的统一事件格式

    delta 字典的约定：
    - TEXT_DELTA:       {"content": str}
    - THINKING_DELTA:   {"thinking": str} 或 {"signature": str} 或 {"redacted_thinking": str}
    - TOOL_CALL_DELTA:  {"index": int, "id": str|None, "name": str|None, "input": str|None}
                        （id/name 仅首个碎片携带，input 为 JSON 文本碎片）
    - START:            {"role": str}
    - STOP:             {"finish_reason": str}，且 token_used 字段携带完整统计；
                        STOP 一定是流的最后一个事件
    """

    type: StreamType
    token_used: int = 0
    delta: Dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    """模型 Provider 抽象基类（端口）

    每个实现对应一种 API 协议（而非一个厂商）：
    OpenAI Chat Completions 协议、Anthropic Messages 协议等。

    注意：本类不定义 __init__——如何构造（需要什么配置）是实现自己的
    事情，端口只承诺 complete/stream 两个行为。
    """

    @abstractmethod
    def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        """非流式请求"""
        pass

    @abstractmethod
    def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Generator[StreamProviderResponse, None, None]:
        """流式请求"""
        pass
