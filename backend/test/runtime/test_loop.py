"""runtime/loop.py 的离线单元测试

用 ScriptedProvider（脚本化响应）+ WeatherTool + CollectingBus 跑完整
多轮循环，断言消息序列、喂回模型的上下文、事件序列与状态机迁移。
"""

import asyncio

import pytest
from fakes import (
    CollectingBus,
    ConcurrencyProbe,
    RecordingTool,
    ScriptedProvider,
    WeatherTool,
    make_provider_response,
    make_stream_events,
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


def make_loop(responses, bus=None, agent=None, registry_tools=None, **kwargs):
    """组装一个离线可跑的 AgentLoop：脚本化 Provider + 真实工具执行器

    registry_tools 指定注册进注册表的工具（默认 [WeatherTool()]）。
    """
    registry = ToolRegistry()
    for tool in registry_tools or [WeatherTool()]:
        registry.register(tool)
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


class Test流式:
    async def test_流式闭环转发增量事件(self):
        bus = CollectingBus()
        loop, provider = make_loop(
            [make_stream_events(content="你好", thinking="想一想", token_used=42)],
            bus=bus,
        )
        result = await loop.run("你好", stream=True)

        assert result.status == RunStatus.COMPLETED
        assert result.token_used == 42

        events = loop_events(bus)
        assert [e.type for e in events] == [
            EventType.TASK_START,
            EventType.STEP_START,
            EventType.MODEL_THINKING_DELTA,
            EventType.MODEL_TEXT_DELTA,
            EventType.STEP_END,
            EventType.TASK_END,
        ]
        assert events[2].content == "想一想"
        assert events[3].content == "你好"
        assert events[2].metadata["step_index"] == 1

        # 组装结果与非流式同路径：最终回答（含思考）入历史
        final = loop.history.messages[-1]
        assert final.content == "你好"
        assert final.thinking == "想一想"

    async def test_流式工具调用闭环(self):
        bus = CollectingBus()
        loop, provider = make_loop(
            [
                make_stream_events(
                    tool_calls=[weather_call()], finish_reason="tool_calls"
                ),
                make_stream_events(content="北京今天晴"),
            ],
            bus=bus,
            tools=[WeatherTool()],
        )
        result = await loop.run("北京天气如何？", stream=True)

        assert result.status == RunStatus.COMPLETED
        assert result.steps == 2
        # make_loop 的默认 agent 无 system prompt：user → tool_use → tool_result → 最终回答
        assert [m.type for m in loop.history.messages] == [
            MessageType.TEXT,
            MessageType.TOOL_USE,
            MessageType.TOOL_RESULT,
            MessageType.TEXT,
        ]
        # 流式路径同样维护 tool_call 配对（跨层拼缝契约）
        second_call = provider.calls[1]
        assert second_call[1].tool_calls[0].id == "call_1"
        assert second_call[2].role == "tool"
        assert second_call[2].tool_call_id == "call_1"
        # executor 的工具事件照常发射，与增量事件共存于同一总线
        types = [e.type for e in bus.events]
        assert EventType.TOOL_EXECUTION_START in types
        assert EventType.MODEL_TEXT_DELTA in types

    async def test_流式中途异常以FAILED收尾(self):
        bus = CollectingBus()
        loop, _ = make_loop([RuntimeError("连接中断")], bus=bus)
        result = await loop.run("任意输入", stream=True)

        assert result.status == RunStatus.FAILED
        assert result.error is not None and "连接中断" in result.error
        assert EventType.TASK_FAILED in [e.type for e in bus.events]

    async def test_非流式默认不发射增量事件(self):
        bus = CollectingBus()
        loop, _ = make_loop([make_provider_response(content="你好")], bus=bus)
        result = await loop.run("你好")

        assert result.status == RunStatus.COMPLETED
        assert not [
            e
            for e in bus.events
            if e.type in (EventType.MODEL_TEXT_DELTA, EventType.MODEL_THINKING_DELTA)
        ]


class Test并行工具执行:
    """同轮多个 tool_call 的并行执行：并行度、限流、保序、失败隔离"""

    @staticmethod
    def make_calls(n: int) -> list:
        return [ToolCall(id=f"c{i}", name=f"tool_{i}", input={}) for i in range(n)]

    async def test_同轮多个工具调用并行执行(self):
        probe = ConcurrencyProbe()
        tools = [
            RecordingTool("tool_0", probe, delay=0.05),
            RecordingTool("tool_1", probe, delay=0.05),
        ]
        loop, _ = make_loop(
            [
                make_provider_response(content="", tool_calls=self.make_calls(2)),
                make_provider_response(content="完成"),
            ],
            registry_tools=tools,
            tools=tools,
        )
        result = await loop.run("并行测试")

        assert result.status == RunStatus.COMPLETED
        # 串行执行时 max_running 只能为 1
        assert probe.max_running == 2

    async def test_限流生效(self):
        probe = ConcurrencyProbe()
        tools = [RecordingTool(f"tool_{i}", probe, delay=0.05) for i in range(3)]
        loop, _ = make_loop(
            [
                make_provider_response(content="", tool_calls=self.make_calls(3)),
                make_provider_response(content="完成"),
            ],
            registry_tools=tools,
            tools=tools,
            max_concurrent_tools=2,
        )
        result = await loop.run("限流测试")

        assert result.status == RunStatus.COMPLETED
        assert probe.max_running == 2

    async def test_历史按提交顺序保序且事件交错可归因(self):
        probe = ConcurrencyProbe()
        slow = RecordingTool("tool_0", probe, delay=0.05)
        fast = RecordingTool("tool_1", probe)
        bus = CollectingBus()
        loop, _ = make_loop(
            [
                make_provider_response(content="", tool_calls=self.make_calls(2)),
                make_provider_response(content="完成"),
            ],
            bus=bus,
            registry_tools=[slow, fast],
            tools=[slow, fast],
        )
        result = await loop.run("保序测试")

        assert result.status == RunStatus.COMPLETED
        # tool_1 先完成，但历史中的 tool_result 顺序与 tool_calls 提交顺序一致
        results = [
            m for m in loop.history.messages if m.type == MessageType.TOOL_RESULT
        ]
        assert [m.metadata["tool_call_id"] for m in results] == ["c0", "c1"]

        # 事件按真实完成时间交错：tool_1 的 END 先于 tool_0 的 END
        ends = [e for e in bus.events if e.type == EventType.TOOL_EXECUTION_END]
        assert [e.metadata["tool_name"] for e in ends] == ["tool_1", "tool_0"]
        # 归属完整：每个 END 都有同 tool_call_id 的 START 先行
        started = set()
        for e in bus.events:
            if e.type == EventType.TOOL_EXECUTION_START:
                started.add(e.metadata["tool_call_id"])
            elif e.type == EventType.TOOL_EXECUTION_END:
                assert e.metadata["tool_call_id"] in started

    async def test_单个工具失败不影响其他并行调用(self):
        probe = ConcurrencyProbe()
        tools = [
            RecordingTool("tool_0", probe, error=RuntimeError("炸了")),
            RecordingTool("tool_1", probe),
        ]
        loop, _ = make_loop(
            [
                make_provider_response(content="", tool_calls=self.make_calls(2)),
                make_provider_response(content="完成"),
            ],
            registry_tools=tools,
            tools=tools,
        )
        result = await loop.run("失败隔离测试")

        assert result.status == RunStatus.COMPLETED
        results = [m for m in loop.history.messages if isinstance(m, ToolResultMessage)]
        assert len(results) == 2
        assert results[0].status == "execution_error"
        assert "炸了" in results[0].content
        assert results[1].is_success
