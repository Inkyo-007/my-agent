"""builtin 子包：预装工具

机制与内容分离：本包只是「内容」，注册与执行机制在 tools 层；
预装工具与将来 MCP 适配来的、用户自加的工具地位平等。
"""

from .fs import ReadFileTool, WriteFileTool

__all__ = ["ReadFileTool", "WriteFileTool"]
