"""application 组合根的离线集成测试

与逐层单测的分工：这里不重复层内行为，只验证「拼缝」——
create_app 接线的各层真实协作（真实 bus / registry / executor /
loop / convert），模型侧注入 ScriptedProvider 保持离线。
"""

from typing import List

from fakes import ScriptedProvider, WeatherTool, make_provider_response

from src.application import Application, create_app
from src.core import (
    Event,
    EventType,
    MessageRole,
    MessageType,
    ToolCall,
    ToolResultMessage,
)
from src.models import ModelConfig, ModelProviderType
from src.runtime import RunStatus


def make_config() -> ModelConfig:
    """provider 注入时 config 仅作占位（不会被用于创建真实 Provider）"""
    return ModelConfig(provider=ModelProviderType.OPENAI, model_id="test-model")


def collect(app: Application) -> List[Event]:
    """订阅真实总线收集全部事件（验证 bus 在 loop 与 executor 之间共享）"""
    events: List[Event] = []
    app.bus.subscribe(events.append)
    return events


def weather_call(call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name="get_weather", input={"city": "北京"})


async def test_单轮问答闭环():
    provider = ScriptedProvider([make_provider_response(content="你好")])
    app = create_app(make_config(), provider=provider)
    events = collect(app)

    result = await app.loop.run("你好")

    assert result.status == RunStatus.COMPLETED
    assert result.steps == 1
    assert result.token_used == 10
    # 默认 Agent 定义生效：首条为 system prompt
    assert [(m.role, m.type) for m in app.loop.history.messages] == [
        (MessageRole.SYSTEM, MessageType.TEXT),
        (MessageRole.USER, MessageType.TEXT),
        (MessageRole.ASSISTANT, MessageType.TEXT),
    ]
    assert [e.type for e in events] == [
        EventType.TASK_START,
        EventType.STEP_START,
        EventType.STEP_END,
        EventType.TASK_END,
    ]


async def test_工具调用闭环的跨层契约():
    provider = ScriptedProvider(
        [
            make_provider_response(content="", tool_calls=[weather_call()]),
            make_provider_response(content="北京今天晴"),
        ]
    )
    app = create_app(make_config(), tools=[WeatherTool()], provider=provider)
    events = collect(app)

    result = await app.loop.run("北京天气如何？")

    assert result.status == RunStatus.COMPLETED
    assert result.steps == 2

    # 历史：system → user → tool_use → tool_result → assistant 最终回答
    history = app.loop.history.messages
    assert [m.type for m in history] == [
        MessageType.TEXT,
        MessageType.TEXT,
        MessageType.TOOL_USE,
        MessageType.TOOL_RESULT,
        MessageType.TEXT,
    ]
    tool_result = history[3]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.is_success
    assert tool_result.content == "晴"

    # 喂回模型的上下文（拼缝核心：tool_call 配对 + tool 结果回传）
    second_call = provider.calls[1]
    assert [m.role for m in second_call] == ["system", "user", "assistant", "tool"]
    assert second_call[2].tool_calls[0].id == "call_1"
    assert second_call[3].tool_call_id == "call_1"
    assert second_call[3].content == "晴"

    # bus 为 loop 与 executor 共享：两类 source 的事件按序到达同一订阅者
    assert [(e.type, e.source) for e in events] == [
        (EventType.TASK_START, "agent_loop"),
        (EventType.STEP_START, "agent_loop"),
        (EventType.TOOL_EXECUTION_START, "tool_executor"),
        (EventType.TOOL_EXECUTION_END, "tool_executor"),
        (EventType.STEP_END, "agent_loop"),
        (EventType.STEP_START, "agent_loop"),
        (EventType.STEP_END, "agent_loop"),
        (EventType.TASK_END, "agent_loop"),
    ]


async def test_模型幻觉调用未注册工具时失败结果回喂():
    provider = ScriptedProvider(
        [
            make_provider_response(
                content="",
                tool_calls=[ToolCall(id="c1", name="not_exist", input={})],
            ),
            make_provider_response(content="抱歉，没有这个工具"),
        ]
    )
    app = create_app(make_config(), tools=[WeatherTool()], provider=provider)
    events = collect(app)

    result = await app.loop.run("调用一个不存在的工具")

    assert result.status == RunStatus.COMPLETED
    tool_result = app.loop.history.messages[3]
    assert isinstance(tool_result, ToolResultMessage)
    assert not tool_result.is_success
    assert tool_result.status == "tool_not_found"
    failed = [e for e in events if e.type == EventType.TOOL_EXECUTION_FAILED]
    assert failed and failed[0].metadata["error_type"] == "tool_not_found"


async def test_模型调用失败以FAILED收尾且不抛出():
    provider = ScriptedProvider([RuntimeError("连接失败")])
    app = create_app(make_config(), provider=provider)
    events = collect(app)

    result = await app.loop.run("任意输入")

    assert result.status == RunStatus.FAILED
    assert result.error is not None and "连接失败" in result.error
    assert EventType.TASK_FAILED in [e.type for e in events]
