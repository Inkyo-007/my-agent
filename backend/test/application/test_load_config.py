"""application/config.py 的单元测试

环境变量由 monkeypatch 全权控制；autouse fixture 阻断真实 .env 加载，
避免本机的 backend/.env 污染「变量缺失」类用例。
"""

import pytest

from src.application import ConfigError, load_model_config
from src.application import config as config_module
from src.models import ModelProviderType


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """阻断真实 .env 加载，测试只受 monkeypatch 的环境变量控制"""
    monkeypatch.setattr(config_module, "load_dotenv", lambda: None)


@pytest.fixture
def env(monkeypatch):
    """清空相关环境变量，返回可继续 setenv/delenv 的 monkeypatch"""
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_API_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def set_required(env, provider: str | None = None) -> None:
    """补齐必需变量，provider 为 None 时保持缺省"""
    env.setenv("LLM_API_KEY", "key")
    env.setenv("LLM_API_MODEL", "deepseek-chat")
    if provider is not None:
        env.setenv("LLM_PROVIDER", provider)


def test_变量齐全时返回配置(env):
    set_required(env)
    env.setenv("LLM_BASE_URL", "https://api.deepseek.com")

    config = load_model_config()

    assert config.provider == ModelProviderType.OPENAI
    assert config.model_id == "deepseek-chat"
    assert config.api_key == "key"
    assert config.base_url == "https://api.deepseek.com"


def test_provider缺省为openai且base_url为None(env):
    set_required(env)

    config = load_model_config()

    assert config.provider == ModelProviderType.OPENAI
    assert config.base_url is None


def test_provider映射claude且大小写不敏感(env):
    set_required(env, provider="Claude")

    config = load_model_config()

    assert config.provider == ModelProviderType.CLAUDE


def test_非法provider报错(env):
    set_required(env, provider="gemini")

    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        load_model_config()


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "LLM_API_MODEL"])
def test_必需变量缺失时报错并指明变量名(env, missing):
    set_required(env)
    env.delenv(missing)

    with pytest.raises(ConfigError, match=missing):
        load_model_config()
