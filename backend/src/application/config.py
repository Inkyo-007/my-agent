"""application 层配置加载：.env 环境变量 → ModelConfig

只负责「读环境、出配置」，不认识其他层的运行时对象。
环境变量（与 test/models/test_live.py 保持一致，模板见 backend/.env.example）：

- LLM_PROVIDER：协议类型，openai（默认）/ claude
- LLM_API_KEY：API 密钥（必需）
- LLM_API_MODEL：模型名称（必需）
- LLM_BASE_URL：兼容服务的 base URL（可选，如 DeepSeek）
"""

import os
from typing import Dict

from dotenv import load_dotenv

from ..models import ModelConfig, ModelProviderType


class ConfigError(ValueError):
    """配置缺失或非法（message 中附带修复指引）"""


_PROVIDERS: Dict[str, ModelProviderType] = {
    "openai": ModelProviderType.OPENAI,
    "claude": ModelProviderType.CLAUDE,
}


def load_model_config() -> ModelConfig:
    """从 .env / 环境变量加载模型配置

    Raises:
        ConfigError: 必需变量缺失，或 LLM_PROVIDER 取值非法
    """
    load_dotenv()

    missing = [name for name in ("LLM_API_KEY", "LLM_API_MODEL") if not os.getenv(name)]
    if missing:
        raise ConfigError(
            f"缺少必需的环境变量：{'、'.join(missing)}（请在 backend/.env 中配置）"
        )

    provider_name = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise ConfigError(
            f"LLM_PROVIDER 取值非法：{provider_name!r}（可选：{'、'.join(_PROVIDERS)}）"
        )

    return ModelConfig(
        provider=provider,
        model_id=os.environ["LLM_API_MODEL"],
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.getenv("LLM_BASE_URL") or None,
    )
