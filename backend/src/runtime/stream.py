"""流式响应组装：StreamProviderResponse 增量序列 → ProviderResponse

models 层已把协议碎片归一化为五种 StreamType（delta 字段约定见
core/provider.py），本模块只做协议无关的聚合：

- 正文 / 思考 / 签名 / 加密思考：按 delta 键分别拼接留存
  （签名与加密思考是 Anthropic 多轮思考的回传命脉，不能丢）；
- 工具调用：按 index 分桶，id/name 取首个非空碎片，input 的 JSON
  文本片段拼接后在 STOP 时解析（非法 JSON 降级为 {}，与 complete() 对齐）；
- START 携带 model；STOP 一定最后一帧，携带 finish_reason 与 token 统计。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..core import ProviderResponse, StreamProviderResponse, StreamType, ToolCall


@dataclass
class _ToolCallBuffer:
    """一次工具调用的碎片收集桶（按 index 分桶）"""

    call_id: str = ""
    name: str = ""
    input_parts: List[str] = field(default_factory=list)


class StreamAccumulator:
    """把一次 stream() 的增量事件聚合成等价的 ProviderResponse

    用法：逐帧 feed()，流结束（STOP 帧已喂入）后调用 response() 取结果。
    """

    def __init__(self) -> None:
        self._content: List[str] = []
        self._thinking: List[str] = []
        self._signature: List[str] = []
        self._redacted_thinking: List[str] = []
        self._tool_calls: Dict[int, _ToolCallBuffer] = {}
        self._model = ""
        self._finish_reason = "unknown"
        self._token_used = 0

    def feed(self, event: StreamProviderResponse) -> None:
        """喂入一帧增量事件"""
        delta = event.delta
        if event.type == StreamType.START:
            self._model = str(delta.get("model") or "")
        elif event.type == StreamType.TEXT_DELTA:
            self._content.append(str(delta.get("content") or ""))
        elif event.type == StreamType.THINKING_DELTA:
            if delta.get("thinking"):
                self._thinking.append(str(delta["thinking"]))
            if delta.get("signature"):
                self._signature.append(str(delta["signature"]))
            if delta.get("redacted_thinking"):
                self._redacted_thinking.append(str(delta["redacted_thinking"]))
        elif event.type == StreamType.TOOL_CALL_DELTA:
            index = int(delta.get("index") or 0)
            bucket = self._tool_calls.setdefault(index, _ToolCallBuffer())
            if delta.get("id"):
                bucket.call_id = str(delta["id"])
            if delta.get("name"):
                bucket.name = str(delta["name"])
            if delta.get("input"):
                bucket.input_parts.append(str(delta["input"]))
        elif event.type == StreamType.STOP:
            self._finish_reason = str(delta.get("finish_reason") or "unknown")
            self._token_used = event.token_used

    def response(self) -> ProviderResponse:
        """输出组装完成的 ProviderResponse（工具调用按 index 升序）"""
        return ProviderResponse(
            content="".join(self._content),
            model=self._model,
            finish_reason=self._finish_reason,
            thinking="".join(self._thinking) or None,
            thinking_signature="".join(self._signature) or None,
            redacted_thinking="".join(self._redacted_thinking) or None,
            token_used=self._token_used,
            tool_calls=[
                self._build_tool_call(self._tool_calls[index])
                for index in sorted(self._tool_calls)
            ],
        )

    @staticmethod
    def _build_tool_call(buffer: _ToolCallBuffer) -> ToolCall:
        raw = "".join(buffer.input_parts)
        input_args: Dict[str, Any] = {}
        if raw:
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                input_args = parsed
        return ToolCall(id=buffer.call_id, name=buffer.name, input=input_args)
