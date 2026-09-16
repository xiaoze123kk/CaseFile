"""Single-call whole-novel Plan-Execute reconciliation."""

from __future__ import annotations

from typing import Any, Final

from casefile.agent_runtime.plan_execute import (
    ExecutionPlan,
    PlanCheckin,
    PlanReconciliationReport,
    plan_hash,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_skills import completion_body
from casefile.agent_runtime.prose_writer import (
    DeepSeekProseWriterProvider,
    ProseWriterProvider,
    ProseWriterProviderResult,
    ProseWriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256

PROSE_PLAN_RECONCILIATION_PROMPT_VERSION: Final = "prose-plan-reconciliation-v1"
PROSE_PLAN_RECONCILIATION_SCHEMA_ID: Final = "casefile.plan-reconciliation.v1"
PROSE_PLAN_RECONCILIATION_SCHEMA: Final = PlanReconciliationReport.model_json_schema()
PROSE_PLAN_RECONCILIATION_MAX_OUTPUT_TOKENS: Final = 32_768
PROSE_PLAN_EVIDENCE_CHARS_PER_SCENE: Final = 320


class DeepSeekPlanReconciliationProvider(DeepSeekProseWriterProvider):
    def completion_body(self, request: ProseWriterRequest) -> dict[str, Any]:
        return completion_body(
            request,
            PROSE_PLAN_RECONCILIATION_SCHEMA,
            "逐项目标核对实际正文证据；只输出最终对账 JSON。",
        )


def build_plan_reconciliation_request(
    *,
    plan: ExecutionPlan,
    accepted_scenes: list[dict[str, Any]],
    checkins: list[PlanCheckin],
    model_id: str,
    api_key: str,
) -> ProseWriterRequest:
    prompt = load_prompt("prose_plan_reconciliation", PROSE_PLAN_RECONCILIATION_PROMPT_VERSION)
    evidence = _bounded_evidence(accepted_scenes)
    payload = {
        "execution_plan": plan.model_dump(mode="json"),
        "evidence": evidence,
        "checkins": [item.model_dump(mode="json") for item in checkins],
        "output_schema_id": PROSE_PLAN_RECONCILIATION_SCHEMA_ID,
    }
    input_hash = canonical_json_sha256(payload)
    component_input_hash = canonical_json_sha256(
        {
            "component": "prose_plan_reconciliation",
            "plan_hash": plan_hash(plan),
            "evidence_hash": canonical_json_sha256(evidence),
            "checkin_hashes": [canonical_json_sha256(item.model_dump()) for item in checkins],
            "prompt_hash": prompt.system_prompt_sha256,
            "schema_hash": canonical_json_sha256(PROSE_PLAN_RECONCILIATION_SCHEMA),
        }
    )
    fingerprint = canonical_json_sha256(
        {
            "component_input_hash": component_input_hash,
            "model_id": model_id,
            "max_turns": 1,
            "network_retries": 0,
            "temperature": 0,
            "thinking_enabled": False,
        }
    )
    return ProseWriterRequest(
        model_id=model_id,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        component_input_hash=component_input_hash,
        request_fingerprint=fingerprint,
        remaining_scene_call_budget=1,
        network_retries=0,
        max_output_tokens=PROSE_PLAN_RECONCILIATION_MAX_OUTPUT_TOKENS,
    )


def execute_plan_reconciliation(
    provider: ProseWriterProvider,
    *,
    plan: ExecutionPlan,
    accepted_scenes: list[dict[str, Any]],
    checkins: list[PlanCheckin],
    model_id: str,
    api_key: str,
) -> tuple[PlanReconciliationReport | None, ProseWriterProviderResult | None]:
    request = build_plan_reconciliation_request(
        plan=plan,
        accepted_scenes=accepted_scenes,
        checkins=checkins,
        model_id=model_id,
        api_key=api_key,
    )
    method = getattr(provider, "reconcile_plan", None) or provider.write_scene
    result = method(request)
    if (
        result.request_fingerprint != request.request_fingerprint
        or result.input_hash != request.input_hash
        or result.component_input_hash != request.component_input_hash
        or result.prompt_hash != request.prompt_hash
        or result.candidate is None
    ):
        return None, result
    try:
        report = PlanReconciliationReport.model_validate(result.candidate)
    except ValueError:
        return None, result
    return report, result


def _bounded_evidence(accepted_scenes: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for render in sorted(accepted_scenes, key=lambda item: int(item["scene_ordinal"])):
        text = "\n\n".join(str(block.get("text", "")) for block in render.get("blocks", []))
        rows.append(
            {
                "scene_id": render["scene_id"],
                "scene_ordinal": render["scene_ordinal"],
                "render_hash": canonical_json_sha256(render),
                "text_excerpt": text[:PROSE_PLAN_EVIDENCE_CHARS_PER_SCENE],
                "text_truncated": len(text) > PROSE_PLAN_EVIDENCE_CHARS_PER_SCENE,
            }
        )
    return {
        "policy": "scene-prefix-320-v1",
        "accepted_scenes": rows,
    }


__all__ = [
    "DeepSeekPlanReconciliationProvider",
    "PROSE_PLAN_RECONCILIATION_PROMPT_VERSION",
    "build_plan_reconciliation_request",
    "execute_plan_reconciliation",
]
