"""最小 CLI 冒烟入口：poe chat（等价于 uv run python -m src.application）

逐行读取输入驱动主循环，事件与最终回答实时打印。
仅用于开发期手动冒烟（需配置 backend/.env），不是产品形态——
Web UI 待前端落地后以订阅事件总线的方式接入。

排版约定：分隔线、结果摘要缩进等终端渲染细节全部在订阅者
（_print_event）中处理，runtime / tools 发射的事件内容保持纯净。
"""

import asyncio

from ..core import Event, EventType, MessageRole
from ..tools.builtin import ReadFileTool, WriteFileTool
from .app import create_app
from .config import ConfigError, load_model_config

PATH_TO_OWN_TESTS = "D:\\Project\\my-agent\\backend\\own_tests"

# 这些事件之后打印分隔线，划分任务 / 步骤的边界
_SEPARATE_AFTER = {
    EventType.STEP_END,
    EventType.STEP_FAILED,
    EventType.TASK_END,
    EventType.TASK_FAILED,
}


def _print_event(event: Event) -> None:
    """打印运行过程事件；排版（分隔线、摘要缩进）由订阅者负责"""
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
    except ConfigError as e:
        print(f"配置错误：{e}")
        return

    app = create_app(
        config=config,
        tools=[ReadFileTool(PATH_TO_OWN_TESTS), WriteFileTool(PATH_TO_OWN_TESTS)],
    )
    app.bus.subscribe(_print_event)
    print(f"my-agent 冒烟终端（模型：{config.model_id}，空行退出）")

    while True:
        user_input = (await asyncio.to_thread(input, "> ")).strip()
        if not user_input:
            break
        print()

        result = await app.loop.run(user_input)

        final = app.loop.history.messages[-1]
        if final.role == MessageRole.ASSISTANT and final.content:
            print(f"assistant:\n{final.content}")
        print(
            f"[{result.status.value}] steps={result.steps} tokens={result.token_used}"
        )
        if result.error:
            print(f"错误：{result.error}")
        print()


if __name__ == "__main__":
    asyncio.run(_main())
