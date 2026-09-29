"""Agent 主循环：组装上下文、调用模型、处理工具调用、记录历史，直到任务完成

设计说明见 docs/design/backend/runtime.md。要点：
- 一轮（step）= 一次模型调用 + 其引发的全部工具执行；
- run() 永不抛异常：模型调用失败包装为 FAILED 的 RunResult；
- 全部依赖都是 core 端口（BaseProvider / BaseToolExecutor / EventBus），
  具体实现的接线在 application 层。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List

from ..core import (
    BaseProvider,
    BaseToolExecutor,
    EventBus,
    EventEmitter,
    EventType,
    Message,
    MessageRole,
    MessageType,
    Tool,
    ToolCall,
    ToolResult,
)
from .convert import (
    message_from_response,
    message_from_tool_result,
    to_provider_messages,
)
from .history import MessageHistory


class RunStatus(str, Enum):
    """一次任务的结束状态"""

    COMPLETED = "completed"  # 模型给出最终回答，正常结束
    MAX_STEPS_REACHED = "max_steps_reached"  # 达到步数上限，兜底收尾
    FAILED = "failed"  # 模型调用失败


@dataclass
class RunResult:
    """一次 run() 的汇总结果；完整会话内容在 loop.history.messages 中"""

    status: RunStatus
    steps: int
    token_used: int = 0
    error: str | None = None


# 上下文变换：喂模型前对消息列表的加工，是 memory 层压缩策略的注入点。
# 不注入则原样透传。
ContextTransform = Callable[[List[Message]], List[Message]]


class AgentLoop:
    """Agent 主循环"""

    def __init__(
        self,
        provider: BaseProvider,
        executor: BaseToolExecutor,
        system_prompt: str | None = None,
        tools: List[Tool] | None = None,
        max_steps: int = 10,
        event_bus: EventBus | None = None,
        context_transform: ContextTransform | None = None,
        history: MessageHistory | None = None,
    ):
        self._provider = provider
        self._executor = executor
        self._tools = tools or []
        self._max_steps = max_steps
        self._context_transform = context_transform
        self._events = EventEmitter(event_bus, source="agent_loop")
        self.history = history or MessageHistory()
        if system_prompt and not self.history.messages:
            # system prompt 作为首条消息存入历史（恢复会话时不重复添加）
            self.history.append(
                Message(
                    role=MessageRole.SYSTEM,
                    type=MessageType.TEXT,
                    content=system_prompt,
                )
            )

    async def run(self, user_input: str) -> RunResult:
        """执行一次任务，返回 RunResult；永不向调用方抛异常"""
        self.history.append(
            Message(role=MessageRole.USER, type=MessageType.TEXT, content=user_input)
        )
        self._events.emit(EventType.TASK_START, "任务开始")

        total_tokens = 0
        for step in range(1, self._max_steps + 1):
            self._events.emit(
                EventType.STEP_START, f"第 {step} 轮开始", step_index=step
            )

            try:
                response = await self._provider.complete(
                    to_provider_messages(self._context_view()),
                    tools=self._tools or None,
                )
            except Exception as e:
                # 模型不可达时无从喂回，终止任务并以结果形式上报
                error = f"模型调用失败：{type(e).__name__}: {e}"
                self._events.emit(EventType.STEP_FAILED, error, step_index=step)
                self._events.emit(EventType.TASK_FAILED, error, steps=step)
                return RunResult(
                    status=RunStatus.FAILED,
                    steps=step,
                    token_used=total_tokens,
                    error=error,
                )

            total_tokens += response.token_used
            self.history.append(message_from_response(response))

            if not response.tool_calls:
                self._events.emit(
                    EventType.STEP_END, f"第 {step} 轮结束", step_index=step
                )
                self._events.emit(
                    EventType.TASK_END,
                    "任务完成",
                    status=RunStatus.COMPLETED.value,
                    steps=step,
                    token_used=total_tokens,
                )
                return RunResult(
                    status=RunStatus.COMPLETED, steps=step, token_used=total_tokens
                )

            # 同一轮的多个工具调用串行执行（并行带来的错误聚合与事件交错
            # 复杂度，留待真实需求出现再引入）
            for tool_call in response.tool_calls:
                result = await self._execute_tool(tool_call)
                self.history.append(message_from_tool_result(tool_call, result))

            self._events.emit(
                EventType.STEP_END,
                f"第 {step} 轮结束",
                step_index=step,
                tool_calls=len(response.tool_calls),
            )

        # max_steps 兜底：这不是错误，是防止无限循环的保险丝
        self._events.emit(
            EventType.TASK_END,
            "达到最大步数，任务收尾",
            status=RunStatus.MAX_STEPS_REACHED.value,
            steps=self._max_steps,
            token_used=total_tokens,
        )
        return RunResult(
            status=RunStatus.MAX_STEPS_REACHED,
            steps=self._max_steps,
            token_used=total_tokens,
        )

    def _context_view(self) -> List[Message]:
        """喂模型前的消息视图：注入了上下文变换则先加工"""
        messages = self.history.messages
        if self._context_transform is not None:
            return self._context_transform(messages)
        return messages

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """执行一次工具调用；防御性兜底保证 run() 不抛出

        BaseToolExecutor 契约已承诺不抛异常，这里是针对违规实现的保险丝。
        """
        try:
            return await self._executor.execute(tool_call)
        except Exception as e:
            return ToolResult(
                success=False,
                content=f"工具执行器异常：{type(e).__name__}: {e}",
                execution_time=0.0,
                error_type="execution_error",
            )
