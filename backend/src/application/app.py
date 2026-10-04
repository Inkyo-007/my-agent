"""application 组合根：创建各层实例并互相连接

application 是唯一了解全局接线的地方（见 docs/design/backend/application.md）：
各功能层只依赖 core 端口，具体实现的创建与连接集中在 create_app()。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..core import Agent, BaseProvider, Tool
from ..models import ModelConfig, create_provider
from ..runtime import AgentLoop, InMemoryEventBus
from ..security import Approver, PermissionMode, SecureExecutor, ShellDialect
from ..tools import ToolExecutor, ToolRegistry


@dataclass
class Application:
    """组装完成的应用实例

    只暴露外部消费者需要的入口，不包业务逻辑：
    - loop：Agent 主循环，run() 驱动一次任务，history 挂在其上
    - bus：事件总线，UI / 日志 / 审计以订阅者身份接入
    """

    loop: AgentLoop
    bus: InMemoryEventBus


def create_app(
    config: ModelConfig,
    agent: Agent | None = None,
    tools: List[Tool] | None = None,
    provider: BaseProvider | None = None,
    workspace_root: Path | None = None,
    permission_mode: PermissionMode = PermissionMode.ASK,
    approver: Approver | None = None,
    shell_dialect: ShellDialect = ShellDialect.BASH,
) -> Application:
    """按依赖方向组装应用：bus → registry/executor（挂安全钩子）→ provider → loop

    Args:
        config: 模型配置（由 application/config.py 从 .env 加载）
        agent: Agent 定义；None 使用默认助手定义
        tools: 预装工具列表；None 表示无工具
        provider: 模型 Provider；None 时按 config 创建
            （测试可注入脚本化实现，保持离线）
        workspace_root: 工作区根目录；提供时接入 SecureExecutor 安全钩子，
            None 表示空钩子链（全部放行，仅供无安全需求的测试）
        permission_mode: 会话权限模式（默认请求询问）
        approver: 审批通道；None 时 ASK_USER 裁决 fail-closed 按拒绝处理
        shell_dialect: 命令护栏的分析方言，应与 shell 工具的解释器一致
            （由 application/shell_env.detect_shell 探测）
    """
    bus = InMemoryEventBus()

    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)

    hooks = []
    if workspace_root is not None:
        secure = SecureExecutor(
            mode=permission_mode,
            workspace_root=workspace_root,
            approver=approver,
            dialect=shell_dialect,
            event_bus=bus,
        )
        hooks.append(secure.before_execute)
    executor = ToolExecutor(registry, hooks=hooks, event_bus=bus)

    loop = AgentLoop(
        agent=agent
        or Agent(
            agent_id="assistant",
            name="助手",
            system_prompt="你是一个乐于助人的助手。",
        ),
        provider=provider or create_provider(config),
        executor=executor,
        tools=registry.list_tools(),
        event_bus=bus,
    )
    return Application(loop=loop, bus=bus)
