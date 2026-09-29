"""Message ↔ ProviderMessage 双向转换（纯函数）

协议字段（思考签名、加密思考数据、tool_call 配对）随 Message.metadata
走，且只放 JSON 可序列化的原始结构（ToolCall 对象由本模块重建）。
转换是无损的：协议字段原样重建，是否回传由具体 Provider 按协议规则
决定（如 ClaudeProvider 跳过无签名的 thinking block——DeepSeek 强制
回传 reasoning_content，在转换层降级会误伤 OpenAI 兼容服务）。

metadata 键约定：
- assistant 消息：thinking_signature / redacted_thinking /
  tool_calls（list[dict]，每项含 id/name/input）
- tool 消息：tool_call_id / tool_name / status（沿用 ToolResultMessage 既有约定）
"""

from typing import Any, Dict, List, Sequence

from ..core import (
    Message,
    MessageRole,
    MessageType,
    ProviderMessage,
    ProviderResponse,
    ToolCall,
    ToolCallMessage,
    ToolResult,
    ToolResultMessage,
)


def message_from_response(response: ProviderResponse) -> Message:
    """模型响应 → 历史消息（assistant）"""
    metadata: Dict[str, Any] = {}
    if response.thinking_signature:
        metadata["thinking_signature"] = response.thinking_signature
    if response.redacted_thinking:
        metadata["redacted_thinking"] = response.redacted_thinking
    if response.tool_calls:
        # 只存原始字典结构：ToolCall 不可 JSON 序列化，由转换层重建
        metadata["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "input": tc.input}
            for tc in response.tool_calls
        ]
        return ToolCallMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.TOOL_USE,
            content=response.content,
            thinking=response.thinking,
            metadata=metadata,
        )
    return Message(
        role=MessageRole.ASSISTANT,
        type=MessageType.TEXT,
        content=response.content,
        thinking=response.thinking,
        metadata=metadata,
    )


def message_from_tool_result(
    tool_call: ToolCall, result: ToolResult
) -> ToolResultMessage:
    """工具结果 → 历史消息（tool）

    status：成功为 "success"，失败取执行器的 error_type（稳定契约），
    供 UI/审计分类消费。
    """
    return ToolResultMessage(
        role=MessageRole.TOOL,
        type=MessageType.TOOL_RESULT,
        content=result.content,
        metadata={
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "status": "success" if result.success else (result.error_type or "error"),
        },
    )


def to_provider_messages(messages: Sequence[Message]) -> List[ProviderMessage]:
    """历史消息 → 喂模型的协议消息（无损重建协议字段）"""
    result: List[ProviderMessage] = []
    for m in messages:
        if m.role == MessageRole.ASSISTANT:
            tool_calls = [
                ToolCall(
                    id=str(tc.get("id", "")),
                    name=str(tc.get("name", "")),
                    input=tc.get("input") if isinstance(tc.get("input"), dict) else {},
                )
                for tc in m.metadata.get("tool_calls", [])
            ]
            result.append(
                ProviderMessage(
                    role="assistant",
                    content=m.content,
                    thinking=m.thinking,
                    thinking_signature=m.metadata.get("thinking_signature"),
                    redacted_thinking=m.metadata.get("redacted_thinking"),
                    tool_calls=tool_calls,
                )
            )
        elif m.role == MessageRole.TOOL:
            result.append(
                ProviderMessage(
                    role="tool",
                    content=m.content,
                    tool_call_id=m.metadata.get("tool_call_id"),
                )
            )
        else:
            # system / user：直接透传（system 由 Provider 按协议处理）
            result.append(ProviderMessage(role=m.role.value, content=m.content))
    return result
