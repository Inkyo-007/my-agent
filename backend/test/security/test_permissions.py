"""security/permissions.py 的离线单元测试

覆盖：风险分类（已知/未知兜底/多标签取最高/空列表）、网络域判定。
最终裁决 decide 由执行器集成实现后另行覆盖。
"""

from src.security import (
    DEFAULT_RISK,
    RiskLevel,
    classify_risk,
    is_network_tag,
)


class Test风险分类:
    def test_已知标签(self):
        assert classify_risk(["fs:read"]) == RiskLevel.SAFE
        assert classify_risk(["fs:write"]) == RiskLevel.MUTATING
        assert classify_risk(["shell:execute"]) == RiskLevel.DANGEROUS

    def test_未知标签按默认风险兜底(self):
        assert classify_risk(["net:fetch"]) == DEFAULT_RISK

    def test_多标签取最高风险(self):
        assert classify_risk(["fs:read", "fs:write"]) == RiskLevel.MUTATING
        assert classify_risk(["fs:write", "shell:execute"]) == RiskLevel.DANGEROUS

    def test_空标签视为只读(self):
        assert classify_risk([]) == RiskLevel.SAFE


class Test网络域判定:
    def test_网络域标签(self):
        assert is_network_tag("net:fetch") is True
        assert is_network_tag("net:search") is True

    def test_非网络域标签(self):
        assert is_network_tag("fs:read") is False
        assert is_network_tag("shell:execute") is False
