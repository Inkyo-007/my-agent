"""models 包：LLM Provider 抽象与具体实现

对外统一从本包导出（from src.models import ...）。
拆分说明：
- base.py            统一配置、消息/响应格式、抽象基类（无 SDK 依赖）
- openai_provider.py OpenAI Chat Completions 协议（OpenAI/DeepSeek/Qwen 等）
- claude_provider.py Anthropic Messages 协议
"""
from .base import (
    BaseProvider,
    ModelConfig,
    ModelProviderType,
    ProviderMessage,
    ProviderResponse,
    StreamProviderResponse,
    StreamType,
)
from .claude_provider import ClaudeProvider
from .openai_provider import OpenAIProvider


def create_provider(config: ModelConfig) -> BaseProvider:
    """根据配置创建合适的 Provider"""
    if config.provider == ModelProviderType.CLAUDE:
        return ClaudeProvider(config)
    elif config.provider == ModelProviderType.OPENAI:
        return OpenAIProvider(config)
    else:
        raise ValueError(f"不支持的 provider 类型: {config.provider}")


__all__ = [
    "BaseProvider",
    "ClaudeProvider",
    "ModelConfig",
    "ModelProviderType",
    "OpenAIProvider",
    "ProviderMessage",
    "ProviderResponse",
    "StreamProviderResponse",
    "StreamType",
    "create_provider",
]
