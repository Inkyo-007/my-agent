"""模型配置

ModelConfig / ModelProviderType 是「构造期」配置：由 application 层组装，
传给具体 Provider 实现。runtime 等编排层不感知配置——它们只通过
core.provider 中定义的 BaseProvider 端口与模型交互。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal


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
