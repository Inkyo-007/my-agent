"""CLI 审批通道：在终端向用户展示审批请求并等待裁决

实现 security 的 Approver 端口（async approve(ApprovalRequest) -> bool）。
渲染审批必须指名道姓——工具名、裁决理由、界外路径、护栏命中明细全部
展示，用户才能做知情决定。v1 的回答只有放行/拒绝二值（y/N）。
"""

import asyncio
from typing import Callable

from ..security import ApprovalRequest


class CliApprover:
    """终端审批：打印请求明细，阻塞读取 y/n

    IO 函数可注入（测试用脚本化输入保持离线）；input 是阻塞调用，
    经 asyncio.to_thread 放到 worker 线程，不阻塞事件循环——并行工具
    调用的其他审批请求仍可排队（串行化由 SecureExecutor 的锁保证）。
    """

    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        print_func: Callable[..., None] = print,
    ):
        self._input = input_func
        self._print = print_func

    async def approve(self, request: ApprovalRequest) -> bool:
        return await asyncio.to_thread(self._ask, request)

    def _ask(self, request: ApprovalRequest) -> bool:
        show = self._print
        show("!" * 60)
        show(f"  审批请求：{request.tool_name}")
        show(f"  理由：{request.reason}")
        for name, value in request.tool_input.items():
            show(f"  参数 {name}：{_preview(value)}")
        for path in request.outside_paths:
            show(f"  界外路径：{path}")
        for item in request.flagged:
            show(f"  护栏提示[{item.rule_name}]：{item.reason}（{item.subcommand}）")
        show("!" * 60)
        answer = self._input("允许执行？[y/N] ").strip().lower()
        return answer in ("y", "yes")


# 参数值的展示上限：write_file 的 content 这类大值只做预览，
# 命令这类关键值通常远短于此，会完整展示
_PREVIEW_MAX = 300


def _preview(value: object) -> str:
    """参数值的单行预览：换行转义，超长截断并标注"""
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\n", "\\n")
    if len(text) > _PREVIEW_MAX:
        return text[:_PREVIEW_MAX] + f"…（共 {len(text)} 字符，已截断）"
    return text
