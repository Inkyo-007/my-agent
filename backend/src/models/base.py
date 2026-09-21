"""Provider 层的基础定义：统一配置、消息/响应格式与抽象基类

本模块不依赖任何厂商 SDK（openai / anthropic），可独立导入。
具体 Provider 实现见 openai_provider.py 与 claude_provider.py。
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Generator, Literal
from abc import ABC, abstractmethod

from ..core import Tool, ToolCall


class ModelProviderType(Enum):
    CLAUDE = "claude"
    OPENAI = "openai"


@dataclass
class ModelConfig:
    """模型配置

    Args:
        provider: 模型提供者类型（CLAUDE 或 OPENAI）
        model_id: 模型名称（如 "gpt-4o", "deepseek-chat", "qwen-plus"）
        api_key: API 密钥
        base_url: API base URL（用于兼容服务，如 "https://api.deepseek.com"）
        timeout: 请求超时（秒）
        max_retries: 失败重试次数
        max_tokens: 最大生成 token 数（思考模式下包含思考 token）
        max_tokens_param: 长度限制参数名——"max_tokens"（DeepSeek 等兼容服务）或
            "max_completion_tokens"（OpenAI 官方新模型，o 系列必须使用，上限含推理 token）
        reasoning_effort: 推理强度档位，None 表示不开启。
            OpenAI 官方推理模型映射为 reasoning_effort 参数（"low"/"medium"/"high"），
            Anthropic 新模型（Opus 4.6+）映射为自适应思考 + output_config.effort
            （"low"/"medium"/"high"/"xhigh"/"max"）
        thinking_budget: 思考 token 预算（Anthropic 特有，最小 1024，必须小于 max_tokens），
            None 表示不开启扩展思考
        extra_body: 厂商扩展参数（如 DeepSeek 思考开关 {"thinking": {"type": "enabled"}}），
            原样透传给 API，None 表示不使用扩展参数
    """

    provider: ModelProviderType
    model_id: str
    api_key: str | None = None
    base_url: str | None = None
    timeout: int = 30
    max_retries: int = 2
    max_tokens: int = 4096
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    reasoning_effort: str | None = None
    thinking_budget: int | None = None
    extra_body: Dict[str, Any] | None = None

    def client_kwargs(self) -> Dict[str, Any]:
        """返回客户端构造参数（白名单），排除 None 值

        请求级参数（model_id、max_tokens、思考配置等）不在此处返回，
        由各 Provider 在调用时自行组装。
        """
        return {
            k: v
            for k, v in {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "timeout": self.timeout,
                "max_retries": self.max_retries,
            }.items()
            if v is not None
        }


@dataclass
class ProviderMessage:

    role: str
    content: str | None = None
    thinking: str | None = None
    thinking_signature: str | None = None  # 签名（Anthropic 特有，多轮回传校验用）
    redacted_thinking: str | None = None   # 加密思考数据（Anthropic 特有，原样回传）
    tool_calls: List[ToolCall] = field(default_factory=list)  # assistant 发出的工具调用
    tool_call_id: str | None = None  # role="tool" 时：对应哪一次调用


@dataclass
class ProviderResponse:

    content: str
    model: str
    finish_reason: str
    thinking: str | None = None
    thinking_signature: str | None = None  # 签名（Anthropic 特有）
    redacted_thinking: str | None = None   # 加密思考数据（Anthropic 特有）
    token_used: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)


class StreamType(Enum):

    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"  # 思考内容增量
    TOOL_CALL_DELTA = "tool_call_delta"
    START = "start"
    STOP = "stop"


@dataclass
class StreamProviderResponse:

    type: StreamType
    token_used: int = 0
    delta: Dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):

    def __init__(self, config: ModelConfig):
        self.config = config

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
