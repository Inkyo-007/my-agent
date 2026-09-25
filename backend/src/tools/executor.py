"""工具执行器：ToolCall → ToolResult 的完整流水线

流水线（任一步失败即包装为 ToolResult 返回，不抛异常）：
1. validator.check_tool_exists    → error_type="tool_not_found"
2. validator.validate_tool_input  → error_type="invalid_input"
3. 钩子链（首个否决短路）          → error_type="permission_denied"
4. 执行（超时控制 + 异常兜底）     → error_type="timeout" / "execution_error"

error_type 是稳定契约（上述五个值），供 UI/审计/循环分类消费；
错误的可读文案放在 content——循环会把它喂回模型，模型据此自我纠正。
"""

import asyncio
import time
from typing import Any, Callable, List

from ..core import (
    Event,
    EventBus,
    EventType,
    HookVerdict,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from .registry import ToolRegistry
from .validator import check_tool_exists, validate_tool_input

# 执行钩子：工具执行前的策略检查点（security 的挂载点）。
# 任何符合此签名的可调用对象都是合法实现——普通函数，
# 或将来 security 模块里某个管理器的绑定方法（如 manager.before_execute）。
ExecutionHook = Callable[[ToolCall, ToolDefinition], HookVerdict]


class ToolExecutor:
    """工具执行器

    职责边界：编排「查校验、过钩子、执行、包装结果」的流水线；
    校验的机械细节在 validator.py，权限策略在钩子实现（security），
    工具的业务逻辑在各 Tool 实现——本类不含任何具体规则。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        hooks: List[ExecutionHook] | None = None,
        event_bus: EventBus | None = None,
    ):
        self.registry = registry
        self.hooks = hooks or []  # 空链 = 全部放行
        self.event_bus = event_bus  # None = 静默（事件是通知，不是刚需）

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """执行一次工具调用，永远返回 ToolResult，不向调用方抛异常"""
        self._emit(
            EventType.TOOL_EXECUTION_START,
            f"开始执行工具 {tool_call.name}",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
        )

        # 1. 存在性检查
        tool = self.registry.get(tool_call.name)
        if tool is None:
            error = check_tool_exists(tool_call, self.registry)
            return self._fail(tool_call, "tool_not_found", error or "工具不存在")

        # 2. 参数 schema 校验
        errors = validate_tool_input(tool_call, tool.definition.input_schema)
        if errors:
            return self._fail(tool_call, "invalid_input", "；".join(errors))

        # 3. 钩子链：首个否决即短路
        for hook in self.hooks:
            verdict = hook(tool_call, tool.definition)
            if not verdict.allowed:
                return self._fail(
                    tool_call,
                    "permission_denied",
                    verdict.reason or "执行被策略钩子拒绝",
                )

        # 4. 执行（execution_time 只计工具实际调用耗时）
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                tool.call(tool_call.input),
                timeout=tool.definition.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.perf_counter() - start
            return self._fail(
                tool_call,
                "timeout",
                f"工具 {tool_call.name} 执行超时（超过 {tool.definition.timeout_seconds}s）",
                execution_time=elapsed,
            )
        except Exception as e:
            elapsed = time.perf_counter() - start
            return self._fail(
                tool_call,
                "execution_error",
                f"工具 {tool_call.name} 执行异常：{type(e).__name__}: {e}",
                execution_time=elapsed,
            )

        self._emit(
            EventType.TOOL_EXECUTION_END,
            f"工具 {tool_call.name} 执行成功",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
        )
        return result

    def _fail(
        self,
        tool_call: ToolCall,
        error_type: str,
        message: str,
        execution_time: float = 0.0,
    ) -> ToolResult:
        """统一构造失败结果并发射失败事件"""
        self._emit(
            EventType.TOOL_EXECUTION_FAILED,
            message,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            error_type=error_type,
        )
        return ToolResult(
            success=False,
            content=message,
            execution_time=execution_time,
            error_type=error_type,
        )

    def _emit(self, event_type: EventType, content: str, **metadata: Any) -> None:
        """发射事件；event_bus 为 None 时静默"""
        if self.event_bus is not None:
            self.event_bus.publish(
                Event(
                    type=event_type,
                    source="tool_executor",
                    content=content,
                    metadata=metadata,
                )
            )
