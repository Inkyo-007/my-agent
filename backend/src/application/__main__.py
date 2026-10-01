"""最小 CLI 冒烟入口：poe chat（等价于 uv run python -m src.application）

逐行读取输入驱动主循环，事件与最终回答实时打印。
仅用于开发期手动冒烟（需配置 backend/.env），不是产品形态——
Web UI 待前端落地后以订阅事件总线的方式接入。
"""

import asyncio

from ..core import Event, MessageRole
from ..tools.builtin.fs import ReadFileTool, WriteFileTool
from .app import create_app
from .config import ConfigError, load_model_config


def _print_event(event: Event) -> None:
    """打印运行过程事件（步骤、工具调用、失败等）"""
    print(f"  [{event.type.value}] {event.content}")

PATH_TO_OWN_TESTS = "D:\\Project\\my-agent\\backend\\own_tests"

async def _main() -> None:
    try:
        config = load_model_config()
    except ConfigError as e:
        print(f"配置错误：{e}")
        return

    app = create_app(config=config, tools=[ReadFileTool(PATH_TO_OWN_TESTS), WriteFileTool(PATH_TO_OWN_TESTS)])
    app.bus.subscribe(_print_event)
    print(f"my-agent 冒烟终端（模型：{config.model_id}，空行退出）")

    while True:
        user_input = (await asyncio.to_thread(input, "> ")).strip()
        if not user_input:
            break
        print("")

        result = await app.loop.run(user_input)

        final = app.loop.history.messages[-1]
        if final.role == MessageRole.ASSISTANT and final.content:
            print("assistant:\n" + final.content + "\n")
        print(
            f"[{result.status.value}] steps={result.steps} tokens={result.token_used}\n"
        )
        if result.error:
            print(f"错误：{result.error}\n")


if __name__ == "__main__":
    asyncio.run(_main())
