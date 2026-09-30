"""application 包：组合根（应用的组装入口）

- config.py：环境变量 → ModelConfig
- app.py：create_app() 组合根 + Application 门面

application 是唯一允许依赖所有功能层的层（见 docs/rules/backend.md）。
"""

from .app import Application, create_app
from .config import ConfigError, load_model_config

__all__ = [
    "Application",
    "ConfigError",
    "create_app",
    "load_model_config",
]
