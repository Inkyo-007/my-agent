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