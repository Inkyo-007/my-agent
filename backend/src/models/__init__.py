"""models 包：模型 Provider 的具体实现

本包只包含实现与构造配置：
- config.py           ModelConfig / ModelProviderType（构造期配置）
- openai_provider.py  OpenAI Chat Completions 协议实现
- claude_provider.py  Anthropic Messages 协议实现

契约（BaseProvider、ProviderMessage 等）定义在 core.provider——
编排层依赖契约，不依赖本包；本包实现契约，被 application 层接线。
"""

from ..core import BaseProvider
from .claude_provider import ClaudeProvider
from .config import ModelConfig, ModelProviderType
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
    "ClaudeProvider",
    "ModelConfig",
    "ModelProviderType",
    "OpenAIProvider",
    "create_provider",
]
