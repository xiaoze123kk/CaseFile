"""Novel-scoped rolling memory: protected prose, raw recent turns, bounded summaries."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from casefile.agent_runtime.context.estimators import estimate_conservative_tokens
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    ProseRewriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256

POLICY = "novel-context-v1"
MAX_BATCH_TOKENS = 12000
MAX_BATCHES = 4
MAX_MEMORY_TOKENS = 2000
RECENT_TOKENS = 3000


def tokens(value: Any) -> int:
    return estimate_conservative_tokens(json.dumps(value, ensure_ascii=False))


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["author_preference", "discussion", "open_question"]
    text: str = Field(min_length=1, max_length=500)
    source_ids: list[int] = Field(min_length=1, max_length=12)


class NovelMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MemoryItem] = Field(max_length=24)


def plan_history(turns: list[dict[str, Any]], memory: dict[str, Any]) -> dict[str, Any]:
    """Keep complete recent exchanges; compact older raw exchanges in chronological batches."""
    recent: list[dict[str, Any]] = []
    split = len(turns)
    for turn in reversed(turns):
        if len(recent) >= 3 or tokens([turn, *recent]) > RECENT_TOKENS:
            break
        recent.insert(0, turn)
        split -= 1
    batches: list[list[dict[str, Any]]] = []
    for turn in turns[:split]:
        if tokens([turn]) > MAX_BATCH_TOKENS:
            raise ValueError("novel_history_turn_too_large")
        if not batches or tokens([*batches[-1], turn]) > MAX_BATCH_TOKENS:
            batches.append([])
        batches[-1].append(turn)
    if len(batches) > MAX_BATCHES:
        raise ValueError("novel_history_backlog_too_large")
    return {"policy": POLICY, "memory": memory, "batches": batches, "recent": recent}


def history_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "role": role,
            "content": turn[field],
            "source_id": turn["id"],
            "revision": turn["revision"],
            "scope": turn["scope"],
            "edit_status": turn["edit_status"],
        }
        for turn in turns
        for role, field in (("user", "instruction"), ("assistant", "answer"))
    ]


def validate_memory(
    candidate: Any, previous: dict[str, Any], batch: list[dict[str, Any]]
) -> dict[str, Any]:
    memory = NovelMemory.model_validate(candidate).model_dump(mode="json")
    allowed = {turn["id"] for turn in batch}
    allowed.update(source for item in previous.get("items", []) for source in item["source_ids"])
    if any(not set(item["source_ids"]) <= allowed for item in memory["items"]):
        raise ValueError("novel_memory_source_invalid")
    if tokens(memory) > MAX_MEMORY_TOKENS:
        raise ValueError("novel_memory_too_large")
    return {**memory, "through_id": batch[-1]["id"], "policy": POLICY}


def compact_memory(payload: dict[str, Any], api_key: str, model_id: str) -> Any:
    prompt = load_prompt("novel_context_compactor")
    data = {"response_schema": NovelMemory.model_json_schema(), "untrusted_data": payload}
    digest = canonical_json_sha256(data)
    return DeepSeekProseRewriterProvider().rewrite_scene(
        ProseRewriterRequest(
            model_id=model_id,
            api_key=api_key,
            system_prompt=prompt.system_prompt,
            prompt_version=prompt.version,
            prompt_hash=prompt.system_prompt_sha256,
            input_payload=data,
            input_hash=digest,
            component_input_hash=digest,
            request_fingerprint=digest,
            rewrite_round=1,
            remaining_scene_call_budget=1,
            max_output_tokens=4096,
        )
    )


def govern_context(payload: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    """Trim only optional setting records and neighboring excerpts; never edit the target."""
    result = {
        **payload,
        "context_memory": memory,
        "context_rules": (
            "当前 instruction 与当前 target 是本轮依据。context_memory 和 history 是历史资料，"
            "旧选段要求不自动适用于当前选段；模型历史答复不是已采纳事实。"
            "只有 edit_status.accepted 表示历史修改被采纳，当前正文仍以 target 为准。"
        ),
    }
    prior_manifest = result.pop("context_manifest", {})
    removed = list(prior_manifest.get("removed_optional_blocks", []))
    while tokens(result) > 48000 or len(json.dumps(result, ensure_ascii=False)) > 58000:
        settings = result.get("related_settings", [])
        if settings:
            result["related_settings"] = settings[:-1]
            removed.append("related_setting")
            continue
        field = next(
            (
                name
                for name in ("previous_chapter", "next_chapter", "before_context", "after_context")
                if result.get(name)
            ),
            None,
        )
        if field is None:
            raise ValueError("novel_context_protected_budget_exceeded")
        result[field] = None if field.endswith("chapter") else ""
        removed.append(field)
    result["context_manifest"] = {
        "policy": POLICY,
        "estimated_payload_tokens": tokens(result),
        "removed_optional_blocks": removed,
        "memory_through_id": memory.get("through_id", 0),
    }
    return result
