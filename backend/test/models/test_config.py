"""models/config.py 与 models/__init__.py（create_provider 工厂）的离线单元测试"""

import pytest

from src.models import (
    ClaudeProvider,
    ModelConfig,
    ModelProviderType,
    OpenAIProvider,
    create_provider,
)


class TestClientKwargs:
    """client_kwargs()：客户端构造参数的白名单过滤"""

    def test_排除None值(self):
        cfg = ModelConfig(provider=ModelProviderType.OPENAI, model_id="m")
        kwargs = cfg.client_kwargs()
        assert "api_key" not in kwargs
        assert "base_url" not in kwargs
        assert kwargs["timeout"] == 30
        assert kwargs["max_retries"] == 2

    def test_不含请求级参数(self):
        # model_id、max_tokens、思考配置等请求级参数由 Provider 在调用时组装
        cfg = ModelConfig(provider=ModelProviderType.OPENAI, model_id="m", api_key="k")
        kwargs = cfg.client_kwargs()
        assert kwargs["api_key"] == "k"
        assert "model_id" not in kwargs
        assert "max_tokens" not in kwargs
        assert "reasoning_effort" not in kwargs
        assert "extra_body" not in kwargs


class TestCreateProvider:
    """create_provider 工厂：按配置类型分发到具体实现"""

    def test_按类型分发(self):
        openai_cfg = ModelConfig(
            provider=ModelProviderType.OPENAI, model_id="m", api_key="k"
        )
        assert isinstance(create_provider(openai_cfg), OpenAIProvider)

        claude_cfg = ModelConfig(
            provider=ModelProviderType.CLAUDE, model_id="m", api_key="k"
        )
        assert isinstance(create_provider(claude_cfg), ClaudeProvider)

    def test_未知类型抛错(self):
        cfg = ModelConfig(provider="not-a-provider", model_id="m", api_key="k")
        with pytest.raises(ValueError, match="不支持"):
            create_provider(cfg)
