"""真实 API 契约测试（live）

验证我们对厂商行为的关键假设是否仍然成立（如思考字段名、reasoning_content
回传规则）。消耗真实 API 额度，默认被 addopts 排除，
需手动运行：pytest -m live
"""

import os

import pytest
from dotenv import load_dotenv
from fakes import WeatherTool

from src.core import ProviderMessage
from src.models import (
    ModelConfig,
    ModelProviderType,
    OpenAIProvider,
)

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="未配置 LLM_API_KEY"),
]


@pytest.fixture(scope="module")
def live_provider():
    return OpenAIProvider(
        ModelConfig(
            provider=ModelProviderType.OPENAI,
            model_id=os.getenv("LLM_API_MODEL", ""),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            max_tokens=1024,
            extra_body={"thinking": {"type": "enabled"}},
        )
    )


async def test_思考模式下thinking和content都非空(live_provider):
    resp = await live_provider.complete(
        [ProviderMessage(role="user", content="9.11 和 9.9 哪个大？")]
    )
    assert resp.thinking, "思考模式开启时 thinking 不应为空"
    assert resp.content, "最终回答不应为空"


async def test_带tools时回传reasoning_content不报400(live_provider):
    """DeepSeek 规则：携带 tools 的请求必须完整回传历史 reasoning_content"""
    msgs = [ProviderMessage(role="user", content="北京今天天气怎么样？请用工具查询。")]
    resp1 = await live_provider.complete(msgs, tools=[WeatherTool()])

    msgs.append(
        ProviderMessage(
            role="assistant", content=resp1.content, thinking=resp1.thinking
        )
    )
    msgs.append(ProviderMessage(role="user", content="那上海呢？"))
    resp2 = await live_provider.complete(msgs, tools=[WeatherTool()])

    assert resp2.finish_reason in ("stop", "tool_calls", "length")
