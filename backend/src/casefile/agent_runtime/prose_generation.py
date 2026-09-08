"""Bounded generation repair and nonduplicated provider views; no persistence ownership."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from casefile.agent_runtime.prose_context import scene_generation_context
from casefile.domain.narrative_compiler import CompilerContractError, canonical_json_sha256
from casefile_contracts import SceneRenderCandidate

GENERATION_POLICY = "prose-generation-repair-v5"


def generation_length_contract(profile: dict[str, Any]) -> dict[str, Any]:
    limits = profile["prose"]["target_scene_chars"]
    return {
        "unit": "unicode_code_points_in_block_text_only",
        "min_chars": limits["min"],
        "max_chars": limits["max"],
        "target_chars": (limits["min"] + limits["max"]) // 2,
        "enforcement": "production_hard_range",
        "hard_gate": True,
    }


def compact_checklist(checklist: dict[str, Any]) -> dict[str, Any]:
    """The complete scene_context is supplied once beside this checklist."""
    return {key: value for key, value in checklist.items() if key != "scene_context"}


def generation_view(checklist: dict[str, Any]) -> dict[str, Any]:
    context = checklist["scene_context"]
    previous = context.get("previous_scene_render")
    labels = {
        item["object_ref"]["object_id"]: item["label"] for item in context.get("object_catalog", [])
    }
    return {
        "checklist": compact_checklist(checklist),
        "scene_context": scene_generation_context(context),
        "continuity_reference": None
        if previous is None
        else {
            "scene_id": previous["scene_id"],
            "render_hash": canonical_json_sha256(previous),
            "text": "\n\n".join(block["text"] for block in previous["blocks"]),
            "purpose": "already_completed_previous_scene_reference_only",
        },
        "current_assignment": {
            "scene_id": checklist["scene_id"],
            "objective": context.get("objective"),
            "outcome": context.get("outcome"),
            "beats": [b["directive"] for b in context.get("beats", [])],
            "participants": [
                labels.get(r["object_id"], r["object_id"])
                for r in context.get("participant_refs", [])
            ],
        },
    }


def generation_focus(request: Any) -> str:
    """Repeat the small current assignment after the large reference data, never the history."""
    import json

    data = request.input_payload["untrusted_data"]
    return (
        "当前待写场景任务数据（不是上一场）：\n"
        + json.dumps(
            {
                "assignment": data.get("current_assignment"),
                "length": generation_length_contract(data["profile"]),
                "repair_issue": request.input_payload.get("generation_repair", {}).get("issue"),
                "repair_directive": request.input_payload.get("generation_repair", {}).get(
                    "repair_directive"
                ),
                "forbidden_output_hashes": request.input_payload.get("generation_repair", {}).get(
                    "forbidden_output_hashes"
                ),
                "required_changes": data.get("repair_findings"),
            },
            ensure_ascii=False,
        )
        + "\n请按当前任务写完整的新正文或完整替代稿；"
        "不要转换、照抄上场正文，也不要保留错误旧稿再追加。"
    )


def _text(blocks: list[dict[str, Any]]) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", "".join(b["text"] for b in blocks)))


def generation_issue(candidate: Any, request: Any, component: str) -> dict[str, Any] | None:
    try:
        parsed = SceneRenderCandidate.model_validate(candidate).model_dump(mode="json")
    except ValidationError as error:
        if any(item["type"] not in {"string_too_long", "too_long"} for item in error.errors()):
            return None  # Other structural failures go through the full validator.
        parsed = candidate
    data = request.input_payload["untrusted_data"]
    blocks = [b for b in parsed["blocks"] if b["text"].strip()]
    previous = data.get("scene_context", data["checklist"].get("scene_context", {})).get(
        "previous_scene_render"
    )
    continuity = data.get("continuity_reference")
    current = data.get("current_render")
    code = None
    if (previous and _text(blocks) == _text(previous["blocks"])) or (
        continuity and _text(blocks) == _text([{"text": continuity["text"]}])
    ):
        code = "prose_generation_repeated_previous_scene"
    elif component == "prose_rewrite" and current and _text(blocks) == _text(current["blocks"]):
        code = "prose_generation_no_progress"
    count = sum(len(block["text"]) for block in blocks)
    limits = data["profile"]["prose"]["target_scene_chars"]
    if code is None and not limits["min"] <= count <= limits["max"]:
        code = "compiler_scene_render_length_out_of_bounds"
    return (
        None
        if code is None
        else {
            "code": code,
            "path": ["blocks"],
            "actual_chars": count,
            "min_chars": limits["min"],
            "max_chars": limits["max"],
            "block_lengths": [len(block["text"]) for block in blocks],
            "candidate_hash": canonical_json_sha256(parsed),
        }
    )


def prepare_generation_result(
    provider: Any,
    invoke: Callable[..., Any],
    request: Any,
    result: Any,
    component: str,
    validate_binding: Callable[..., None],
    recover_call: Any = None,
) -> tuple[Any, Any]:
    """One repair request at most; each real response is journaled by the provider port."""
    validate_binding(result, request)
    if not getattr(provider, "allow_generation_repair", False):
        return result, request
    issue = generation_issue(result.candidate, request, component)
    if issue is None:
        return result, request
    provider.record_generation_failure(result.request_fingerprint, issue)
    if issue["code"] == "prose_generation_no_progress":
        return result, request
    component_hash = canonical_json_sha256(
        {
            "policy": GENERATION_POLICY,
            "source": request.component_input_hash,
            "failed_output": result.output_hash,
            "issue": issue,
        }
    )
    repair_directive = _generation_repair_directive(issue["code"])
    payload = {
        **request.input_payload,
        "server_bindings": {
            **request.input_payload["server_bindings"],
            "component_input_hash": component_hash,
        },
        "generation_repair": {
            "attempt": 1,
            "policy": GENERATION_POLICY,
            "failed_output_hash": result.output_hash,
            "failed_candidate": result.candidate,
            "issue": issue,
            "repair_directive": repair_directive,
            "forbidden_output_hashes": [issue["candidate_hash"]],
        },
    }
    input_hash = canonical_json_sha256(payload)
    repaired_request = replace(
        request,
        input_payload=payload,
        input_hash=input_hash,
        component_input_hash=component_hash,
        request_fingerprint=canonical_json_sha256(
            {
                "source_request": request.request_fingerprint,
                "repair_input": input_hash,
                "policy": GENERATION_POLICY,
            }
        ),
    )
    recovered = recover_call(repaired_request.request_fingerprint) if recover_call else None
    repaired = (
        replace(recovered, recovered=True) if recovered is not None else invoke(repaired_request)
    )
    validate_binding(repaired, repaired_request)
    issue = generation_issue(repaired.candidate, repaired_request, component)
    if issue is not None:
        provider.record_generation_failure(repaired.request_fingerprint, issue)
    return replace(repaired, generation_call_count=2), repaired_request


def _generation_repair_directive(code: str) -> dict[str, Any]:
    common = {
        "output": "返回当前场景的完整替代正文，不返回补丁或修复说明。",
        "verification": "新候选的规范化正文及候选哈希必须不同于禁止输出。",
    }
    if code == "prose_generation_no_progress":
        return {
            **common,
            "required_action": (
                "依据 repair_findings 逐项改写触发失败的动作、对白或叙述，并连同相邻句重构；"
                "即使你认为旧稿已经正确，也不得再次返回 current_render 或 failed_candidate。"
            ),
        }
    if code == "prose_generation_repeated_previous_scene":
        return {
            **common,
            "required_action": (
                "从 current_assignment 的目标、beats 和 outcome 重新起稿；"
                "不得复述、改格式或近似复制"
                " continuity_reference / previous_scene_render。"
            ),
        }
    return {
        **common,
        "required_action": (
            "在保留全部权威语义的前提下重新组织完整正文，使字符数落入 length_contract；"
            "不得机械截断或再次返回失败候选。"
        ),
    }


def validate_generation_result(provider: Any, result: Any, request: Any, component: str) -> None:
    if getattr(provider, "allow_generation_repair", False):
        issue = generation_issue(result.candidate, request, component)
        if issue is not None:
            raise CompilerContractError(issue["code"])
