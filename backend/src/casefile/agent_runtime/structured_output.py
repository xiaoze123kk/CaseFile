"""Provider-neutral helpers for validated structured model output."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
from pydantic import BaseModel, ValidationError

from casefile.contracts import ContractValidationError

DEEPSEEK_BETA_BASE_URL = "https://api.deepseek.com/beta"
STRICT_OUTPUT_TOOL_NAME = "submit_structured_output"
_ISSUE_LIMIT = 20
_SUPPORTED_STRING_FORMATS = {"email", "hostname", "ipv4", "ipv6", "uuid"}
_DROPPED_TRANSPORT_KEYWORDS = {
    "$id",
    "$schema",
    "default",
    "examples",
    "maxItems",
    "maxLength",
    "minItems",
    "minLength",
    "title",
}
_SUPPORTED_SCHEMA_KEYWORDS = {
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "description",
    "discriminator",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maximum",
    "minimum",
    "multipleOf",
    "pattern",
    "properties",
    "required",
    "type",
}


class StrictSchemaIneligible(ValueError):
    """The Pydantic schema cannot be represented by DeepSeek strict tools safely."""


class StrictOutputProtocolError(RuntimeError):
    """DeepSeek did not honor the required strict-tool response protocol."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class StructuredCallResult:
    raw_output: str
    usage: dict[str, Any]


@lru_cache(maxsize=32)
def compile_deepseek_strict_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    """Compile a Pydantic schema into DeepSeek's strict-tool transport subset."""

    raw_schema = deepcopy(output_type.model_json_schema())
    compiled = _compile_schema_node(raw_schema, path="#")
    if not isinstance(compiled, dict) or compiled.get("type") != "object":
        raise StrictSchemaIneligible("Strict tool output must have an object root")
    return compiled


def _compile_schema_node(node: Any, *, path: str) -> Any:
    if isinstance(node, list):
        return [
            _compile_schema_node(item, path=f"{path}/{index}") for index, item in enumerate(node)
        ]
    if not isinstance(node, dict):
        return node

    unknown = set(node) - _SUPPORTED_SCHEMA_KEYWORDS - _DROPPED_TRANSPORT_KEYWORDS
    if unknown:
        raise StrictSchemaIneligible(
            f"Unsupported JSON Schema keywords at {path}: {sorted(unknown)!r}"
        )
    if "oneOf" in node or "allOf" in node or "not" in node:
        raise StrictSchemaIneligible(f"Unsupported JSON Schema composition at {path}")
    if node.get("type") == "null":
        return {"enum": [None]}

    compiled: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROPPED_TRANSPORT_KEYWORDS or key == "discriminator":
            continue
        if key in {"$defs", "properties"}:
            if not isinstance(value, dict):
                raise StrictSchemaIneligible(f"Schema map is invalid at {path}/{key}")
            compiled[key] = {
                str(name): _compile_schema_node(schema, path=f"{path}/{key}/{name}")
                for name, schema in value.items()
            }
            continue
        if key == "const":
            compiled["enum"] = [value]
            continue
        if key == "format" and value not in _SUPPORTED_STRING_FORMATS:
            continue
        if key == "additionalProperties":
            if value is not False:
                raise StrictSchemaIneligible(f"Open object is not supported at {path}")
            compiled[key] = False
            continue
        compiled[key] = _compile_schema_node(value, path=f"{path}/{key}")

    properties = compiled.get("properties")
    if compiled.get("type") == "object":
        if not isinstance(properties, dict):
            raise StrictSchemaIneligible(f"Object properties are missing at {path}")
        compiled["required"] = list(properties)
        compiled["additionalProperties"] = False
    return compiled


async def call_deepseek_strict_tool(
    *,
    api_key: str,
    model_id: str,
    network_retries: int,
    instructions: str,
    input_text: str,
    output_type: type[BaseModel],
) -> StructuredCallResult:
    """Call DeepSeek Beta with one forced strict tool and validate its arguments."""

    schema = compile_deepseek_strict_schema(output_type)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BETA_BASE_URL,
        max_retries=network_retries,
    )
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": input_text},
        ],
        tools=cast(
            Any,
            [
                {
                    "type": "function",
                    "function": {
                        "name": STRICT_OUTPUT_TOOL_NAME,
                        "description": "Submit the complete validated structured output.",
                        "parameters": schema,
                        "strict": True,
                    },
                }
            ],
        ),
        tool_choice=cast(
            Any,
            {
                "type": "function",
                "function": {"name": STRICT_OUTPUT_TOOL_NAME},
            },
        ),
        parallel_tool_calls=False,
        extra_body={"thinking": {"type": "disabled"}},
    )
    if len(response.choices) != 1:
        raise StrictOutputProtocolError(
            "strict_choice_count_invalid",
            "DeepSeek strict output must contain exactly one choice",
        )
    tool_calls = response.choices[0].message.tool_calls or []
    if len(tool_calls) != 1:
        raise StrictOutputProtocolError(
            "strict_tool_call_count_invalid",
            "DeepSeek strict output must contain exactly one tool call",
        )
    tool_call = tool_calls[0]
    if tool_call.type != "function":
        raise StrictOutputProtocolError(
            "strict_tool_type_invalid",
            "DeepSeek returned an unexpected custom tool call",
        )
    if tool_call.function.name != STRICT_OUTPUT_TOOL_NAME:
        raise StrictOutputProtocolError(
            "strict_tool_name_invalid",
            "DeepSeek called an unexpected structured-output tool",
        )
    usage = response.usage
    return StructuredCallResult(
        raw_output=tool_call.function.arguments,
        usage={
            "requests": 1,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "cached_tokens": int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
            "reasoning_tokens": 0,
        },
    )


def strict_fallback_reason(error: Exception) -> str | None:
    """Return a stable reason only for strict-protocol compatibility failures."""

    if isinstance(error, StrictSchemaIneligible):
        return "strict_schema_ineligible"
    if isinstance(error, StrictOutputProtocolError):
        return error.reason_code
    if not isinstance(error, BadRequestError):
        return None
    body = json.dumps(error.body, ensure_ascii=False, default=str).lower()
    markers = ("beta", "function", "schema", "strict", "tool")
    if any(marker in body for marker in markers):
        return "strict_request_unsupported"
    return None


def repair_input(input_text: str, issues: list[dict[str, Any]]) -> str:
    """Append bounded machine-readable validation feedback to the frozen input."""

    feedback = [
        {
            "code": str(issue.get("code", "schema_invalid")),
            "path": str(issue.get("path", "")),
            "message": str(issue.get("message", "生成内容未通过结构校验。"))[:240],
        }
        for issue in issues[:_ISSUE_LIMIT]
    ]
    return (
        input_text
        + "\n\nrepair_feedback="
        + json.dumps(
            feedback,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def validate_model_json(
    output_type: type[BaseModel],
    raw_output: str,
    *,
    discarded_paths: list[str] | None = None,
    planned_object_types: dict[str, str] | None = None,
    normalized_ref_paths: list[str] | None = None,
) -> BaseModel:
    """Validate JSON and apply only deterministic, precisely located normalization."""

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError:
        try:
            return output_type.model_validate_json(raw_output)
        except ValidationError as error:
            raise ContractValidationError(pydantic_validation_issues(error)) from error

    try:
        return output_type.model_validate(payload)
    except ValidationError as error:
        removed = _discard_forbidden_fields(payload, error)
        normalized = _normalize_planned_object_ref_types(
            payload,
            error,
            planned_object_types or {},
        )
        if removed or normalized:
            try:
                validated = output_type.model_validate(payload)
            except ValidationError as repaired_error:
                raise ContractValidationError(
                    pydantic_validation_issues(repaired_error)
                ) from repaired_error
            if discarded_paths is not None:
                discarded_paths.extend(removed)
            if normalized_ref_paths is not None:
                normalized_ref_paths.extend(normalized)
            return validated
        raise ContractValidationError(pydantic_validation_issues(error)) from error


def pydantic_validation_issues(error: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in error.errors(
        include_url=False,
        include_context=True,
        include_input=False,
    ):
        issue_type = str(item.get("type", "schema_invalid"))
        code = "candidate_json_invalid" if issue_type == "json_invalid" else issue_type
        message = _provider_validation_message(issue_type)
        if issue_type == "literal_error":
            expected = item.get("ctx", {}).get("expected")
            if isinstance(expected, str):
                message = f"字段值必须是以下枚举之一：{expected}。"
        elif issue_type == "value_error":
            raw_message = str(item.get("msg", ""))
            if "local_key values must be unique" in raw_message:
                message = "每个对象的 local_key 必须唯一。"
            elif "references unknown keys" in raw_message:
                message = "referenced_keys 只能引用同一计划中已声明的 local_key。"
            elif "at least one resolution spec" in raw_message:
                message = "计划必须至少包含一个 collection 为 resolution_specs 的对象。"
        issues.append(
            {
                "code": code,
                "path": _json_pointer(item.get("loc", ())),
                "message": message,
            }
        )
    return issues or [
        {
            "code": "schema_invalid",
            "path": "",
            "message": "生成内容未通过 CaseFile 结构校验。",
        }
    ]


def merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
    for record in records:
        for key in tuple(merged):
            value = record.get(key, 0)
            if isinstance(value, int):
                merged[key] += value
    return merged


def _discard_forbidden_fields(payload: Any, error: ValidationError) -> list[str]:
    removed: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        if issue.get("type") != "extra_forbidden":
            continue
        location = tuple(issue.get("loc", ()))
        if location and _delete_json_location(payload, location):
            removed.append(_json_pointer(location))
    return sorted(set(removed))


def _delete_json_location(payload: Any, location: tuple[Any, ...]) -> bool:
    current = payload
    for part in location[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return False
    leaf = location[-1]
    if isinstance(current, dict) and leaf in current:
        del current[leaf]
        return True
    return False


def _normalize_planned_object_ref_types(
    payload: Any,
    error: ValidationError,
    planned_object_types: dict[str, str],
) -> list[str]:
    normalized: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = tuple(issue.get("loc", ()))
        if issue.get("type") not in {"enum", "literal_error"}:
            continue
        if not location or location[-1] != "object_type":
            continue
        reference = _json_value_at(payload, location[:-1])
        if not isinstance(reference, dict):
            continue
        object_id = reference.get("object_id")
        if not isinstance(object_id, str):
            continue
        expected_type = planned_object_types.get(object_id)
        if expected_type is None or reference.get("object_type") == expected_type:
            continue
        reference["object_type"] = expected_type
        normalized.append(_json_pointer(location))
    return sorted(set(normalized))


def _json_value_at(payload: Any, location: tuple[Any, ...]) -> Any:
    current = payload
    for part in location:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return None
    return current


def _json_pointer(parts: Any) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not escaped else "/" + "/".join(escaped)


def _provider_validation_message(issue_type: str) -> str:
    return {
        "missing": "缺少必填字段。",
        "extra_forbidden": "包含契约未允许的字段。",
        "string_type": "字段应为文本。",
        "string_too_short": "文本长度不足。",
        "string_too_long": "文本长度超出限制。",
        "int_parsing": "字段应为整数。",
        "int_type": "字段应为整数。",
        "bool_type": "字段应为布尔值。",
        "list_type": "字段应为列表。",
        "dict_type": "字段应为对象。",
        "literal_error": "字段值不在契约允许范围内。",
        "enum": "字段不符合 CaseFile 结构约束。",
    }.get(issue_type, "字段不符合 CaseFile 结构约束。")


__all__ = [
    "DEEPSEEK_BETA_BASE_URL",
    "STRICT_OUTPUT_TOOL_NAME",
    "StrictOutputProtocolError",
    "StrictSchemaIneligible",
    "StructuredCallResult",
    "call_deepseek_strict_tool",
    "compile_deepseek_strict_schema",
    "merge_usage",
    "pydantic_validation_issues",
    "repair_input",
    "strict_fallback_reason",
    "validate_model_json",
]
