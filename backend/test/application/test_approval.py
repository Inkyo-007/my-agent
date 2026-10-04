"""application/approval.py 的单元测试

注入脚本化的 input/print，验证 y/N 裁决映射与审批明细的完整渲染
（审批必须指名道姓：工具名、理由、界外路径、护栏命中）。
"""

from src.application import CliApprover
from src.security import ApprovalRequest


def _approver(answers):
    """构造脚本化审批者：answers 为依次返回的输入；返回 (approver, 输出行)"""
    lines = []
    iterator = iter(answers)
    approver = CliApprover(
        input_func=lambda _prompt: next(iterator),
        print_func=lambda *args: lines.append(" ".join(str(a) for a in args)),
    )
    return approver, lines


class Test裁决映射:
    async def test_y放行(self):
        approver, _ = _approver(["y"])
        assert (
            await approver.approve(ApprovalRequest(tool_name="t", reason="r")) is True
        )

    async def test_yes放行(self):
        approver, _ = _approver(["YES"])
        assert (
            await approver.approve(ApprovalRequest(tool_name="t", reason="r")) is True
        )

    async def test_空输入拒绝(self):
        approver, _ = _approver([""])
        assert (
            await approver.approve(ApprovalRequest(tool_name="t", reason="r")) is False
        )

    async def test_其他输入拒绝(self):
        approver, _ = _approver(["n"])
        assert (
            await approver.approve(ApprovalRequest(tool_name="t", reason="r")) is False
        )


class Test明细渲染:
    async def test_渲染包含全部审批细节(self):
        from src.security.guardrails import FlaggedCommand
        from src.security.guardrails_rules import RuleVerdict

        request = ApprovalRequest(
            tool_name="shell_command",
            reason="该操作涉及工作区外的文件，需要您确认",
            tool_input={"command": "echo x > C:\\important.txt"},
            outside_paths=["C:\\important.txt"],
            flagged=[
                FlaggedCommand(
                    subcommand="git push --force",
                    rule_name="git-push-force",
                    verdict=RuleVerdict.FLAG,
                    reason="强制推送会覆盖远端历史",
                )
            ],
        )
        approver, lines = _approver(["n"])

        await approver.approve(request)

        rendered = "\n".join(lines)
        assert "shell_command" in rendered
        assert "涉及工作区外的文件" in rendered
        # 关键：展示要执行的具体命令，用户才能做知情决定
        assert "echo x > C:\\important.txt" in rendered
        assert "git-push-force" in rendered
        assert "git push --force" in rendered

    async def test_超长参数值截断为单行预览(self):
        big = "x" * 500
        request = ApprovalRequest(
            tool_name="write_file",
            reason="高危",
            tool_input={"path": "a.txt", "content": "第一行\n" + big},
        )
        approver, lines = _approver(["n"])

        await approver.approve(request)

        rendered = "\n".join(lines)
        assert "参数 path：a.txt" in rendered
        # 换行转义为单行，且超长截断并标注
        assert "第一行\\n" in rendered
        assert "已截断" in rendered
        assert big not in rendered
