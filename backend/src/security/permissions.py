"""security 层 · 权限决策子层：裁决所需的决策要素

职责边界：只提供决策要素——风险等级、权限模式、标签 → 风险的分类、
网络域判定。不下结论：「这个模式下的这次调用，放行 / 拒绝 / 询问」的
最终裁决（decide）由执行器集成在组装路径校验（paths）、命令护栏
（commands）后实现——完整裁决需要两者的分析结果作为输入。

设计理由见 docs/design/backend/security.md。
"""

from enum import Enum
from typing import Dict, List


class RiskLevel(Enum):
    """工具风险等级（静态分类）

    与权限标签的「领域」不同：领域是开放的字符串（会生长），风险等级是
    封闭的安全语义（不生长），因此可以用枚举。
    """

    SAFE = "safe"  # 只读，无副作用
    MUTATING = "mutating"  # 有副作用（写文件、改状态）
    DANGEROUS = "dangerous"  # 高危（执行命令等）


class PermissionMode(str, Enum):
    """会话权限模式（用户选择）"""

    READ_ONLY = "read_only"  # 只读：仅放行只读操作
    ASK = "ask"  # 请求询问：触发器命中或高危操作时询问用户
    FULL_ACCESS = "full_access"  # 完全访问：全部放行（命令硬阻断除外）


# 权限标签 → 风险等级的映射表。只收录已知域；未知标签由 DEFAULT_RISK 兜底。
_TAG_RISK: Dict[str, RiskLevel] = {
    "fs:read": RiskLevel.SAFE,
    "fs:write": RiskLevel.MUTATING,
    "shell:execute": RiskLevel.DANGEROUS,
}

# 未知标签的兜底风险：fail-closed，按最高警惕处理——我们不认识的操作，
# 只读模式拒绝、ASK 模式必询问。宁可多询问，不可漏拦截。
DEFAULT_RISK = RiskLevel.DANGEROUS

# 风险等级的严重度排序（Enum 不可比大小，显式给序）
_SEVERITY: Dict[RiskLevel, int] = {
    RiskLevel.SAFE: 0,
    RiskLevel.MUTATING: 1,
    RiskLevel.DANGEROUS: 2,
}

# 网络域：该域下的标签一律视为「使用网络」触发器
_NETWORK_DOMAIN = "net"


def classify_risk(tags: List[str]) -> RiskLevel:
    """标签列表 → 最高风险等级

    空列表视为 SAFE（工具显式声明无任何权限需求；tools.md 约定声明要
    保守诚实，漏报等于绕过安全检查）。
    """
    risk = RiskLevel.SAFE
    for tag in tags:
        level = _TAG_RISK.get(tag, DEFAULT_RISK)
        if _SEVERITY[level] > _SEVERITY[risk]:
            risk = level
    return risk


def is_network_tag(tag: str) -> bool:
    """标签是否属于网络域（即「使用网络」触发器）"""
    return tag.split(":", 1)[0] == _NETWORK_DOMAIN
