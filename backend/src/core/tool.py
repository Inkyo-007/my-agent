"""
定义所有工具必须遵守的接口。
无论是读文件、执行命令，还是调用 API，都要实现 Tool 基类。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

# 权限标签的语法契约："{领域}:{动作}"，小写字母/数字/下划线。
# 只约束形式，不枚举领域——领域会不断生长，语法不会（见 docs/design/backend/core.md）。
_PERMISSION_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")


@dataclass
class ToolResult:
    """工具结果类"""

    success: bool
    content: str
    execution_time: float
    error_type: str | None = None


class HookDecision(str, Enum):
    """执行钩子裁决的种类

    ASK_USER 是策略层的中间裁决：审批通道（如何询问用户、等待回答）由
    security 在钩子内部解决，裁决到达 executor 时应已收敛为 ALLOW 或 DENY；
    若 ASK_USER 泄漏到 executor，executor 按拒绝兜底（fail-closed）。
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"


@dataclass
class HookVerdict:
    """执行钩子的裁决（security 等策略模块的回答）

    与 ToolResult 同属「工具执行的反馈」家族：
    ToolResult 是执行之后的结果，HookVerdict 是执行之前的放行裁决。

    拦截（DENY）是正常业务流程（如用户拒绝），不是异常；
    reason 会被 executor 包装进 ToolResult.content 喂回模型，
    使模型能得体地向用户解释，而非盲目重试。
    """

    decision: HookDecision
    reason: str = ""

    @classmethod
    def allow(cls) -> "HookVerdict":
        """放行"""
        return cls(decision=HookDecision.ALLOW)

    @classmethod
    def deny(cls, reason: str = "") -> "HookVerdict":
        """拦截；reason 说明理由（会喂回模型）"""
        return cls(decision=HookDecision.DENY, reason=reason)

    @classmethod
    def ask_user(cls, reason: str = "") -> "HookVerdict":
        """需用户裁决；reason 说明需要审批的原因"""
        return cls(decision=HookDecision.ASK_USER, reason=reason)


@dataclass
class ToolCall:
    """模型发出的工具调用（跨 Provider 统一格式）

    Provider 层解析模型响应得到 ToolCall，agent 循环据此查找并执行工具，
    执行结果包装为 ToolResult。
    """

    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolInputSchema:
    """工具输入参数模式类"""

    type: str = "object"
    properties: Dict[str, Dict[str, Any]] | None = None
    required: List[str] | None = None

    def __post_init__(self):
        if self.properties is None:
            self.properties = {}
        if self.required is None:
            self.required = []


@dataclass
class ToolDefinition:
    """工具定义类"""

    name: str
    description: str
    input_schema: ToolInputSchema
    permission_required: List[str] | None = None
    timeout_seconds: float = 30

    def __post_init__(self):
        if self.permission_required is None:
            self.permission_required = []
        for tag in self.permission_required:
            if not _PERMISSION_TAG_RE.fullmatch(tag):
                raise ValueError(
                    f"非法权限标签：{tag!r}（应为 '领域:动作' 小写格式，如 fs:read）"
                )


class Tool(ABC):
    """工具抽象基类"""

    definition: ToolDefinition
    # 工具必须包含一个 ToolDefinition 对象，但在此处不进行检测，而在工具注册时进行检测。

    @abstractmethod
    async def call(self, params: Dict[str, Any]) -> ToolResult:
        """调用工具方法"""
        pass

    def get_basic_definition_dict(self) -> Dict[str, Any]:
        """获取工具基础定义的字典表示，用于展示给模型"""
        return {
            "name": self.definition.name,
            "description": self.definition.description,
            "input_schema": {
                "type": self.definition.input_schema.type,
                "properties": self.definition.input_schema.properties,
                "required": self.definition.input_schema.required,
            },
        }

    def get_definition_dict(self) -> Dict[str, Any]:
        """获取工具完整定义的字典表示"""
        return {
            "name": self.definition.name,
            "description": self.definition.description,
            "input_schema": {
                "type": self.definition.input_schema.type,
                "properties": self.definition.input_schema.properties,
                "required": self.definition.input_schema.required,
            },
            "permission_required": self.definition.permission_required,
            "timeout_seconds": self.definition.timeout_seconds,
        }


class BaseToolExecutor(ABC):
    """工具执行器契约（端口）：ToolCall → ToolResult 的抽象

    runtime 等编排层只依赖本接口，不认识 tools 层的具体实现。
    契约只承诺一件事：执行一次工具调用并返回结果。
    流水线细节（校验、钩子链、超时、事件发射）是实现自己的事，
    具体实现见 tools/executor.py 的 ToolExecutor。
    """

    @abstractmethod
    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """执行一次工具调用，永远返回 ToolResult，不向调用方抛异常"""
        pass
