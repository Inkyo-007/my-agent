"""core/tool.py 的离线单元测试

覆盖：ToolDefinition 权限标签的格式校验（构造期 fail-fast）。
"""

import pytest

from src.core import ToolDefinition, ToolInputSchema


def _make_definition(permission_required: list[str] | None) -> ToolDefinition:
    """构造一个仅权限标签不同的 ToolDefinition"""
    return ToolDefinition(
        name="demo",
        description="演示工具",
        input_schema=ToolInputSchema(),
        permission_required=permission_required,
    )


class Test权限标签格式校验:
    def test_合法标签通过校验(self):
        definition = _make_definition(
            ["fs:read", "fs:write", "shell:execute", "net:fetch_v2"]
        )
        assert definition.permission_required == [
            "fs:read",
            "fs:write",
            "shell:execute",
            "net:fetch_v2",
        ]

    def test_None归一化为空列表(self):
        assert _make_definition(None).permission_required == []

    def test_空列表合法(self):
        assert _make_definition([]).permission_required == []

    @pytest.mark.parametrize(
        "tag",
        [
            "fsread",  # 缺少冒号
            "fs:",  # 缺少动作
            ":read",  # 缺少领域
            "FS:read",  # 大写
            "fs:READ",  # 大写动作
            "fs:read file",  # 含空格
            "fs:read:all",  # 多于一段
            "",  # 空字符串
        ],
    )
    def test_非法标签构造期抛错(self, tag: str):
        with pytest.raises(ValueError, match="非法权限标签"):
            _make_definition([tag])

    def test_混合列表中任一非法即抛错(self):
        with pytest.raises(ValueError, match="非法权限标签"):
            _make_definition(["fs:read", "bad tag"])
