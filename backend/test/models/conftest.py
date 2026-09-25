"""models 测试的共享 fixture：打桩后的 Provider

仅对 models/ 目录下的测试可见（pytest 的 conftest 层级语义）。
共享的 fake 工厂在 test/fakes.py，显式导入使用。
"""

import pytest
from fakes import make_response

from src.models import ModelConfig, ModelProviderType, OpenAIProvider


@pytest.fixture
def provider():
    """打桩后的 OpenAIProvider：真实 HTTP 调用被替换为参数捕获器

    - captured：complete() 实际组装的请求参数
    - stubbed_response：测试可替换的伪造响应
    """
    p = OpenAIProvider(
        ModelConfig(
            provider=ModelProviderType.OPENAI,
            model_id="deepseek-chat",
            api_key="fake-key",
        )
    )
    p.captured = {}
    p.stubbed_response = make_response()

    def fake_create(**kwargs):
        p.captured = kwargs
        return p.stubbed_response

    p.client.chat.completions.create = fake_create
    return p
