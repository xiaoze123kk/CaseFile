"""LLM-owned editorial decisions, using the journaled Rewrite transport envelope."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_rewriter import (
    ProseRewriterInfrastructureError,
    ProseRewriterProvider,
    ProseRewriterProviderResult,
    ProseRewriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile_contracts import ProseRevisionDecisionCandidate as RevisionDecision


@dataclass(frozen=True, slots=True)
class RevisionExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    report: dict[str, Any] | None
    call: ProseRewriterProviderResult | None
    error_code: str | None = None


def validate_revision_decision(
    candidate: Any, check_ids: list[str], *, exhausted: bool
) -> dict[str, Any]:
    """Shared editorial coverage, fatal-retention and revision-budget validation."""
    decision = RevisionDecision.model_validate(candidate).model_dump(mode="json")
    expected = set(check_ids)
    actual = [item["check_id"] for item in decision["findings"]]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("finding_binding")
    if exhausted and decision["action"] not in {"retain", "stop"}:
        raise ValueError("budget")
    if decision["action"] == "retain" and any(
        f["severity"] == "fatal" for f in decision["findings"]
    ):
        raise ValueError("fatal_retention")
    if (
        decision["action"] in {"local_revision", "full_rewrite"}
        and not decision["revision_plan"].strip()
    ):
        raise ValueError("plan_missing")
    return decision


def execute_revision_decision(
    provider: ProseRewriterProvider,
    source: ProseRewriterRequest,
    *,
    exhausted: bool,
) -> RevisionExecution:
    """Assess contested findings; never alter the frozen Judge or planning artifacts."""
    prompt = load_prompt("prose_revision")
    data = source.input_payload["untrusted_data"]
    payload = {
        "output_schema_id": "compiler.prose-revision-decision.v1",
        "response_schema": RevisionDecision.model_json_schema(),
        "untrusted_data": data,
        "repair_budget_exhausted": exhausted,
        "source_request_fingerprint": source.request_fingerprint,
    }
    digest = canonical_json_sha256(payload)
    request = replace(
        source,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=digest,
        component_input_hash=digest,
        request_fingerprint=canonical_json_sha256(
            {"input": digest, "prompt": prompt.system_prompt_sha256, "model": source.model_id}
        ),
    )
    try:
        call = provider.rewrite_scene(request)
    except ProseRewriterInfrastructureError as error:
        return RevisionExecution("inconclusive", None, None, str(error))
    if (
        call.request_fingerprint != request.request_fingerprint
        or call.input_hash != digest
        or call.component_input_hash != digest
        or call.prompt_hash != request.prompt_hash
        or call.prompt_version != request.prompt_version
        or call.model_id != request.model_id
        or canonical_json_sha256(call.request_payload) != digest
    ):
        return RevisionExecution("protocol_failed", None, call, "prose_revision_binding_invalid")
    try:
        decision = validate_revision_decision(
            call.candidate,
            [item["check_id"] for item in data["repair_findings"]],
            exhausted=exhausted,
        )
    except (ValidationError, ValueError):
        return RevisionExecution("protocol_failed", None, call, "prose_revision_decision_invalid")
    report = {
        "schema_id": "compiler.prose-revision-decision.v1",
        "scene_id": data["current_render"]["scene_id"],
        "render_hash": canonical_json_sha256(data["current_render"]),
        "input_hash": digest,
        "repair_budget_exhausted": exhausted,
        **decision,
    }
    return RevisionExecution("completed", report, call)
