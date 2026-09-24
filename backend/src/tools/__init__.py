"""tools 包：工具子系统（机制层）

- registry.py：ToolRegistry——工具的注册、校验与查找
- validator.py：机械校验（存在性、参数 schema），纯函数
- executor.py：ToolExecutor——执行流水线（校验→钩子→执行→包装结果）
- builtin/：（待 M2）预装工具——只是注册进 ToolRegistry 的内容，地位与
  将来 MCP 适配来的、用户自加的工具平等（见 docs/architecture.md D14）
"""
from .executor import ToolExecutor
from .registry import ToolRegistry

__all__ = ["ToolExecutor", "ToolRegistry"]
