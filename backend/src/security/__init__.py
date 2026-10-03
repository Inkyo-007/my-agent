"""security 层：工具执行前的安全策略（权限决策、路径校验、命令护栏）

只依赖 core；三个子层由执行器集成组装后以执行钩子的形式接入 tools 层
（见 tools/executor.py）。
"""

from .guardrails import CommandReport, FlaggedCommand, analyze
from .guardrails_rules import RULES, CommandRule, RuleVerdict, ShellDialect
from .path_validator import (
    PathLocation,
    PathReport,
    ResolvedPath,
    resolve_in_root,
    validate_call_paths,
)
from .permissions import (
    DEFAULT_RISK,
    PermissionMode,
    RiskLevel,
    classify_risk,
    is_network_tag,
)
from .secure_executor import (
    ApprovalRequest,
    Approver,
    SecureExecutor,
    decide,
)

__all__ = [
    "RULES",
    "CommandReport",
    "CommandRule",
    "FlaggedCommand",
    "RuleVerdict",
    "ShellDialect",
    "analyze",
    "PathLocation",
    "PathReport",
    "ResolvedPath",
    "resolve_in_root",
    "validate_call_paths",
    "ApprovalRequest",
    "Approver",
    "SecureExecutor",
    "decide",
    "DEFAULT_RISK",
    "PermissionMode",
    "RiskLevel",
    "classify_risk",
    "is_network_tag",
]
