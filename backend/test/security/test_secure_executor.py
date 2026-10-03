"""security/secure_executor.py 的离线单元测试

覆盖：decide 裁决矩阵、三模式下的完整流水线、硬阻断绕过审批、
界外路径触发与理由文案、unparsable 升级、审批通道行为（放行/拒绝/
未接入/串行化）、事件序列。
"""

import asyncio
from pathlib import Path
from typing import List

from fakes import CollectingBus

from src.core import (
    EventType,
    HookDecision,
    ToolCall,
    ToolDefinition,
    ToolInputSchema,
)
from src.security import (
    ApprovalRequest,
    PermissionMode,
    RiskLevel,
    SecureExecutor,
    ShellDialect,
    decide,
)

BASH = ShellDialect.BASH


def _definition(
    name: str,
    tags: list[str],
    path_params: list[str] | None = None,
    command_params: list[str] | None = None,
) -> ToolDefinition:
    properties = {}
    for param in path_params or []:
        properties[param] = {"type": "string", "format": "path"}
    for param in command_params or []:
        properties[param] = {"type": "string", "format": "command"}
    return ToolDefinition(
        name=name,
        description="测试工具",
        input_schema=ToolInputSchema(properties=properties),
        permission_required=tags,
    )


READ_TOOL = _definition("read_file", ["fs:read"], path_params=["path"])
WRITE_TOOL = _definition("write_file", ["fs:write"], path_params=["path"])
SHELL_TOOL = _definition(
    "shell_command", ["shell:execute"], path_params=["cwd"], command_params=["command"]
)
NET_TOOL = _definition("web_fetch", ["net:fetch"])


def _call(name: str, params: dict) -> ToolCall:
    return ToolCall(id="c1", name=name, input=params)


class FakeApprover:
    """预设回答的审批通道；记录收到的请求"""

    def __init__(self, answer: bool):
        self.answer = answer
        self.requests: List[ApprovalRequest] = []

    async def approve(self, request: ApprovalRequest) -> bool:
        self.requests.append(request)
        return self.answer


def _executor(
    mode: PermissionMode,
    root: Path,
    approver: FakeApprover | None = None,
    bus: CollectingBus | None = None,
) -> SecureExecutor:
    return SecureExecutor(
        mode=mode,
        workspace_root=root,
        approver=approver,
        dialect=BASH,
        event_bus=bus,
    )


class Test裁决矩阵:
    def test_只读模式(self):
        assert (
            decide(PermissionMode.READ_ONLY, RiskLevel.SAFE).decision
            is HookDecision.ALLOW
        )
        assert (
            decide(PermissionMode.READ_ONLY, RiskLevel.MUTATING).decision
            is HookDecision.DENY
        )
        assert (
            decide(PermissionMode.READ_ONLY, RiskLevel.DANGEROUS).decision
            is HookDecision.DENY
        )

    def test_请求询问模式(self):
        assert decide(PermissionMode.ASK, RiskLevel.SAFE).decision is HookDecision.ALLOW
        assert (
            decide(PermissionMode.ASK, RiskLevel.MUTATING).decision
            is HookDecision.ALLOW
        )
        assert (
            decide(PermissionMode.ASK, RiskLevel.DANGEROUS).decision
            is HookDecision.ASK_USER
        )
        assert (
            decide(PermissionMode.ASK, RiskLevel.MUTATING, ["将访问网络"]).decision
            is HookDecision.ASK_USER
        )

    def test_完全访问模式(self):
        assert (
            decide(PermissionMode.FULL_ACCESS, RiskLevel.DANGEROUS).decision
            is HookDecision.ALLOW
        )
        # 无法解析的命令在任何便利模式下都不得自动放行
        assert (
            decide(
                PermissionMode.FULL_ACCESS, RiskLevel.DANGEROUS, unparsable=True
            ).decision
            is HookDecision.ASK_USER
        )


class Test只读模式流水线:
    async def test_工作区内读取放行(self, tmp_path: Path):
        executor = _executor(PermissionMode.READ_ONLY, tmp_path)
        verdict = await executor.before_execute(
            _call("read_file", {"path": "a.txt"}), READ_TOOL
        )
        assert verdict.decision is HookDecision.ALLOW

    async def test_写入拒绝(self, tmp_path: Path):
        executor = _executor(PermissionMode.READ_ONLY, tmp_path)
        verdict = await executor.before_execute(
            _call("write_file", {"path": "a.txt"}), WRITE_TOOL
        )
        assert verdict.decision is HookDecision.DENY
        assert "只读模式" in verdict.reason

    async def test_硬阻断优先于模式(self, tmp_path: Path):
        # rm -rf / 即使在完全访问下也直接拒绝，且不打扰审批通道
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.FULL_ACCESS, tmp_path, approver)
        verdict = await executor.before_execute(
            _call("shell_command", {"command": "rm -rf /"}), SHELL_TOOL
        )
        assert verdict.decision is HookDecision.DENY
        assert "硬阻断" in verdict.reason
        assert approver.requests == []


class Test请求询问模式流水线:
    async def test_工作区内写入放行(self, tmp_path: Path):
        executor = _executor(PermissionMode.ASK, tmp_path)
        verdict = await executor.before_execute(
            _call("write_file", {"path": "a.txt"}), WRITE_TOOL
        )
        assert verdict.decision is HookDecision.ALLOW

    async def test_界外写入触发询问并指名路径(self, tmp_path: Path):
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.ASK, tmp_path, approver)
        verdict = await executor.before_execute(
            _call("write_file", {"path": "../secret.txt"}), WRITE_TOOL
        )
        assert verdict.decision is HookDecision.ALLOW  # 用户放行后收敛为 ALLOW
        assert approver.requests[0].outside_paths == ["../secret.txt"]
        assert "工作区外" in approver.requests[0].reason

    async def test_用户拒绝(self, tmp_path: Path):
        approver = FakeApprover(answer=False)
        executor = _executor(PermissionMode.ASK, tmp_path, approver)
        verdict = await executor.before_execute(
            _call("write_file", {"path": "../secret.txt"}), WRITE_TOOL
        )
        assert verdict.decision is HookDecision.DENY
        assert "用户拒绝" in verdict.reason

    async def test_网络工具触发询问(self, tmp_path: Path):
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.ASK, tmp_path, approver)
        verdict = await executor.before_execute(_call("web_fetch", {}), NET_TOOL)
        assert verdict.decision is HookDecision.ALLOW
        assert "网络" in approver.requests[0].reason

    async def test_高危命令触发询问并携带护栏明细(self, tmp_path: Path):
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.ASK, tmp_path, approver)
        await executor.before_execute(
            _call("shell_command", {"command": "rm -rf ./build"}), SHELL_TOOL
        )
        assert any(f.rule_name == "rm-recursive" for f in approver.requests[0].flagged)

    async def test_护栏提取的界外重定向目标触发询问(self, tmp_path: Path):
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.ASK, tmp_path, approver)
        await executor.before_execute(
            _call("shell_command", {"command": "echo x > ../out.txt"}), SHELL_TOOL
        )
        assert "../out.txt" in approver.requests[0].outside_paths

    async def test_审批通道未接入时按拒绝处理(self, tmp_path: Path):
        executor = _executor(PermissionMode.ASK, tmp_path, approver=None)
        verdict = await executor.before_execute(
            _call("shell_command", {"command": "ls"}), SHELL_TOOL
        )
        assert verdict.decision is HookDecision.DENY
        assert "审批通道未接入" in verdict.reason


class Test完全访问模式流水线:
    async def test_界外写入放行(self, tmp_path: Path):
        executor = _executor(PermissionMode.FULL_ACCESS, tmp_path)
        verdict = await executor.before_execute(
            _call("write_file", {"path": "../x.txt"}), WRITE_TOOL
        )
        assert verdict.decision is HookDecision.ALLOW

    async def test_无法解析的命令升级为询问(self, tmp_path: Path):
        approver = FakeApprover(answer=True)
        executor = _executor(PermissionMode.FULL_ACCESS, tmp_path, approver)
        verdict = await executor.before_execute(
            _call("shell_command", {"command": "echo 'unbalanced"}), SHELL_TOOL
        )
        assert verdict.decision is HookDecision.ALLOW  # 经审批放行
        assert "无法解析" in approver.requests[0].reason


class Test审批串行化:
    async def test_并行调用下审批互斥(self, tmp_path: Path):
        class SlowApprover:
            def __init__(self):
                self.active = 0
                self.max_active = 0

            async def approve(self, request: ApprovalRequest) -> bool:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                return True

        approver = SlowApprover()
        executor = SecureExecutor(
            mode=PermissionMode.ASK,
            workspace_root=tmp_path,
            approver=approver,
            dialect=BASH,
        )
        await asyncio.gather(
            executor.before_execute(
                _call("shell_command", {"command": "ls"}), SHELL_TOOL
            ),
            executor.before_execute(
                _call("shell_command", {"command": "pwd"}), SHELL_TOOL
            ),
        )
        assert approver.max_active == 1


class Test事件:
    async def test_放行时发射PERMISSION_CHECK(self, tmp_path: Path):
        bus = CollectingBus()
        executor = _executor(PermissionMode.ASK, tmp_path, bus=bus)
        await executor.before_execute(
            _call("write_file", {"path": "a.txt"}), WRITE_TOOL
        )
        assert [e.type for e in bus.events] == [EventType.PERMISSION_CHECK]
        assert bus.events[0].source == "secure_executor"
        assert bus.events[0].metadata["decision"] == "allow"

    async def test_询问流程的事件序列(self, tmp_path: Path):
        bus = CollectingBus()
        approver = FakeApprover(answer=False)
        executor = _executor(PermissionMode.ASK, tmp_path, approver, bus)
        await executor.before_execute(
            _call("write_file", {"path": "../x.txt"}), WRITE_TOOL
        )
        assert [e.type for e in bus.events] == [
            EventType.PERMISSION_CHECK,
            EventType.APPROVAL_REQUEST,
            EventType.DENIAL_REQUEST,
        ]

    async def test_硬阻断发射DENIAL_REQUEST(self, tmp_path: Path):
        bus = CollectingBus()
        executor = _executor(PermissionMode.FULL_ACCESS, tmp_path, bus=bus)
        await executor.before_execute(
            _call("shell_command", {"command": "rm -rf /"}), SHELL_TOOL
        )
        assert [e.type for e in bus.events] == [EventType.DENIAL_REQUEST]
