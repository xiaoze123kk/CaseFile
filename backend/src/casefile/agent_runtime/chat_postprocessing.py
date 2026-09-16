"""Post-finalizer safe-patch preparation, separate from the retry loop.

This adapter owns the existing materialization events; validation Hook handlers
remain side-effect free. No patch is applied here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from casefile.agent_runtime.chat_safe_patches import (
    materialize_unique_safe_patches,
    server_gate_audit_suggestions,
)
from casefile.agent_runtime.chat_validation import ValidationIssue, target_label
from casefile.agent_runtime.chat_versions import SAFE_PATCH_PROMPT_VERSIONS
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult


def prepare_safe_patch_candidate(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    materialization_history: list[dict[str, Any]],
) -> tuple[CaseFileChatResult, tuple[ValidationIssue, ...]]:
    server_gate_issues: tuple[ValidationIssue, ...] = ()
    if (
        request.prompt_version in SAFE_PATCH_PROMPT_VERSIONS
        and request.route is not None
        and request.route.execution_profile.get("primary_intent") == "logic_audit"
    ):
        candidate_payload = result.candidate.model_dump(mode="json")
        raw_suggestions = candidate_payload.get("suggestions")
        if isinstance(raw_suggestions, list):
            proposals = [item for item in raw_suggestions if isinstance(item, dict)]
            gate = server_gate_audit_suggestions(request, proposals)
            ledger = result.tool_ledger or request.frozen_tool_ledger
            if isinstance(ledger, dict):
                gate = replace(
                    gate,
                    registry=replace(
                        gate.registry,
                        ledger_hash=str(ledger.get("ledger_hash") or ""),
                    ),
                )
            rejected_indexes = {failure.suggestion_index for failure in gate.failures} | {
                discard.suggestion_index for discard in gate.discards
            }
            safe_suggestions = [
                suggestion
                for index, suggestion in enumerate(proposals)
                if index not in rejected_indexes
            ]
            materialized, changes = materialize_unique_safe_patches(
                safe_suggestions,
                gate.registry,
            )
            if materialized != raw_suggestions:
                candidate_payload["suggestions"] = materialized
                result = replace(
                    result,
                    candidate=result.candidate.__class__.model_validate(candidate_payload),
                )
            result = replace(result, safe_patch_registry=gate.registry.as_dict())
            request.emit(
                "model.safe_patch_gated",
                "validating",
                {
                    "source": "server_post_finalizer_gate",
                    "safe_count": len(gate.registry.candidates),
                    "rejected": [failure.as_dict() for failure in gate.failures],
                    "discarded": [discard.as_dict() for discard in gate.discards],
                },
            )
            if changes:
                change_payloads = [change.as_dict() for change in changes]
                materialization_history.extend(change_payloads)
                request.emit(
                    "model.safe_patch_materialized",
                    "validating",
                    {
                        "ledger_hash": gate.registry.ledger_hash,
                        "source": gate.registry.source,
                        "changes": change_payloads,
                    },
                )
            if gate.failures:
                preserved = sorted(
                    target_label(item.get("object_id"), item.get("path")) for item in materialized
                )
                server_gate_issues = tuple(
                    ValidationIssue(
                        code="audit_suggestion_server_gate_failed",
                        stage="patch",
                        path=f"/suggestions/{failure.suggestion_index}",
                        message="审计建议未通过服务器确定性补丁门禁。",
                        repairable=True,
                        details={
                            "extra": [failure.target],
                            "preserve": preserved,
                            "object_id": failure.object_id,
                            "path": failure.path,
                            "reason_code": failure.reason_code,
                            "value_json": proposals[failure.suggestion_index].get("value_json"),
                            "validation": failure.validation,
                            "simulation": failure.simulation,
                        },
                    )
                    for failure in gate.failures
                )

    return result, server_gate_issues
