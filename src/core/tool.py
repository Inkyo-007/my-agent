"""
定义所有工具必须遵守的接口。
无论是读文件、执行命令，还是调用 API，都要实现 Tool 基类。
"""

from dataclasses import dataclass
from typing import Any, Dict, List
from abc import ABC, abstractmethod

@dataclass
class ToolResult:
    """工具结果类"""

    success: bool
    content: str
    execution_time: float
    error_type: str | None = None

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
    timeout_seconds: int = 30

    def __post_init__(self):
        if self.permission_required is None:
            self.permission_required = []

class Tool(ABC):
    """工具抽象基类"""

    defination: ToolDefinition

    @abstractmethod
    def call(self, params: Dict[str, Any]) -> ToolResult:
        """调用工具方法"""
        pass

    def get_definition_dict(self) -> Dict[str, Any]:
        """获取工具定义的字典表示"""
        return {
            "name": self.defination.name,
            "description": self.defination.description,
            "input_schema": {
                "type": self.defination.input_schema.type,
                "properties": self.defination.input_schema.properties,
                "required": self.defination.input_schema.required,
            },
            "permission_required": self.defination.permission_required,
            "timeout_seconds": self.defination.timeout_seconds,
        }