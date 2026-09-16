"""智能体接口定义"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class AgentState(str, Enum):
    """智能体状态"""

    IDLE = "idle"
    INITIALIZING = "initializing"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionResult:
    """执行结果"""

    status: str  # "success", "error", "timeout"
    output: str | None = None
    error: str | None = None
    metadata: Dict[str, Any] | None = None
    # metadata 可以包含额外的执行信息，例如执行时间、token 使用情况等

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class Agent:
    """Agent定义"""

    agent_id: str
    name: str
    description: str
    system_prompt: str
    model_name: str
    max_steps: int = 10
    metadata: Dict[str, Any] | None = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
