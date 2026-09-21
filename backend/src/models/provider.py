from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Generator, Literal
from abc import ABC, abstractmethod
from ..core import Tool, ToolCall
import json

# 延迟导入，避免未安装时直接报错
_anthropic = None
_openai = None

def _get_anthropic() -> Any:
    global _anthropic
    if _anthropic is None:
        import anthropic

        _anthropic = anthropic
    return _anthropic


def _get_openai() -> Any:
    global _openai
    if _openai is None:
        import openai

        _openai = openai
    return _openai

class ModelProviderType(Enum):
    CLAUDE = "claude"
    OPENAI = "openai"

@dataclass
class ModelConfig:
    """模型配置

    Args:
        provider: 模型提供者类型（CLAUDE 或 OPENAI）
        model_id: 模型名称（如 "gpt-4o", "deepseek-chat", "qwen-plus"）
        api_key: API 密钥
        base_url: API base URL（用于兼容服务，如 "https://api.deepseek.com"）
        timeout: 请求超时（秒）
        max_retries: 失败重试次数
        max_tokens: 最大生成 token 数（思考模式下包含思考 token）
        max_tokens_param: 长度限制参数名——"max_tokens"（DeepSeek 等兼容服务）或
            "max_completion_tokens"（OpenAI 官方新模型，o 系列必须使用，上限含推理 token）
        reasoning_effort: 推理强度档位，None 表示不开启。
            OpenAI 官方推理模型映射为 reasoning_effort 参数（"low"/"medium"/"high"），
            Anthropic 新模型（Opus 4.6+）映射为自适应思考 + output_config.effort
            （"low"/"medium"/"high"/"xhigh"/"max"）
        thinking_budget: 思考 token 预算（Anthropic 特有，最小 1024，必须小于 max_tokens），
            None 表示不开启扩展思考
        extra_body: 厂商扩展参数（如 DeepSeek 思考开关 {"thinking": {"type": "enabled"}}），
            原样透传给 API，None 表示不使用扩展参数
    """

    provider: ModelProviderType
    model_id: str
    api_key: str | None = None
    base_url: str | None = None
    timeout: int = 30
    max_retries: int = 2
    max_tokens: int = 4096
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    reasoning_effort: str | None = None
    thinking_budget: int | None = None
    extra_body: Dict[str, Any] | None = None

    def client_kwargs(self) -> Dict[str, Any]:
        """返回客户端构造参数（白名单），排除 None 值

        请求级参数（model_id、max_tokens、思考配置等）不在此处返回，
        由各 Provider 在调用时自行组装。
        """
        return {
            k: v
            for k, v in {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "timeout": self.timeout,
                "max_retries": self.max_retries,
            }.items()
            if v is not None
        }

@dataclass
class ProviderMessage:

    role: str
    content: str | None = None
    thinking: str | None = None
    thinking_signature: str | None = None  # 签名（Anthropic 特有，多轮回传校验用）
    redacted_thinking: str | None = None   # 加密思考数据（Anthropic 特有，原样回传）
    tool_calls: List[ToolCall] = field(default_factory=list)  # assistant 发出的工具调用
    tool_call_id: str | None = None  # role="tool" 时：对应哪一次调用

@dataclass
class ProviderResponse:

    content: str
    model: str
    finish_reason: str
    thinking: str | None = None
    thinking_signature: str | None = None # 签名（Anthropic 特有）
    redacted_thinking: str | None = None  # 加密思考数据（Anthropic 特有）
    token_used: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)

class StreamType(Enum):

    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"  # 思考内容增量
    TOOL_CALL_DELTA = "tool_call_delta"
    START = "start"
    STOP = "stop"

@dataclass
class StreamProviderResponse:

    type: StreamType
    token_used: int = 0
    delta: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Provider 抽象基类
# ============================================================================


class BaseProvider(ABC):

    def __init__(self, config: ModelConfig):
        self.config = config

    @abstractmethod
    def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        """非流式请求"""
        pass

    @abstractmethod
    def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Generator[StreamProviderResponse, None, None]:
        """流式请求"""
        pass


# ============================================================================
# OpenAI-Compatible Provider（支持 OpenAI、DeepSeek、Qwen、Ollama 等）
# ============================================================================


class OpenAIProvider(BaseProvider):

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        openai = _get_openai()
        kwargs = self.config.client_kwargs()
        self.client = openai.OpenAI(**kwargs)

    def complete(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> ProviderResponse:
        kwargs = self._build_request_kwargs(messages, tools)

        response = self.client.chat.completions.create(**kwargs)
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
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))
        
        return ProviderResponse(
            content=message.content or "",
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
            thinking=getattr(message, "reasoning_content", None),
            token_used=response.usage.total_tokens if response.usage else 0,
            tool_calls=tool_calls
        )

    def stream(
        self, messages: List[ProviderMessage], tools: List[Tool] | None = None
    ) -> Generator[StreamProviderResponse, None, None]:
        kwargs = self._build_request_kwargs(messages, tools)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        started = False
        token_used = 0
        finish_reason = None

        with self.client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
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
                        type = StreamType.START,
                        delta = {"role": delta.role or "assistant"}
                    )

                # OpenAI 官方 schema 里不包含 reasoning_content，而部分 OpenAI 兼容服务包含该字段
                # 思考 chunk
                thinking = getattr(delta, "reasoning_content", None)
                if thinking:
                    yield StreamProviderResponse(
                        type = StreamType.THINKING_DELTA,
                        delta = {"thinking": thinking}
                    )

                # 正文 chunk
                if delta.content:
                    yield StreamProviderResponse(
                        type = StreamType.TEXT_DELTA,
                        delta = {"content": delta.content}
                    )

                # 工具调用 chunk
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        yield StreamProviderResponse(
                            type = StreamType.TOOL_CALL_DELTA,
                            delta = {
                                "index": tc.index,
                                "id": tc.id,
                                "name": tc.function.name if tc.function else None,
                                "input": tc.function.arguments if tc.function else None
                            }
                        )

                # 暂存 finish_reason，不立即发 STOP：
                # 官方 OpenAI 的 usage chunk 在 finish chunk 之后才到达
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        # 流结束后统一发 STOP：两种厂商形态的 usage 此时都已收到，
        # 保证 STOP 携带完整的 token 统计，且 STOP 一定是最后一个事件
        yield StreamProviderResponse(
            type = StreamType.STOP,
            delta = {"finish_reason": finish_reason or "unknown"},
            token_used = token_used
        )
        

    def _build_request_kwargs(self, messages: List[ProviderMessage], tools: List[Tool]) -> Dict[str, Any]:
        """
        构建请求参数字典，用于调用 OpenAI API
        属于 complete() 与 stream() 共用的请求参数组装
        """
        kwargs = {
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
                msg["tool_calls"] = [OpenAIProvider._serialize_tool_call(tc) for tc in m.tool_calls]
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
                    }
                }
            )
            
        return openai_tools


# ============================================================================
# Claude Provider（Anthropic SDK）
# ============================================================================


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

# ============================================================================
# 统一工厂方法
# ============================================================================


def create_provider(config: ModelConfig) -> BaseProvider:
    """根据配置创建合适的 Provider"""
    if config.provider == ModelProviderType.CLAUDE:
        return ClaudeProvider(config)
    elif config.provider == ModelProviderType.OPENAI:
        return OpenAIProvider(config)
    else:
        raise ValueError(f"不支持的 provider 类型: {config.provider}")