"""最小 CLI 冒烟入口：poe chat（等价于 uv run python -m src.application）

逐行读取输入驱动主循环（流式），模型输出实时流式打印，事件同步展示。
仅用于开发期手动冒烟（需配置 backend/.env），不是产品形态——
Web UI 待前端落地后以订阅事件总线的方式接入。

排版约定：分隔线、增量换行、结果摘要缩进等终端渲染细节全部在订阅者
（_print_event）中处理，runtime / tools 发射的事件内容保持纯净。
"""

import asyncio
from pathlib import Path

from ..core import Event, EventType, MessageRole
from ..security import ShellDialect
from ..tools.builtin import ReadFileTool, ShellCommandTool, WriteFileTool
from .app import create_app
from .approval import CliApprover
from .config import ConfigError, load_model_config, load_permission_mode
from .shell_env import detect_shell

PATH_TO_OWN_TESTS = "D:\\Project\\my-agent\\backend\\own_tests"

# 这些事件之后打印分隔线，划分任务 / 步骤的边界
_SEPARATE_AFTER = {
    EventType.STEP_END,
    EventType.STEP_FAILED,
    EventType.TASK_END,
    EventType.TASK_FAILED,
}

# 是否正处于增量输出的同一行（增量不换行，下个非增量事件前补换行）
_in_delta = False
# 下一段正文增量前是否需要打印 assistant: 头（每个步骤重置，
# 使流式输出与非流式的 assistant:\n<正文> 结构一致）
_text_header_pending = True


def _print_event(event: Event) -> None:
    """打印运行过程事件；排版（分隔线、增量换行、摘要缩进）由订阅者负责"""
    global _in_delta, _text_header_pending

    if event.type == EventType.MODEL_THINKING_DELTA:
        # 思考增量原样流出（淡化等视觉区分待 UI 落地后处理）
        print(event.content, end="", flush=True)
        _in_delta = True
        return
    if event.type == EventType.MODEL_TEXT_DELTA:
        if _text_header_pending:
            # 正文块开始前打印 assistant: 头；若此前正在输出思考增量，
            # 先换行分隔，避免思考与正文混在一行
            if _in_delta:
                print()
            print("assistant:")
            _in_delta = False
            _text_header_pending = False
        print(event.content, end="", flush=True)
        _in_delta = True
        return

    if event.type == EventType.STEP_START:
        _text_header_pending = True
    if _in_delta:
        print()
        _in_delta = False

    if event.type == EventType.TASK_START:
        print("=" * 60)
    print(f"  [{event.type.value}] {event.content}")
    excerpt = event.metadata.get("result_excerpt")
    if excerpt:
        print("    " + str(excerpt).replace("\n", "\n    "))
    if event.type in _SEPARATE_AFTER:
        print("=" * 60)


async def _main() -> None:
    try:
        config = load_model_config()
        mode = load_permission_mode()
    except ConfigError as e:
        print(f"配置错误：{e}")
        return

    root = Path(PATH_TO_OWN_TESTS)
    tools = [ReadFileTool(root), WriteFileTool(root)]
    shell = detect_shell()
    if shell is not None:
        tools.append(ShellCommandTool(shell.program, root))
    else:
        print(
            "未检测到可用的 shell（bash/pwsh/powershell），本次会话无 shell_command 工具"
        )

    app = create_app(
        config=config,
        tools=tools,
        workspace_root=root,
        permission_mode=mode,
        approver=CliApprover(),
        shell_dialect=shell.dialect if shell else ShellDialect.BASH,
    )
    app.bus.subscribe(_print_event)
    shell_desc = shell.program.program if shell else "无"
    print(
        f"my-agent 冒烟终端（模型：{config.model_id}，权限模式：{mode.value}，"
        f"shell：{shell_desc}，流式，空行退出）"
    )

    while True:
        user_input = (await asyncio.to_thread(input, "> ")).strip()
        if not user_input:
            break
        print()

        # 流式开关：True 时回答由增量事件实时打印；False 时收尾统一打印
        use_stream = True
        before = len(app.loop.history.messages)
        result = await app.loop.run(user_input, stream=use_stream)

        if not use_stream:
            # 非流式没有增量事件，收尾时按序打印本轮产生的全部 assistant 正文
            # ——包括工具调用消息（ToolCallMessage）上附带的内容，
            # 只取最后一条会漏掉中间轮的正文
            for message in app.loop.history.messages[before:]:
                if message.role == MessageRole.ASSISTANT and message.content:
                    print(f"assistant:\n{message.content}")

        print(
            f"[{result.status.value}] steps={result.steps} tokens={result.token_used}"
        )
        if result.error:
            print(f"错误：{result.error}")
        print()


if __name__ == "__main__":
    asyncio.run(_main())
