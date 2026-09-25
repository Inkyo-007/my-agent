"""
定义所有工具必须遵守的接口。
无论是读文件、执行命令，还是调用 API，都要实现 Tool 基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolResult:
    """工具结果类"""

    success: bool
    content: str
    execution_time: float
    error_type: str | None = None


@dataclass
class HookVerdict:
    """执行钩子的裁决（security 等策略模块的回答）

    与 ToolResult 同属「工具执行的反馈」家族：
    ToolResult 是执行之后的结果，HookVerdict 是执行之前的放行裁决。

    拦截（allowed=False）是正常业务流程（如用户拒绝），不是异常；
    reason 会被 executor 包装进 ToolResult.content 喂回模型，
    使模型能得体地向用户解释，而非盲目重试。
    """

    allowed: bool
    reason: str = ""


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
