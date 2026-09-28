"""OpenAI-Compatible Provider（支持 OpenAI、DeepSeek、Qwen、Ollama 等）

一个 Provider 对应一种协议：所有遵循 OpenAI Chat Completions 协议的厂商
（换 base_url + model_id 即可接入）都复用本实现。
"""

import json
from typing import Any, AsyncGenerator, Dict, List

from ..core import (
    BaseProvider,
    ProviderMessage,
    ProviderResponse,
    StreamProviderResponse,
    StreamType,
    Tool,
    ToolCall,
)
from .config import ModelConfig

# 延迟导入，避免未安装时直接报错
_openai = None


def _get_openai() -> Any:
    global _openai
    if _openai is None:
        import openai

        _openai = openai
    return _openai


class OpenAIProvider(BaseProvider):
    def __init__(self, config: ModelConfig):
        self.config = config
        openai = _get_openai()
        kwargs = self.config.client_kwargs()
        self.client = openai.AsyncOpenAI(**kwargs)

    async def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        kwargs = self._build_request_kwargs(messages, tools)

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls: List[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=args,
                    )
                )

        return ProviderResponse(
            content=message.content or "",
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
            thinking=getattr(message, "reasoning_content", None),
            token_used=response.usage.total_tokens if response.usage else 0,
            tool_calls=tool_calls,
        )

    async def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> AsyncGenerator[StreamProviderResponse, None]:
        kwargs = self._build_request_kwargs(messages, tools)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        started = False
        token_used = 0
        finish_reason = None

        stream = await self.client.chat.completions.create(**kwargs)
        async with stream:
            async for chunk in stream:
                # 暂存 token 使用情况。usage 的位置随厂商不同：
                # DeepSeek 挂在 finish chunk 上；官方 OpenAI 在 finish chunk 之后
                # 单独发一个 choices 为空的 chunk。因此每个 chunk 都检查。
                if chunk.usage:
                    token_used = chunk.usage.total_tokens
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # 首个有效 chunk，标记流开始
                if not started:
                    started = True
                    yield StreamProviderResponse(
                        type=StreamType.START, delta={"role": delta.role or "assistant"}
                    )

                # OpenAI 官方 schema 里不包含 reasoning_content，而部分 OpenAI 兼容服务包含该字段
                # 思考 chunk
                thinking = getattr(delta, "reasoning_content", None)
                if thinking:
                    yield StreamProviderResponse(
                        type=StreamType.THINKING_DELTA, delta={"thinking": thinking}
                    )

                # 正文 chunk
                if delta.content:
                    yield StreamProviderResponse(
                        type=StreamType.TEXT_DELTA, delta={"content": delta.content}
                    )

                # 工具调用 chunk
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        yield StreamProviderResponse(
                            type=StreamType.TOOL_CALL_DELTA,
                            delta={
                                "index": tc.index,
                                "id": tc.id,
                                "name": tc.function.name if tc.function else None,
                                "input": tc.function.arguments if tc.function else None,
                            },
                        )

                # 暂存 finish_reason，不立即发 STOP：
                # 官方 OpenAI 的 usage chunk 在 finish chunk 之后才到达
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        # 流结束后统一发 STOP：两种厂商形态的 usage 此时都已收到，
        # 保证 STOP 携带完整的 token 统计，且 STOP 一定是最后一个事件
        yield StreamProviderResponse(
            type=StreamType.STOP,
            delta={"finish_reason": finish_reason or "unknown"},
            token_used=token_used,
        )

    def _build_request_kwargs(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Dict[str, Any]:
        """
        构建请求参数字典，用于调用 OpenAI API
        属于 complete() 与 stream() 共用的请求参数组装
        """
        kwargs: Dict[str, Any] = {
            "model": self.config.model_id,
            "messages": self._build_api_messages(messages),
            self.config.max_tokens_param: self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        # 推理/扩展参数
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        if self.config.extra_body:
            # 厂商扩展参数（如 DeepSeek 思考开关），原样透传
            kwargs["extra_body"] = self.config.extra_body

        return kwargs

    @staticmethod
    def _build_api_messages(messages: List[ProviderMessage]) -> List[Dict[str, Any]]:
        """将 ProviderMessage 列表转换为 OpenAI API 消息格式

        thinking（reasoning_content）一律回传：携带 tools 时 DeepSeek 强制要求
        回传所有历史轮次的 reasoning_content（否则返回 400）；不携带 tools 时
        API 会忽略该字段，无副作用。
        """
        api_messages = []
        for m in messages:
            msg: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.thinking:
                msg["reasoning_content"] = m.thinking
            if m.tool_calls:
                # assistant 的工具调用回传：arguments 需重新序列化为 JSON 字符串
                msg["tool_calls"] = [
                    OpenAIProvider._serialize_tool_call(tc) for tc in m.tool_calls
                ]
            if m.tool_call_id:
                # role="tool" 的工具结果消息
                msg["tool_call_id"] = m.tool_call_id
            api_messages.append(msg)
        return api_messages

    @staticmethod
    def _serialize_tool_call(tool_call: ToolCall) -> Dict[str, Any]:
        """将统一的 ToolCall 转换为 OpenAI API 的 tool_calls 格式"""
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.name,
                "arguments": json.dumps(tool_call.input, ensure_ascii=False),
            },
        }

    @staticmethod
    def _convert_tools(tools: List[Tool]) -> List[Dict[str, Any]]:
        """将 Tool 对象转换为 OpenAI function calling 格式"""
        openai_tools = []
        for tool in tools:
            tool_dict = tool.get_basic_definition_dict()
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_dict["name"],
                        "description": tool_dict["description"],
                        "parameters": tool_dict["input_schema"],
                    },
                }
            )

        return openai_tools
