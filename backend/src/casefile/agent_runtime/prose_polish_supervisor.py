"""Server-owned N4.5 Quality findings, polish, preservation, and selection loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    ProseCouncilExecution,
    ProseJudgeProvider,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_polisher import (
    PROSE_POLISHER_MODEL_ID,
    ProsePolisherExecution,
    ProsePolisherProvider,
    execute_prose_polisher,
)
from casefile.agent_runtime.prose_quality_config import (
    QUALITY_V2,
    ProseQualityConfig,
    validate_quality_config,
)
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_MODEL_ID,
    MirroredQualityExecution,
    ProseQualityCriticProvider,
    ProseQualityExecution,
    execute_mirrored_pairwise_quality,
    execute_quality_findings,
)
from casefile.agent_runtime.prose_runtime import ComponentObserver, ignore_component
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    finalize_scene_render,
    validate_quality_findings_report,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_POLISH_SUPERVISOR_VERSION = "prose-polish-supervisor-v1"


@dataclass(frozen=True, slots=True)
class ProsePolishSupervisorExecution:
    status: Literal["finalized_original", "finalized_polished", "protocol_failed", "inconclusive"]
    original_render: dict[str, Any] | None
    findings: ProseQualityExecution | None
    polish: ProsePolisherExecution | None
    preservation: ProseCouncilExecution | None
    pairwise: MirroredQualityExecution | None
    accepted_render: dict[str, Any] | None
    selection_reason: str | None
    error_code: str | None = None


def execute_prose_polish_supervisor(
    quality_provider: ProseQualityCriticProvider,
    polisher_provider: ProsePolisherProvider,
    judge_provider: ProseJudgeProvider,
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    original_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_model_id: str,
    generation_model_id: str,
    api_key: str,
    observe: ComponentObserver = ignore_component,
    quality_config: ProseQualityConfig = QUALITY_V2,
    frozen_findings: dict[str, Any] | None = None,
    reverse_first: bool = False,
) -> ProsePolishSupervisorExecution:
    """Run the bounded B3 path and never expose model-owned acceptance control."""

    try:
        validate_quality_config(quality_config)
    except ValueError as error:
        return _terminal("protocol_failed", None, None, None, None, None, None, str(error))
    if (
        quality_model_id != PROSE_QUALITY_MODEL_ID
        or generation_model_id != PROSE_POLISHER_MODEL_ID
        or generation_model_id != PROSE_COUNCIL_MODEL_ID
    ):
        return _terminal(
            "protocol_failed",
            None,
            None,
            None,
            None,
            None,
            None,
            "prose_polish_supervisor_model_id_not_frozen",
        )
    try:
        original = validate_scene_render(
            original_render, checklist=checklist, profile=profile
        ).model_dump(mode="json")
        validate_semantic_acceptance(
            semantic_consensus,
            checklist=checklist,
            render=original,
            profile=profile,
        )
    except CompilerContractError as error:
        return _terminal("protocol_failed", None, None, None, None, None, None, str(error))
    if frozen_findings is None:
        findings = execute_quality_findings(
            quality_provider,
            checklist=checklist,
            render=original,
            profile=profile,
            semantic_consensus=semantic_consensus,
            model_id=quality_model_id,
            api_key=api_key,
        )
    else:
        try:
            report = validate_quality_findings_report(
                frozen_findings,
                checklist=checklist,
                render=original,
                profile=profile,
                semantic_consensus=semantic_consensus,
            ).model_dump(mode="json")
        except CompilerContractError as error:
            return _terminal("protocol_failed", original, None, None, None, None, None, str(error))
        findings = ProseQualityExecution("completed", report, None)
    observe("findings", findings)
    if findings.status != "completed" or findings.report is None:
        return _terminal(
            findings.status,
            original,
            findings,
            None,
            None,
            None,
            None,
            findings.error_code,
        )
    polish = execute_prose_polisher(
        polisher_provider,
        profile=profile,
        checklist=checklist,
        current_render=original,
        semantic_consensus=semantic_consensus,
        quality_findings=findings.report,
        model_id=generation_model_id,
        api_key=api_key,
    )
    observe("polish", polish)
    if polish.status != "completed" or polish.render is None:
        return _terminal(
            polish.status,
            original,
            findings,
            polish,
            None,
            None,
            None,
            polish.error_code,
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
    observe("preservation", preservation)
    if preservation.status != "completed" or preservation.consensus is None:
        return _terminal(
            preservation.status,
            original,
            findings,
            polish,
            preservation,
            None,
            None,
            preservation.error_code,
        )
    if preservation.consensus["scene_verdict"] != "pass":
        accepted = _accepted(
            original,
            original,
            checklist,
            profile,
            "polish_semantic_rollback",
            {
                "supervisor": PROSE_POLISH_SUPERVISOR_VERSION,
                "original_hash": canonical_json_sha256(original),
                "polished_hash": canonical_json_sha256(polish.render),
                "preservation_hash": canonical_json_sha256(preservation.consensus),
                "selection_reason": "polish_semantic_rollback",
            },
        )
        return _terminal(
            "finalized_original",
            original,
            findings,
            polish,
            preservation,
            None,
            accepted,
            None,
            "polish_semantic_rollback",
        )
    pairwise = execute_mirrored_pairwise_quality(
        quality_provider,
        checklist=checklist,
        original_render=original,
        polished_render=polish.render,
        profile=profile,
        preservation_consensus=preservation.consensus,
        model_id=quality_config.pairwise_model,
        config=quality_config,
        reverse_first=reverse_first,
        api_key=api_key,
    )
    observe("pairwise", pairwise)
    if pairwise.status != "completed" or pairwise.decision is None:
        return _terminal(
            pairwise.status,
            original,
            findings,
            polish,
            preservation,
            pairwise,
            None,
            pairwise.error_code,
        )
    decision = pairwise.decision
    selected = polish.render if decision.accept_polished else original
    accepted = _accepted(
        selected,
        original,
        checklist,
        profile,
        decision.selection_reason,
        {
            "supervisor": PROSE_POLISH_SUPERVISOR_VERSION,
            "original_hash": canonical_json_sha256(original),
            "polished_hash": canonical_json_sha256(polish.render),
            "preservation_hash": canonical_json_sha256(preservation.consensus),
            "quality_report_hashes": list(decision.report_hashes),
            "selection_reason": decision.selection_reason,
        },
    )
    status = "finalized_polished" if decision.accept_polished else "finalized_original"
    return _terminal(
        status,
        original,
        findings,
        polish,
        preservation,
        pairwise,
        accepted,
        None,
        decision.selection_reason,
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
    original: dict[str, Any] | None,
    findings: ProseQualityExecution | None,
    polish: ProsePolisherExecution | None,
    preservation: ProseCouncilExecution | None,
    pairwise: MirroredQualityExecution | None,
    accepted: dict[str, Any] | None,
    error_code: str | None,
    selection_reason: str | None = None,
) -> ProsePolishSupervisorExecution:
    return ProsePolishSupervisorExecution(
        status=status,  # type: ignore[arg-type]
        original_render=original,
        findings=findings,
        polish=polish,
        preservation=preservation,
        pairwise=pairwise,
        accepted_render=accepted,
        selection_reason=selection_reason,
        error_code=error_code,
    )


__all__ = [
    "PROSE_POLISH_SUPERVISOR_VERSION",
    "ProsePolishSupervisorExecution",
    "execute_prose_polish_supervisor",
]
