"""application 层的真实 API 端到端冒烟测试（live）

验证组合根接线在真实协议下能跑通完整闭环（模型调用、事件、历史、
工具调用回喂）。消耗真实 API 额度，默认被 addopts 排除，
需手动运行：pytest -m live（或 poe test-live）。
"""

import os

import pytest
from dotenv import load_dotenv
from fakes import WeatherTool

from src.application import create_app, load_model_config
from src.core import MessageRole, MessageType
from src.runtime import RunStatus

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="未配置 LLM_API_KEY"),
]


async def test_真实模型端到端问答闭环():
    app = create_app(load_model_config())

    result = await app.loop.run("用一句话回答：1 + 1 等于几？")

    assert result.status == RunStatus.COMPLETED
    assert result.token_used > 0
    final = app.loop.history.messages[-1]
    assert final.role == MessageRole.ASSISTANT
    assert final.type == MessageType.TEXT
    assert final.content


async def test_真实模型工具调用闭环():
    app = create_app(load_model_config(), tools=[WeatherTool()])

    result = await app.loop.run(
        "北京今天天气怎么样？请调用 get_weather 工具查询，不要直接回答。"
    )

    assert result.status == RunStatus.COMPLETED
    types = [m.type for m in app.loop.history.messages]
    assert MessageType.TOOL_USE in types, "模型未发起工具调用"
    assert MessageType.TOOL_RESULT in types, "工具结果未写入历史"
