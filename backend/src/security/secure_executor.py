"""security 层 · 执行器集成：组装三个子层的完整裁决流水线

security 唯一与 tools 层接触的出口：实现 ExecutionHook 签名
（async (ToolCall, ToolDefinition) -> HookVerdict），以 before_execute
绑定方法的形式挂入 ToolExecutor 的钩子链，接线在 application 层。

流水线（设计规格见 docs/design/backend/security.md）：
1. 命令护栏：按 schema 的 format == "command" 标注提取命令串并 analyze——
   硬阻断（blocked）在任何模式（含完全访问）下直接 DENY，不走审批；
2. 路径校验：schema 路径参数 + 护栏提取的 cd/重定向目标统一判内/外，
   任一界外（含 INVALID，fail-closed）即触发器命中；
3. decide：权限模式 × 风险等级 × 触发器 → 三态裁决（本文件实现）；
4. 审批：ASK_USER 经 Approver 端口询问用户（审批交互的实现——终端、
   Web——在 application 层注入）；asyncio.Lock 保证并行工具调用下审批
   串行化，只读判定在锁外完成，不牺牲并行度。
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Protocol

from ..core import (
    EventBus,
    EventEmitter,
    EventType,
    HookDecision,
    HookVerdict,
    ToolCall,
    ToolDefinition,
)
from .guardrails import FlaggedCommand, analyze
from .guardrails_rules import RuleVerdict, ShellDialect
from .path_validator import PathLocation, resolve_in_root, validate_call_paths
from .permissions import PermissionMode, RiskLevel, classify_risk, is_network_tag


def decide(
    mode: PermissionMode,
    risk: RiskLevel,
    triggers: List[str] | None = None,
    *,
    unparsable: bool = False,
) -> HookVerdict:
    """裁决矩阵：权限模式 × 风险等级 × 触发器 → 三态 HookVerdict

    矩阵（规格与取舍理由见 security.md「裁决矩阵」）：
    - 完全访问：一律放行；但命令无法解析（unparsable）时升级为询问——
      无法分析的操作不得自动放行（fail-closed）；
    - 只读：SAFE 放行，其余拒绝；
    - 请求询问：触发器命中始终询问，高危（DANGEROUS）询问，其余放行。

    Args:
        mode: 会话权限模式（用户选择）。
        risk: 工具的风险等级（classify_risk 的输出）。
        triggers: 命中的触发器描述列表（如 "涉及工作区外的文件"、
            "将访问网络"），用于拼接询问理由。
        unparsable: 命令是否无法解析（来自命令护栏）。
    """
    triggers = triggers or []
    if mode is PermissionMode.FULL_ACCESS:
        if unparsable:
            return HookVerdict.ask_user("命令无法解析，需要您确认")
        return HookVerdict.allow()
    if mode is PermissionMode.READ_ONLY:
        if risk is RiskLevel.SAFE:
            # 只读模式的语义是「不改本地状态」；网络读取不改本地，放行
            return HookVerdict.allow()
        return HookVerdict.deny("当前会话为只读模式，该操作有副作用，已被拒绝")
    # ASK 模式
    if triggers:
        return HookVerdict.ask_user(f"该操作{'、'.join(triggers)}，需要您确认")
    if unparsable:
        return HookVerdict.ask_user("命令无法解析，需要您确认")
    if risk is RiskLevel.DANGEROUS:
        return HookVerdict.ask_user("该操作为高危操作，需要您确认")
    return HookVerdict.allow()


@dataclass(frozen=True)
class ApprovalRequest:
    """一次审批请求：审批者渲染提示所需的全部细节

    审批必须指名道姓——「有个高危操作要执行」的提示无法支撑知情决定，
    因此携带工具输入原文（要执行的命令、要写入的路径）、界外路径与
    护栏命中明细。
    """

    tool_name: str
    reason: str  # 裁决理由（触发器 / 高危 / 不可解析）
    tool_input: Dict[str, Any] = field(default_factory=dict)  # 工具输入原文
    outside_paths: List[str] = field(default_factory=list)  # 界外路径（参数原文）
    flagged: List[FlaggedCommand] = field(default_factory=list)  # 护栏命中明细


class Approver(Protocol):
    """审批通道端口：如何询问用户、等待回答

    实现（CLI 终端确认、Web 弹窗）在 application 层注入；本层只依赖
    此协议。v1 的回答只有放行/拒绝二值，「不再询问」类规则持久化留待后续。
    """

    async def approve(self, request: ApprovalRequest) -> bool:
        """请求用户裁决；True = 放行，False = 拒绝"""
        ...


class SecureExecutor:
    """security 层的执行钩子：一次工具调用的完整安全裁决

    以 before_execute 绑定方法挂入 ToolExecutor 钩子链（见
    tools/executor.py 的 ExecutionHook）。构造依赖由 application 层注入。
    """

    def __init__(
        self,
        mode: PermissionMode,
        workspace_root: Path,
        approver: Approver | None = None,
        dialect: ShellDialect = ShellDialect.BASH,
        event_bus: EventBus | None = None,
    ):
        self._mode = mode
        self._root = workspace_root.resolve()  # resolve 一次，避免每次调用的 IO
        self._approver = approver
        self._dialect = dialect
        self._events = EventEmitter(event_bus, source="secure_executor")
        # 审批串行化：并行工具调用下，等待用户回答的段落互斥
        self._approval_lock = asyncio.Lock()

    async def before_execute(
        self, tool_call: ToolCall, definition: ToolDefinition
    ) -> HookVerdict:
        """对一次工具调用执行完整裁决流水线（ExecutionHook 签名）"""
        tags = definition.permission_required or []
        risk = classify_risk(tags)
        uses_network = any(is_network_tag(tag) for tag in tags)

        # 1. 命令护栏：硬阻断在任何模式下直接拒绝，不走审批
        flagged: List[FlaggedCommand] = []
        referenced: List[str] = []
        unparsable = False
        for command in _extract_commands(tool_call, definition):
            report = analyze(command, self._dialect)
            if report.blocked:
                reason = _block_reason(report.flagged)
                self._events.emit(
                    EventType.DENIAL_REQUEST,
                    f"命令被安全护栏硬阻断：{reason}",
                    tool_name=definition.name,
                )
                return HookVerdict.deny(f"命令被安全护栏硬阻断：{reason}")
            flagged.extend(report.flagged)
            referenced.extend(report.referenced_paths)
            unparsable = unparsable or report.unparsable

        # 2. 路径校验：schema 路径参数 + 护栏提取的 cd/重定向目标
        path_report = validate_call_paths(tool_call, definition, self._root)
        outside_paths = _outside_paths(tool_call, path_report.locations)
        for raw in referenced:
            resolved = resolve_in_root(self._root, raw)
            if resolved is None or not resolved.within_root:
                outside_paths.append(raw)
        outside_paths = list(dict.fromkeys(outside_paths))  # 保序去重

        # 3. decide：模式 × 风险 × 触发器
        triggers = []
        if outside_paths:
            triggers.append("涉及工作区外的文件")
        if uses_network:
            triggers.append("将访问网络")
        verdict = decide(self._mode, risk, triggers, unparsable=unparsable)
        self._events.emit(
            EventType.PERMISSION_CHECK,
            f"{definition.name} → {verdict.decision.value}",
            tool_name=definition.name,
            decision=verdict.decision.value,
        )

        # 4. 审批：ASK_USER 经 Approver 询问用户，回答收敛为终态
        if verdict.decision is not HookDecision.ASK_USER:
            return verdict
        if self._approver is None:
            return HookVerdict.deny(f"{verdict.reason}（审批通道未接入，按拒绝处理）")
        request = ApprovalRequest(
            tool_name=definition.name,
            reason=verdict.reason,
            tool_input=dict(tool_call.input),
            outside_paths=outside_paths,
            flagged=flagged,
        )
        self._events.emit(
            EventType.APPROVAL_REQUEST, verdict.reason, tool_name=definition.name
        )
        async with self._approval_lock:
            approved = await self._approver.approve(request)
        if approved:
            return HookVerdict.allow()
        self._events.emit(
            EventType.DENIAL_REQUEST,
            f"用户拒绝：{verdict.reason}",
            tool_name=definition.name,
        )
        return HookVerdict.deny(f"用户拒绝了该操作：{verdict.reason}")


def _extract_commands(tool_call: ToolCall, definition: ToolDefinition) -> List[str]:
    """按 schema 的 format == "command" 标注提取命令串

    与 format == "path" 同款声明式约定：工具自己声明哪个参数是 shell
    命令，新增 shell 类工具无需改动 security。
    """
    commands: List[str] = []
    for name, prop in (definition.input_schema.properties or {}).items():
        if prop.get("format") == "command":
            value = tool_call.input.get(name)
            if isinstance(value, str) and value:
                commands.append(value)
    return commands


def _outside_paths(
    tool_call: ToolCall, locations: Dict[str, PathLocation]
) -> List[str]:
    """从 PathReport 的位置判定中收集界外路径原文（含 INVALID，fail-closed）"""
    return [
        str(tool_call.input.get(param, param))
        for param, location in locations.items()
        if location is not PathLocation.INSIDE
    ]


def _block_reason(flagged: List[FlaggedCommand]) -> str:
    """从命中明细中取第一条硬阻断规则的理由"""
    for item in flagged:
        if item.verdict is RuleVerdict.BLOCK:
            return item.reason
    return "命中危险命令规则"
