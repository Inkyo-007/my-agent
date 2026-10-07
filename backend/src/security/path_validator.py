"""security 层 · 路径校验子层：工具调用涉及路径的内/外分类

职责边界：纯分类，不做策略裁决。按 input_schema 中 format == "path" 的
标注提取调用中的路径参数，逐个判定在工作区内/外，输出 PathReport；
「OUTSIDE 该怎么办」（询问还是拒绝）由执行器集成结合权限模式决定。

解析原语（规范化 + resolve）在 core/paths.py：工具执行（tools）与安全
判定（security）必须对同一路径字符串解析出同一个文件，共用一份实现
从机制上消除「审批 A、执行 B」的错位（如 Git Bash 风格的 /d/... 路径，
pathlib 会把它错拼到 root 所在盘下的字母目录）。本层只做「判内外」的分类语义。

执行器集成落地后，fs 工具的硬围栏已移除：工作区外的读写由本层分析、
经审批放行，工具只负责忠实执行。本层使集成处在执行之前就能获得
「该操作涉及工作区外」的事实。

fail-closed：无法解析的路径判 INVALID，消费方按 OUTSIDE 同等对待
（touches_outside 属性已内置这一约定）。

已知边界（详见 docs/design/backend/security.md）：Windows 8.3 短名不
展开、硬链接无法按路径分辨、判定与执行之间的 TOCTOU 竞态。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List

from ..core import ToolCall, ToolDefinition
from ..core import resolve_path as _core_resolve


class PathLocation(Enum):
    """单个路径参数与工作区的位置关系"""

    INSIDE = "inside"  # 工作区内
    OUTSIDE = "outside"  # 工作区外
    INVALID = "invalid"  # 无法解析（空串、超长、含空字节、resolve 异常）


@dataclass
class ResolvedPath:
    """resolve 后的绝对路径及其与工作区的位置关系"""

    path: Path  # resolve 后的绝对路径（符号链接已展开）
    within_root: bool  # 是否在工作区内


@dataclass
class PathReport:
    """一次调用的路径校验结果：参数名 → 位置判定"""

    locations: Dict[str, PathLocation] = field(default_factory=dict)

    @property
    def touches_outside(self) -> bool:
        """是否触及工作区外；INVALID 按 OUTSIDE 处理（fail-closed）"""
        return any(
            location is not PathLocation.INSIDE for location in self.locations.values()
        )


def validate_call_paths(
    tool_call: ToolCall,
    definition: ToolDefinition,
    workspace_root: Path,
) -> PathReport:
    """按 schema 的 format == "path" 标注提取路径参数并分类

    缺失或非字符串的参数跳过——参数合法性由 tools 层的 schema 校验兜底，
    本层只分析确实出现的路径。workspace_root 须预先 resolve。
    """
    locations: Dict[str, PathLocation] = {}
    for param in _path_params(definition):
        value = tool_call.input.get(param)
        if not isinstance(value, str) or not value:
            continue
        locations[param] = _locate(workspace_root, value)
    return PathReport(locations=locations)


def _path_params(definition: ToolDefinition) -> List[str]:
    """从 input_schema 提取标注为路径的参数名（format == "path"）

    format 是 JSON Schema 的标准注解关键字：tools 层校验器不传 format
    checker 时会忽略它（不影响参数校验），security 用它做声明式提取，
    且该标注随 schema 一并发给模型，模型也能知道该参数是路径。
    """
    return [
        name
        for name, prop in (definition.input_schema.properties or {}).items()
        if prop.get("format") == "path"
    ]


def resolve_in_root(root: Path, path: str) -> ResolvedPath | None:
    """将用户路径解析为绝对路径并判定是否在 root 内；None = 路径非法

    解析委托 core.resolve_path（规范化、合法性检查、符号链接展开），
    本函数只做内/外分类：is_relative_to 判定（Windows 不区分大小写），
    不用字符串前缀比较。root 须由调用方预先 resolve（构造期一次性完成，
    避免每次调用的 IO）。
    """
    resolved = _core_resolve(root, path)
    if resolved is None:
        return None
    return ResolvedPath(path=resolved, within_root=resolved.is_relative_to(root))


def _locate(workspace_root: Path, path: str) -> PathLocation:
    """单条路径的内/外判定"""
    resolved = resolve_in_root(workspace_root, path)
    if resolved is None:
        return PathLocation.INVALID
    if resolved.within_root:
        return PathLocation.INSIDE
    return PathLocation.OUTSIDE
