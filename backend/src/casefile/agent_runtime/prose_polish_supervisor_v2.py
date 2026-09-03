"""Server-owned B3 v4 pointwise Quality and bounded-patch supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    ProseCouncilExecution,
    ProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_patch_polisher import (
    PROSE_PATCH_POLISHER_MODEL_ID,
    ProsePatchPolisherExecution,
    ProsePatchPolisherProvider,
    execute_prose_patch_polisher,
)
from casefile.agent_runtime.prose_quality_assessor import (
    PROSE_QUALITY_ASSESSMENT_MODEL_ID,
    ProseQualityAssessmentExecution,
    ProseQualityAssessmentProvider,
    execute_quality_assessment,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_editable_window_manifest,
    canonical_json_sha256,
    finalize_scene_render,
    resolve_quality_delta,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_POLISH_SUPERVISOR_V2_VERSION = "prose-polish-supervisor-v2"


@dataclass(frozen=True, slots=True)
class ProsePolishSupervisorV2Execution:
    status: Literal[
        "finalized_original", "finalized_polished", "protocol_failed", "inconclusive"
    ]
    original_render: dict[str, Any] | None
    before_assessment: ProseQualityAssessmentExecution | None
    window_manifest: dict[str, Any] | None
    polish: ProsePatchPolisherExecution | None
    preservation: ProseCouncilExecution | None
    after_assessment: ProseQualityAssessmentExecution | None
    quality_delta: dict[str, Any] | None
    audit_record: dict[str, Any] | None
    accepted_render: dict[str, Any] | None
    selection_reason: str | None
    error_code: str | None = None


def execute_prose_polish_supervisor_v2(
    assessment_provider: ProseQualityAssessmentProvider,
    polisher_provider: ProsePatchPolisherProvider,
    judge_provider: ProseJudgeProvider,
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    original_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_model_id: str,
    generation_model_id: str,
    api_key: str,
) -> ProsePolishSupervisorV2Execution:
    """Run assessment → bounded patch → preservation → deterministic delta."""

    if (
        quality_model_id != PROSE_QUALITY_ASSESSMENT_MODEL_ID
        or generation_model_id != PROSE_PATCH_POLISHER_MODEL_ID
        or generation_model_id != PROSE_COUNCIL_MODEL_ID
    ):
        return _terminal(
            "protocol_failed",
            error_code="prose_polish_supervisor_v2_model_id_not_frozen",
        )
    try:
        original = validate_scene_render(
            original_render, checklist=checklist, profile=profile
        ).model_dump(mode="json")
        original_consensus = validate_semantic_acceptance(
            semantic_consensus,
            checklist=checklist,
            render=original,
            profile=profile,
        ).model_dump(mode="json")
    except CompilerContractError as error:
        return _terminal("protocol_failed", error_code=str(error))

    before = execute_quality_assessment(
        assessment_provider,
        checklist=checklist,
        render=original,
        profile=profile,
        semantic_consensus=original_consensus,
        model_id=quality_model_id,
        api_key=api_key,
    )
    if before.status != "completed" or before.assessment is None:
        return _terminal(
            before.status,
            original=original,
            before=before,
            error_code=before.error_code,
        )
    try:
        windows = build_editable_window_manifest(
            assessment=before.assessment,
            checklist=checklist,
            render=original,
            profile=profile,
            semantic_consensus=original_consensus,
            evidence_catalog=build_server_evidence_catalog(original),
        )
    except CompilerContractError as error:
        return _terminal(
            "protocol_failed", original=original, before=before, error_code=str(error)
        )

    if windows.status == "noop":
        return _finalized_original(
            original=original,
            checklist=checklist,
            profile=profile,
            reason="quality_noop",
            before=before,
            window_manifest=windows.manifest,
        )
    if windows.status == "scope_exceeded":
        return _finalized_original(
            original=original,
            checklist=checklist,
            profile=profile,
            reason="polish_scope_rollback",
            before=before,
            window_manifest=windows.manifest,
        )

    polish = execute_prose_patch_polisher(
        polisher_provider,
        profile=profile,
        checklist=checklist,
        current_render=original,
        semantic_consensus=original_consensus,
        quality_assessment=before.assessment,
        window_manifest=windows.manifest,
        model_id=generation_model_id,
        api_key=api_key,
    )
    if polish.status != "completed":
        return _terminal(
            polish.status,
            original=original,
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
            error_code=polish.error_code,
        )
    if polish.abstained or polish.render is None:
        return _finalized_original(
            original=original,
            checklist=checklist,
            profile=profile,
            reason="quality_rollback",
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
        )

    preservation = execute_semantic_council(
        judge_provider,
        checklist=checklist,
        render=polish.render,
        profile=profile,
        policy=FULL_COUNCIL_POLICY,
        model_id=generation_model_id,
        api_key=api_key,
    )
    if preservation.status != "completed" or preservation.consensus is None:
        return _terminal(
            preservation.status,
            original=original,
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
            preservation=preservation,
            error_code=preservation.error_code,
        )
    if preservation.consensus["scene_verdict"] != "pass":
        return _finalized_original(
            original=original,
            checklist=checklist,
            profile=profile,
            reason="polish_semantic_rollback",
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
            preservation=preservation,
        )

    after = execute_quality_assessment(
        assessment_provider,
        checklist=checklist,
        render=polish.render,
        profile=profile,
        semantic_consensus=preservation.consensus,
        model_id=quality_model_id,
        api_key=api_key,
    )
    if after.status != "completed" or after.assessment is None:
        return _terminal(
            after.status,
            original=original,
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
            preservation=preservation,
            after=after,
            error_code=after.error_code,
        )
    try:
        delta = resolve_quality_delta(
            original_assessment=before.assessment,
            polished_assessment=after.assessment,
            checklist=checklist,
            original_render=original,
            polished_render=polish.render,
            profile=profile,
            original_semantic_consensus=original_consensus,
            preservation_consensus=preservation.consensus,
        ).model_dump(mode="json")
    except CompilerContractError as error:
        return _terminal(
            "protocol_failed",
            original=original,
            before=before,
            window_manifest=windows.manifest,
            polish=polish,
            preservation=preservation,
            after=after,
            error_code=str(error),
        )
    selected = polish.render if delta["accept_polished"] else original
    reason = delta["selection_reason"]
    audit_record = {
        "supervisor": PROSE_POLISH_SUPERVISOR_V2_VERSION,
        "original_hash": canonical_json_sha256(original),
        "original_assessment_hash": canonical_json_sha256(before.assessment),
        "window_manifest_hash": canonical_json_sha256(windows.manifest),
        "patch_hash": canonical_json_sha256(polish.candidate),
        "polished_hash": canonical_json_sha256(polish.render),
        "preservation_hash": canonical_json_sha256(preservation.consensus),
        "polished_assessment_hash": canonical_json_sha256(after.assessment),
        "quality_delta_hash": canonical_json_sha256(delta),
        "selection_reason": reason,
    }
    accepted = _accepted(
        selected,
        original,
        checklist,
        profile,
        reason,
        audit_record,
    )
    return _terminal(
        "finalized_polished" if delta["accept_polished"] else "finalized_original",
        original=original,
        before=before,
        window_manifest=windows.manifest,
        polish=polish,
        preservation=preservation,
        after=after,
        delta=delta,
        audit_record=audit_record,
        accepted=accepted,
        selection_reason=reason,
    )


def _finalized_original(
    *,
    original: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    reason: str,
    before: ProseQualityAssessmentExecution,
    window_manifest: dict[str, Any],
    polish: ProsePatchPolisherExecution | None = None,
    preservation: ProseCouncilExecution | None = None,
) -> ProsePolishSupervisorV2Execution:
    binding = {
        "supervisor": PROSE_POLISH_SUPERVISOR_V2_VERSION,
        "original_hash": canonical_json_sha256(original),
        "original_assessment_hash": canonical_json_sha256(before.assessment),
        "window_manifest_hash": canonical_json_sha256(window_manifest),
        "patch_hash": canonical_json_sha256(polish.candidate) if polish else None,
        "polished_hash": canonical_json_sha256(polish.render)
        if polish and polish.render
        else None,
        "preservation_hash": canonical_json_sha256(preservation.consensus)
        if preservation and preservation.consensus
        else None,
        "polished_assessment_hash": None,
        "quality_delta_hash": None,
        "selection_reason": reason,
    }
    accepted = _accepted(
        original, original, checklist, profile, reason, binding
    )
    return _terminal(
        "finalized_original",
        original=original,
        before=before,
        window_manifest=window_manifest,
        polish=polish,
        preservation=preservation,
        audit_record=binding,
        accepted=accepted,
        selection_reason=reason,
    )


def _accepted(
    selected: dict[str, Any],
    original: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    reason: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    return finalize_scene_render(
        selected,
        original_render=original,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_json_sha256(binding),
        selection_reason=reason,
    ).model_dump(mode="json")


def _terminal(
    status: str,
    *,
    original: dict[str, Any] | None = None,
    before: ProseQualityAssessmentExecution | None = None,
    window_manifest: dict[str, Any] | None = None,
    polish: ProsePatchPolisherExecution | None = None,
    preservation: ProseCouncilExecution | None = None,
    after: ProseQualityAssessmentExecution | None = None,
    delta: dict[str, Any] | None = None,
    audit_record: dict[str, Any] | None = None,
    accepted: dict[str, Any] | None = None,
    selection_reason: str | None = None,
    error_code: str | None = None,
) -> ProsePolishSupervisorV2Execution:
    return ProsePolishSupervisorV2Execution(
        status=status,  # type: ignore[arg-type]
        original_render=original,
        before_assessment=before,
        window_manifest=window_manifest,
        polish=polish,
        preservation=preservation,
        after_assessment=after,
        quality_delta=delta,
        audit_record=audit_record,
        accepted_render=accepted,
        selection_reason=selection_reason,
        error_code=error_code,
    )


__all__ = [
    "PROSE_POLISH_SUPERVISOR_V2_VERSION",
    "ProsePolishSupervisorV2Execution",
    "execute_prose_polish_supervisor_v2",
]
