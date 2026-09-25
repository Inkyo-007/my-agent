"""工具注册表：Tool 实例的注册、校验与查找

职责边界：只做「存与取」。执行语义（超时控制、钩子链、事件发射）
属于 executor.py，不在此处。
"""

from typing import Dict, List

from ..core import Tool, ToolDefinition, ToolInputSchema


class ToolRegistry:
    """工具注册表：name → Tool 实例"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具，注册时校验 definition 完整性
        （兑现 core/tool.py 中「在工具注册时进行检测」的预留意图）

        Raises:
            ValueError: definition 缺失或不合法、name/description 为空、
                input_schema 类型错误、同名重复注册
        """
        definition = getattr(tool, "definition", None)
        if not isinstance(definition, ToolDefinition):
            raise ValueError(
                f"工具 {type(tool).__name__} 缺少合法的 definition（ToolDefinition 实例）"
            )
        if not definition.name:
            raise ValueError(f"工具 {type(tool).__name__} 的 definition.name 不能为空")
        if not definition.description:
            raise ValueError(
                f"工具 {definition.name} 的 definition.description 不能为空"
            )
        if not isinstance(definition.input_schema, ToolInputSchema):
            raise ValueError(
                f"工具 {definition.name} 的 input_schema 必须是 ToolInputSchema 实例"
            )
        if definition.name in self._tools:
            # 禁止静默覆盖：配置错误应当暴露，而非被后注册者悄悄顶替
            raise ValueError(f"工具 {definition.name} 已注册，禁止重复注册")

        self._tools[definition.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名查找，未找到返回 None

        不抛异常：模型幻觉出不存在的工具名是常态，由 executor 包装为
        ToolResult(success=False, error_type="tool_not_found") 喂回模型自我纠正。
        """
        return self._tools.get(name)

    def get_schema(self, name: str) -> ToolInputSchema | None:
        """按名获取工具的输入 schema，未找到返回 None"""
        tool = self.get(name)
        if tool is None:
            return None
        return tool.definition.input_schema

    def list_tools(self) -> List[Tool]:
        """全部已注册工具（供 provider 的 tools 参数使用）"""
        return list(self._tools.values())
