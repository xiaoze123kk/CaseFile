"""Bounded editor context and model output, independent of compiler IR and SQL."""

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    ProseRewriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile_contracts import NovelEditorCandidate

VERSION = "novel-collaboration-v1"
MAX_CONTEXT_CHARS = 60000


def prepare_context(
    chapters: list[dict[str, str]], request: dict[str, Any], history: list[dict[str, str]]
) -> dict[str, Any]:
    chapter = next((c for c in chapters if c["id"] == request["chapter_id"]), None)
    if chapter is None:
        raise ValueError("novel_chapter_missing")
    anchor = request["anchor"]
    if request["scope"] == "selection":
        if (
            not anchor
            or anchor["chapter_id"] != chapter["id"]
            or not 0 <= anchor["start"] < anchor["end"] <= len(chapter["text"])
            or chapter["text"][anchor["start"] : anchor["end"]] != anchor["text"]
        ):
            raise ValueError("novel_selection_stale")
        start, end = anchor["start"], anchor["end"]
    else:
        start, end = 0, len(chapter["text"])
    if end - start > 40000 or not chapter["text"][start:end].strip():
        raise ValueError("novel_target_too_large_or_empty")
    payload = {
        "mode": request["mode"],
        "instruction": request["instruction"],
        "chapter_title": chapter["title"],
        "target": chapter["text"][start:end],
        "target_start": start,
        "before_context": chapter["text"][max(0, start - 2000) : start],
        "after_context": chapter["text"][end : end + 2000],
        "history": history,
    }
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        raise ValueError("novel_context_too_large")
    return payload


def validate_edits(candidate: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = NovelEditorCandidate.model_validate(candidate).model_dump(mode="json")
    target = payload["target"]
    edits = []
    for item in parsed["edits"]:
        if not item["reason"].strip():
            raise ValueError("novel_edit_reason_missing")
        before = item["before"]
        if not before or target.count(before) != 1:
            raise ValueError("novel_edit_ambiguous_anchor")
        if before == item["after"]:
            continue
        start = target.index(before) + payload["target_start"]
        edits.append({**item, "start": start, "end": start + len(before)})
    edits.sort(key=lambda e: e["start"])
    if any(a["end"] > b["start"] for a, b in zip(edits, edits[1:], strict=False)):
        raise ValueError("novel_edits_overlap")
    if payload["mode"] == "discuss" and edits:
        raise ValueError("novel_discuss_cannot_edit")
    return edits


class NovelCollaborationProvider:
    def edit(
        self, payload: dict[str, Any], api_key: str, model_id: str, repair: str | None = None
    ) -> Any:
        prompt = load_prompt("novel_collaboration")
        data = {
            "response_schema": NovelEditorCandidate.model_json_schema(),
            "untrusted_data": payload,
            "protocol_repair": repair,
        }
        digest = canonical_json_sha256(data)
        request = ProseRewriterRequest(
            model_id=model_id,
            api_key=api_key,
            system_prompt=prompt.system_prompt,
            prompt_version=VERSION,
            prompt_hash=prompt.system_prompt_sha256,
            input_payload=data,
            input_hash=digest,
            component_input_hash=digest,
            request_fingerprint=digest,
            rewrite_round=1,
            remaining_scene_call_budget=2,
        )
        return DeepSeekProseRewriterProvider().rewrite_scene(request)

    def discuss(
        self, payload: dict[str, Any], api_key: str, model_id: str, emit: Callable[[str], None]
    ) -> tuple[str, dict[str, int]]:
        prompt = load_prompt("novel_collaboration")
        answer = ""
        usage = {}
        with OpenAI(
            api_key=api_key, base_url="https://api.deepseek.com", max_retries=0, timeout=120
        ) as client:
            stream = client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "system",
                        "content": prompt.system_prompt
                        + "\n当前为讨论模式：直接输出自然语言回答，不输出 JSON，不修改正文。",
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                stream=True,
                stream_options={"include_usage": True},
                max_tokens=4096,
                extra_body={"thinking": {"type": "disabled"}},
            )
            for chunk in stream:
                if chunk.usage:
                    usage = {
                        "requests": 1,
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }
                if chunk.choices:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        answer += delta
                        emit(delta)
            if not answer.strip():
                raise ValueError("novel_discussion_empty")
        return answer, usage
