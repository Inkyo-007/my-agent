"""security/guardrails.py 的离线单元测试

覆盖：规则表 match/not_match 自测、拆链与分词（引号/转义/方言差异）、
argv[0] 规范化与 pwsh 别名、硬阻断与标注、shell -c 递归、内联解释器、
未分析结构、路径提取、unparsable 兜底。
"""

import shlex

import pytest

from src.security import RULES, ShellDialect, analyze

BASH = ShellDialect.BASH
PWSH = ShellDialect.PWSH


def _rule_ids(rule):
    return f"{rule.name}:{' '.join(rule.pattern)}"


class Test规则自测:
    """每条规则的 match / not_match 样例逐条校验（防规则表腐烂）"""

    @pytest.mark.parametrize("rule", RULES, ids=_rule_ids)
    def test_match样例命中(self, rule):
        for dialect in rule.dialects:
            for example in rule.match:
                report = analyze(example, dialect)
                assert any(f.rule_name == rule.name for f in report.flagged), (
                    f"{rule.name} 应命中 {example!r}"
                )

    @pytest.mark.parametrize("rule", RULES, ids=_rule_ids)
    def test_not_match样例不命中(self, rule):
        for dialect in rule.dialects:
            for example in rule.not_match:
                report = analyze(example, dialect)
                assert not any(f.rule_name == rule.name for f in report.flagged), (
                    f"{rule.name} 不应命中 {example!r}"
                )


class Test硬阻断:
    def test_递归删除根目录(self):
        report = analyze("rm -rf /", BASH)
        assert report.blocked is True
        assert any(f.rule_name == "rm-root" for f in report.flagged)

    def test_工作区内删除不阻断但标注(self):
        report = analyze("rm -rf ./build", BASH)
        assert report.blocked is False
        assert any(f.rule_name == "rm-recursive" for f in report.flagged)

    def test_链式中任一子命令命中即整链阻断(self):
        report = analyze("ls -la && mkfs /dev/sda", BASH)
        assert report.blocked is True

    def test_引号内的运算符不拆分不误报(self):
        report = analyze("echo 'a && b; mkfs /dev/sda'", BASH)
        assert report.blocked is False
        assert report.flagged == []

    def test_绝对路径与exe后缀同样命中(self):
        assert analyze("/sbin/mkfs /dev/sda", BASH).blocked is True
        assert analyze(r"C:\Windows\format.com C:", PWSH).blocked is True

    def test_pwsh关机命令(self):
        assert analyze("Stop-Computer", PWSH).blocked is True

    def test_pwsh别名归一化后命中(self):
        # rm 是 Remove-Item 的别名；alias 规范化后命中递归删除标注
        report = analyze("rm -Recurse build", PWSH)
        assert any(f.rule_name == "remove-item-recurse" for f in report.flagged)


class Test递归分析:
    def test_bash_c内层命令被分析(self):
        report = analyze("bash -c 'rm -rf /'", BASH)
        assert report.blocked is True

    def test_外层方言中调用pwsh(self):
        report = analyze("pwsh -Command 'Stop-Computer'", BASH)
        assert report.blocked is True

    def test_pwsh中调用bash(self):
        report = analyze("bash -c 'rm -rf /'", PWSH)
        assert report.blocked is True

    def test_浅层嵌套仍被分析(self):
        command = "rm -rf /"
        for _ in range(2):
            command = f"bash -c {shlex.quote(command)}"
        assert analyze(command, BASH).blocked is True

    def test_嵌套层级限深(self):
        command = "rm -rf /"
        for _ in range(6):
            command = f"bash -c {shlex.quote(command)}"
        report = analyze(command, BASH)
        # 深度超限后内层不再展开：不阻断，但如实标注
        assert report.blocked is False
        assert any(f.rule_name == "nested-shell" for f in report.flagged)


class Test内联解释器与未分析结构:
    def test_python_c标注但不阻断(self):
        report = analyze("python -c 'import os'", BASH)
        assert report.blocked is False
        assert any(f.rule_name == "inline-interpreter" for f in report.flagged)

    def test_命令替换标注(self):
        report = analyze("echo $(whoami)", BASH)
        assert any(f.rule_name == "unanalyzed-construct" for f in report.flagged)

    def test_单引号内的命令替换是字面量(self):
        report = analyze("echo '$(whoami)'", BASH)
        assert report.flagged == []

    def test_pwsh反引号是转义符不是命令替换(self):
        report = analyze('echo "a`$b"', PWSH)
        assert not any(f.rule_name == "unanalyzed-construct" for f in report.flagged)

    def test_控制流标注(self):
        report = analyze("for f in *.log; do rm $f; done", BASH)
        assert any(f.rule_name == "control-flow" for f in report.flagged)


class Test路径提取:
    def test_cd目标(self):
        report = analyze("cd /etc && ls", BASH)
        assert "/etc" in report.referenced_paths

    def test_重定向目标(self):
        report = analyze("echo x > /etc/important", BASH)
        assert "/etc/important" in report.referenced_paths

    def test_附着形式的重定向与设备文件忽略(self):
        report = analyze("cmd 2>/dev/null > out.txt", BASH)
        assert report.referenced_paths == ["out.txt"]

    def test_文件描述符形式忽略(self):
        report = analyze("cmd 2>&1", BASH)
        assert report.referenced_paths == []

    def test_pwsh的Windows路径保留反斜杠(self):
        report = analyze(r"echo x > C:\temp\out.txt", PWSH)
        assert r"C:\temp\out.txt" in report.referenced_paths

    def test_pwsh的cd目标(self):
        report = analyze(r"cd C:\Windows; ls", PWSH)
        assert r"C:\Windows" in report.referenced_paths


class Test解析失败兜底:
    def test_引号不配对(self):
        report = analyze("echo 'unbalanced", BASH)
        assert report.unparsable is True

    def test_pwsh引号不配对(self):
        report = analyze('echo "unbalanced', PWSH)
        assert report.unparsable is True

    def test_空命令(self):
        report = analyze("", BASH)
        assert report.blocked is False
        assert report.flagged == []
        assert report.unparsable is False
