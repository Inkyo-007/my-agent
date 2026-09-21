"""Claude Provider（Anthropic SDK）

对应 Anthropic Messages API 协议：block 结构的响应、顶层 system 参数、
tool_result 合并规则、扩展思考（手动预算 / 自适应）等协议知识都封装在本文件。
"""
from typing import Any, Dict, List, Generator

from ..core import Tool, ToolCall
from .base import (
    BaseProvider,
    ModelConfig,
    ProviderMessage,
    ProviderResponse,
    StreamProviderResponse,
    StreamType,
)

# 延迟导入，避免未安装时直接报错
_anthropic = None


def _get_anthropic() -> Any:
    global _anthropic
    if _anthropic is None:
        import anthropic

        _anthropic = anthropic
    return _anthropic


class ClaudeProvider(BaseProvider):

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        anthropic = _get_anthropic()
        kwargs = self.config.client_kwargs()
        self.client = anthropic.Anthropic(**kwargs)

    def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        kwargs = self._build_request_kwargs(messages, tools)

        response = self.client.messages.create(**kwargs)

        text_parts: List[str] = []
        thinking_parts: List[str] = []
        thinking_signature = None
        redacted_thinking = None
        tool_calls: List[ToolCall] = []

        # content 是 block 列表，按类型分拣
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(block.thinking)
                thinking_signature = block.signature
            elif block.type == "redacted_thinking":
                # 加密思考数据，无法读取明文，原样留存用于回传
                redacted_thinking = block.data
            elif block.type == "tool_use":
                # input 已是解析好的 dict，无需 json.loads
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input or {},
                ))

        usage = response.usage
        return ProviderResponse(
            content="".join(text_parts),
            model=response.model,
            finish_reason=response.stop_reason or "end_turn",
            thinking="".join(thinking_parts) or None,
            thinking_signature=thinking_signature,
            redacted_thinking=redacted_thinking,
            # Anthropic 的 usage 没有 total_tokens，需自行相加
            token_used=(usage.input_tokens + usage.output_tokens) if usage else 0,
            tool_calls=tool_calls,
        )

    def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Generator[StreamProviderResponse, None, None]:
        kwargs = self._build_request_kwargs(messages, tools)
        kwargs["stream"] = True

        started = False
        input_tokens = 0
        output_tokens = 0

        with self.client.messages.create(**kwargs) as stream:
            for event in stream:
                # 事件类型：message_start / content_block_start / content_block_delta /
                #           content_block_stop / message_delta / message_stop
                if event.type == "message_start":
                    if event.message.usage:
                        input_tokens = event.message.usage.input_tokens
                    if not started:
                        started = True
                        yield StreamProviderResponse(
                            type=StreamType.START,
                            delta={"role": "assistant"},
                        )

                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        # 工具调用的 id/name 在 start 事件一次性给全
                        yield StreamProviderResponse(
                            type=StreamType.TOOL_CALL_DELTA,
                            delta={"index": event.index, "id": block.id,
                                   "name": block.name, "input": None},
                        )
                    elif block.type == "redacted_thinking":
                        # 加密思考数据在 start 事件一次性给全（无 delta），透传给上层留存回传
                        yield StreamProviderResponse(
                            type=StreamType.THINKING_DELTA,
                            delta={"redacted_thinking": block.data},
                        )

                elif event.type == "content_block_delta":
                    d = event.delta
                    if d.type == "thinking_delta":
                        yield StreamProviderResponse(
                            type=StreamType.THINKING_DELTA,
                            delta={"thinking": d.thinking},
                        )
                    elif d.type == "signature_delta":
                        yield StreamProviderResponse(
                            type=StreamType.THINKING_DELTA,
                            delta={"signature": d.signature},
                        )
                    elif d.type == "text_delta":
                        yield StreamProviderResponse(
                            type=StreamType.TEXT_DELTA,
                            delta={"content": d.text},
                        )
                    elif d.type == "input_json_delta":
                        # 工具参数的 JSON 碎片（对应 OpenAI 的 input 碎片）
                        yield StreamProviderResponse(
                            type=StreamType.TOOL_CALL_DELTA,
                            delta={"index": event.index, "id": None,
                                   "name": None, "input": d.partial_json},
                        )

                elif event.type == "message_delta":
                    # 携带 stop_reason 与最终 output_tokens，在此统一发 STOP
                    if event.usage:
                        output_tokens = event.usage.output_tokens
                    else:
                        output_tokens = 0
                    stop_reason = getattr(event.delta, "stop_reason", None)
                    yield StreamProviderResponse(
                        type=StreamType.STOP,
                        delta={"finish_reason": stop_reason or "end_turn"},
                        token_used=input_tokens + output_tokens,
                    )
                # content_block_stop / message_stop / ping：无对外事件

    def _build_request_kwargs(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Dict[str, Any]:
        """
        构建请求参数字典，用于调用 Anthropic API
        属于 complete() 与 stream() 共用的请求参数组装
        """
        kwargs: Dict[str, Any] = {
            "model": self.config.model_id,
            "max_tokens": self.config.max_tokens,  # Anthropic 必填
            "messages": self._build_api_messages(messages),
        }

        # system 提示：Anthropic 的顶层参数，从消息列表中剥离
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        # 扩展思考
        if self.config.thinking_budget is not None:
            # 手动预算模式（Claude 4.x 系列；注意 Opus 4.7+ 已移除此模式，会返回 400）
            if self.config.max_tokens <= self.config.thinking_budget:
                raise ValueError(
                    f"max_tokens({self.config.max_tokens}) 必须大于 "
                    f"thinking_budget({self.config.thinking_budget})"
                )
            elif self.config.thinking_budget < 1024:
                raise ValueError(
                    f"thinking_budget({self.config.thinking_budget}) 必须大于等于 1024"
                )
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.config.thinking_budget,
            }
        elif self.config.reasoning_effort:
            # 自适应思考（Claude Opus 4.6+）：模型根据请求复杂度自行决定思考量。
            # 显式声明而不依赖 API 默认行为。
            # 注意：老模型（如 Sonnet 4.5）不支持 adaptive，该组合属未定义行为
            kwargs["thinking"] = {"type": "adaptive"}

        # 推理强度档位（GA 参数，新模型上替代已移除的 temperature 等采样参数）
        if self.config.reasoning_effort:
            kwargs["output_config"] = {"effort": self.config.reasoning_effort}

        if self.config.extra_body:
            kwargs["extra_body"] = self.config.extra_body

        return kwargs

    @staticmethod
    def _build_api_messages(messages: List[ProviderMessage]) -> List[Dict[str, Any]]:
        """将 ProviderMessage 列表转换为 Anthropic API 消息格式

        - role="system" 的消息被跳过（由顶层 system 参数承载）
        - assistant 消息重建为 block 列表：thinking → redacted_thinking → text → tool_use
        - role="tool" 的工具结果转为 user 消息中的 tool_result block；
          连续多条工具结果必须合并进同一条 user 消息（Anthropic 要求）
        """
        api_messages: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue

            if m.role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if m.thinking:
                    blocks.append({
                        "type": "thinking",
                        "thinking": m.thinking,
                        "signature": m.thinking_signature,
                    })
                if m.redacted_thinking:
                    blocks.append({
                        "type": "redacted_thinking",
                        "data": m.redacted_thinking,
                    })
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(ClaudeProvider._serialize_tool_call(tc))
                api_messages.append({
                    "role": "assistant",
                    "content": blocks if blocks else (m.content or ""),
                })

            elif m.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                }
                prev = api_messages[-1] if api_messages else None
                if prev and prev["role"] == "user" and isinstance(prev["content"], list):
                    # 与上一条工具结果合并进同一条 user 消息
                    prev["content"].append(block)
                else:
                    api_messages.append({"role": "user", "content": [block]})

            else:  # user
                api_messages.append({"role": "user", "content": m.content or ""})

        return api_messages

    @staticmethod
    def _serialize_tool_call(tool_call: ToolCall) -> Dict[str, Any]:
        """将统一的 ToolCall 转换为 Anthropic 的 tool_use block 格式"""
        return {
            "type": "tool_use",
            "id": tool_call.id,
            "name": tool_call.name,
            "input": tool_call.input,  # input 保持 dict，无需序列化为字符串
        }

    @staticmethod
    def _convert_tools(tools: List[Tool]) -> List[Dict[str, Any]]:
        """将 Tool 对象转换为 Anthropic calling 格式"""

        return [tool.get_basic_definition_dict() for tool in tools]
