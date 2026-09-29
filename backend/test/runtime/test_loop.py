"""runtime/loop.py 的离线单元测试

用 ScriptedProvider（脚本化响应）+ WeatherTool + CollectingBus 跑完整
多轮循环，断言消息序列、喂回模型的上下文、事件序列与状态机迁移。
"""

import asyncio

import pytest
from fakes import (
    CollectingBus,
    ScriptedProvider,
    WeatherTool,
    make_provider_response,
)

from src.core import (
    Agent,
    AgentState,
    EventType,
    Message,
    MessageRole,
    MessageType,
    ToolCall,
    ToolResultMessage,
)
from src.runtime import AgentLoop, MessageHistory, RunStatus
from src.tools import ToolExecutor, ToolRegistry


def weather_call(call_id: str = "call_1", city: str = "北京") -> ToolCall:
    return ToolCall(id=call_id, name="get_weather", input={"city": city})


def make_agent(**kwargs) -> Agent:
    kwargs.setdefault("agent_id", "test")
    kwargs.setdefault("name", "测试")
    return Agent(**kwargs)


def make_loop(responses, bus=None, agent=None, **kwargs):
    """组装一个离线可跑的 AgentLoop：脚本化 Provider + 真实工具执行器"""
    registry = ToolRegistry()
    registry.register(WeatherTool())
    executor = ToolExecutor(registry, event_bus=bus)
    provider = ScriptedProvider(responses)
    loop = AgentLoop(
        agent=agent or make_agent(),
        provider=provider,
        executor=executor,
        event_bus=bus,
        **kwargs,
    )
    return loop, provider


def loop_events(bus):
    """只取主循环发射的事件（排除执行器的 TOOL_* 事件）"""
    return [e for e in bus.events if e.source == "agent_loop"]


class Test单轮问答:
    async def test_无工具调用一次结束(self):
        bus = CollectingBus()
        loop, provider = make_loop([make_provider_response(content="你好")], bus=bus)

        result = await loop.run("hi")

        assert result.status == RunStatus.COMPLETED
        assert result.steps == 1
        assert result.token_used == 10
        # 历史：user → assistant
        assert [m.role for m in loop.history.messages] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
        ]
        assert loop.history.messages[-1].content == "你好"
        # 喂给模型的只有用户输入
        assert len(provider.calls) == 1
        assert provider.calls[0][0].role == "user"
        assert provider.calls[0][0].content == "hi"
        # 事件序列
        assert [e.type for e in loop_events(bus)] == [
            EventType.TASK_START,
            EventType.STEP_START,
            EventType.STEP_END,
            EventType.TASK_END,
        ]

    async def test_事件携带agent_id(self):
        """多 Agent 场景的观测基础：主循环事件统一携带 agent_id"""
        bus = CollectingBus()
        loop, _ = make_loop(
            [make_provider_response(content="你好")],
            bus=bus,
            agent=make_agent(agent_id="agent-007"),
        )

        await loop.run("hi")

        assert all(e.metadata["agent_id"] == "agent-007" for e in loop_events(bus))

    async def test_无事件总线时静默运行(self):
        loop, _ = make_loop([make_provider_response(content="你好")])
        result = await loop.run("hi")
        assert result.status == RunStatus.COMPLETED


class Test工具调用闭环:
    async def test_一轮工具调用后给出最终回答(self):
        bus = CollectingBus()
        loop, provider = make_loop(
            [
                make_provider_response(
                    content="我查一下",
                    tool_calls=[weather_call()],
                    finish_reason="tool_calls",
                ),
                make_provider_response(content="北京今天晴", token_used=20),
            ],
            bus=bus,
        )

        result = await loop.run("北京天气？")

        assert result.status == RunStatus.COMPLETED
        assert result.steps == 2
        assert result.token_used == 30  # 两轮累计
        # 历史：user → assistant(tool_use) → tool → assistant
        roles = [m.role for m in loop.history.messages]
        assert roles == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]
        # 第二次调用模型时，工具结果已正确回喂（tool_call 配对）
        second_call = provider.calls[1]
        assert second_call[-1].role == "tool"
        assert second_call[-1].tool_call_id == "call_1"
        assert second_call[-1].content == "晴"
        assert second_call[-2].tool_calls == [weather_call()]

    async def test_同一轮多个工具调用串行执行(self):
        loop, provider = make_loop(
            [
                make_provider_response(
                    tool_calls=[
                        weather_call("call_1", "北京"),
                        weather_call("call_2", "上海"),
                    ],
                    finish_reason="tool_calls",
                ),
                make_provider_response(content="两地都晴"),
            ]
        )

        result = await loop.run("北京和上海天气？")

        assert result.status == RunStatus.COMPLETED
        # 两条 tool 结果连续入历史，与调用一一配对
        tool_msgs = [m for m in loop.history.messages if m.role == MessageRole.TOOL]
        assert [m.metadata["tool_call_id"] for m in tool_msgs] == ["call_1", "call_2"]
        # 第二次调模型时两条结果连续出现（Claude 的合并规则依赖于此）
        second_call = provider.calls[1]
        assert second_call[-2].role == "tool"
        assert second_call[-1].role == "tool"
        assert second_call[-2].tool_call_id == "call_1"
        assert second_call[-1].tool_call_id == "call_2"

    async def test_工具失败结果喂回模型自我纠正(self):
        """工具不存在时，失败文案回喂模型，循环继续而非中断"""
        loop, provider = make_loop(
            [
                make_provider_response(
                    tool_calls=[
                        ToolCall(id="call_1", name="get_weathe", input={"city": "北京"})
                    ],
                    finish_reason="tool_calls",
                ),
                make_provider_response(content="抱歉，工具名写错了"),
            ]
        )

        result = await loop.run("北京天气？")

        assert result.status == RunStatus.COMPLETED
        fed_back = provider.calls[1][-1]
        assert fed_back.role == "tool"
        assert "未注册" in (fed_back.content or "")  # 执行器的错误文案
        tool_msg = loop.history.messages[-2]
        assert isinstance(tool_msg, ToolResultMessage)
        assert tool_msg.status == "tool_not_found"


class Test异常与兜底:
    async def test_模型调用失败返回FAILED不抛异常(self):
        bus = CollectingBus()
        loop, _ = make_loop([ConnectionError("连接被拒")], bus=bus)

        result = await loop.run("hi")

        assert result.status == RunStatus.FAILED
        assert result.error is not None
        assert "连接被拒" in result.error
        assert [e.type for e in loop_events(bus)] == [
            EventType.TASK_START,
            EventType.STEP_START,
            EventType.STEP_FAILED,
            EventType.TASK_FAILED,
        ]

    async def test_达到max_steps兜底收尾(self):
        bus = CollectingBus()
        # 模型永远要求调用工具
        responses = [
            make_provider_response(
                tool_calls=[weather_call()], finish_reason="tool_calls"
            )
            for _ in range(3)
        ]
        loop, provider = make_loop(responses, bus=bus, agent=make_agent(max_steps=3))

        result = await loop.run("hi")

        assert result.status == RunStatus.MAX_STEPS_REACHED
        assert result.steps == 3
        assert len(provider.calls) == 3
        end_events = [e for e in loop_events(bus) if e.type == EventType.TASK_END]
        assert end_events[0].metadata["status"] == "max_steps_reached"


class Test状态机:
    async def test_初始IDLE完成后COMPLETED(self):
        loop, _ = make_loop([make_provider_response(content="你好")])
        assert loop.state == AgentState.IDLE

        await loop.run("hi")

        assert loop.state == AgentState.COMPLETED

    async def test_失败后为FAILED(self):
        loop, _ = make_loop([ConnectionError("连接被拒")])
        await loop.run("hi")
        assert loop.state == AgentState.FAILED

    async def test_执行中重入run抛RuntimeError(self):
        """并发重入是调用方的编程错误：抛异常而非结果化"""
        gate = asyncio.Event()

        class BlockingProvider(ScriptedProvider):
            async def complete(self, messages, tools=None):
                await gate.wait()  # 阻塞以制造「执行中」窗口
                return await super().complete(messages, tools)

        registry = ToolRegistry()
        registry.register(WeatherTool())
        loop = AgentLoop(
            agent=make_agent(),
            provider=BlockingProvider([make_provider_response(content="ok")]),
            executor=ToolExecutor(registry),
        )

        task = asyncio.create_task(loop.run("hi"))
        await asyncio.sleep(0)  # 让任务推进到 EXECUTING
        assert loop.state == AgentState.EXECUTING

        with pytest.raises(RuntimeError, match="重入"):
            await loop.run("again")

        gate.set()
        result = await task
        assert result.status == RunStatus.COMPLETED

    async def test_完成后可再次运行(self):
        """COMPLETED/FAILED 是终态而非锁定态：同一 loop 可继续多轮对话"""
        loop, provider = make_loop(
            [
                make_provider_response(content="第一答"),
                make_provider_response(content="第二答"),
            ]
        )

        await loop.run("第一问")
        result = await loop.run("第二问")

        assert result.status == RunStatus.COMPLETED
        assert provider.calls[1][-1].content == "第二问"


class Test上下文组装:
    async def test_system_prompt作为首条消息(self):
        loop, provider = make_loop(
            [make_provider_response(content="答")],
            agent=make_agent(system_prompt="你是助手"),
        )

        await loop.run("hi")

        assert loop.history.messages[0].role == MessageRole.SYSTEM
        assert provider.calls[0][0].role == "system"
        assert provider.calls[0][0].content == "你是助手"

    async def test_恢复会话时不重复添加system_prompt(self):
        history = MessageHistory(
            [Message(role=MessageRole.USER, type=MessageType.TEXT, content="旧消息")]
        )
        loop, provider = make_loop(
            [make_provider_response(content="答")],
            agent=make_agent(system_prompt="你是助手"),
            history=history,
        )

        await loop.run("hi")

        # 历史非空时视为恢复的会话，不再插入 system prompt
        assert all(m.role != MessageRole.SYSTEM for m in loop.history.messages)
        assert provider.calls[0][0].content == "旧消息"

    async def test_上下文变换注入点(self):
        """context_transform 在喂模型前加工消息列表（memory 压缩的挂载点）"""
        loop, provider = make_loop(
            [
                make_provider_response(tool_calls=[weather_call()]),
                make_provider_response(content="答"),
            ],
            context_transform=lambda msgs: msgs[-2:],  # 只保留最近两条
        )

        await loop.run("hi")

        # 第二次调用时历史已有 4 条，但喂给模型的只有最后 2 条
        assert len(provider.calls[0]) == 1
        assert len(provider.calls[1]) == 2
