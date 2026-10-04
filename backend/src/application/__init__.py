"""application 包：组合根（应用的组装入口）

- config.py：环境变量 → ModelConfig / 会话权限模式
- app.py：create_app() 组合根 + Application 门面
- approval.py：CLI 审批通道（security.Approver 的终端实现）
- shell_env.py：shell 环境探测（Git Bash 主方言，pwsh 降级）

application 是唯一允许依赖所有功能层的层（见 docs/rules/backend.md）。
"""

from .app import Application, create_app
from .approval import CliApprover
from .config import ConfigError, load_model_config, load_permission_mode
from .shell_env import DetectedShell, detect_shell

__all__ = [
    "Application",
    "CliApprover",
    "ConfigError",
    "DetectedShell",
    "create_app",
    "detect_shell",
    "load_model_config",
    "load_permission_mode",
]
