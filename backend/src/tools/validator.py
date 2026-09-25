"""工具调用的机械校验助手（纯函数，无状态）

职责边界：只做「机械校验」——确定性、永远适用、与部署无关的检查（工具是否存在、参数是否合法）。

错误文案的消费者是模型本身——executor 会把这些文案包装进 ToolResult
喂回模型，模型据此自我纠正重试。因此文案必须具体、可操作。
"""

from difflib import get_close_matches
from typing import List

import jsonschema

from ..core import ToolCall, ToolInputSchema
from .registry import ToolRegistry


def check_tool_exists(tool_call: ToolCall, registry: ToolRegistry) -> str | None:
    """工具存在性检查

    存在返回 None；不存在返回错误文案，包含：
    - 相近名字的模糊建议（difflib，cutoff=0.6）
    - 可用工具清单
    """
    if registry.get(tool_call.name) is not None:
        return None

    available = [t.definition.name for t in registry.list_tools()]
    parts = [f"工具 '{tool_call.name}' 未注册"]
    suggestions = get_close_matches(tool_call.name, available, n=1, cutoff=0.6)
    if suggestions:
        parts.append(f"您是想要调用 '{suggestions[0]}' 吗？")
    parts.append(f"可用工具: {available}")
    return "。".join(parts)


def validate_tool_input(tool_call: ToolCall, schema: ToolInputSchema) -> List[str]:
    """参数 schema 校验

    返回错误描述列表，空列表 = 通过。
    注意：schema 未声明 additionalProperties=False，模型多传的参数被允许
    （与两家 API 的行为一致），只有缺必填与类型错误会拦截。
    """
    json_schema = {
        "type": "object",
        "properties": schema.properties,
        "required": schema.required,
    }
    validator = jsonschema.Draft7Validator(json_schema)
    return [_translate_error(e) for e in validator.iter_errors(tool_call.input)]


def _translate_error(error: jsonschema.ValidationError) -> str:
    """将 jsonschema 的错误翻译为模型可读的中文文案"""
    if error.validator == "required":
        # message 形如 "'city' is a required property"，从其中提取字段名
        missing = error.message.split("'")[1]
        return f"缺少必填参数 '{missing}'"
    if error.validator == "type":
        field = ".".join(str(p) for p in error.absolute_path) or "(根)"
        return f"参数 '{field}' 类型错误：应为 {error.validator_value}"
    field = ".".join(str(p) for p in error.absolute_path) or "(根)"
    return f"参数 '{field}' 校验失败：{error.message}"
