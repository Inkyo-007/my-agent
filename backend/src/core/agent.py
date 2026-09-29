"""智能体定义

Agent 是声明式的定义对象：身份（id、名称、描述）+ 循环配置
（system_prompt、max_steps）。它是 UI 列表、orchestration 多 Agent
管理与 runtime 主循环的公共单元。

边界：模型身份与推理参数不在此——Provider 构造时已由 ModelConfig
锁定，双数据源只会引入不一致。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class AgentState(str, Enum):
    """Agent 的运行状态（由 runtime 主循环迁移）

    只保留有真实迁移时机的状态；暂停/恢复等能力落地时再补回。
    """

    IDLE = "idle"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Agent:
    """Agent 的声明式定义：身份 + 循环配置"""

    agent_id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    max_steps: int = 50
    metadata: Dict[str, Any] = field(default_factory=dict)
