"""Pure N4.5 literary-quality report and mirrored-selection rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from casefile_contracts import ProseConsensusReport, ProseQualityReport
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.prose_checklist import validate_scene_render

QUALITY_DIMENSIONS: Final = (
    "pov_voice_consistency",
    "scene_specificity",
    "dialogue_narration_naturalness",
    "dramatic_progression_pacing",
    "readability_editability",
)

QualityIdentity = Literal["original", "polished", "tie"]
QualitySelectionReason = Literal[
    "polished_accepted",
    "quality_rollback",
    "quality_unstable",
]


@dataclass(frozen=True, slots=True)
class MirroredQualityDecision:
    """Deterministic selection after the two opposite-position comparisons."""

    accept_polished: bool
    selection_reason: QualitySelectionReason
    report_hashes: tuple[str, str]


def validate_semantic_acceptance(
    consensus: dict[str, Any],
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
) -> ProseConsensusReport:
    """Require a complete pass bound to the exact render and checklist."""

    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    try:
        parsed = ProseConsensusReport.model_validate(consensus)
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_quality_consensus_invalid") from error
    value = parsed.model_dump(mode="json")
    check_ids = [item["check_id"] for item in checklist["checks"]]
    if (
        value["scene_id"] != render_json["scene_id"]
        or value["round"] != render_json["round"]
        or value["checklist_hash"] != canonical_json_sha256(checklist)
        or value["render_hash"] != canonical_json_sha256(render_json)
        or [item["check_id"] for item in value["checks"]] != check_ids
        or any(item["final_verdict"] != "pass" for item in value["checks"])
        or value["scene_verdict"] != "pass"
        or value["failed_check_ids"]
        or value["unresolved_check_ids"]
    ):
        raise CompilerContractError("compiler_prose_quality_semantic_acceptance_required")
    return parsed


def validate_quality_findings_report(
    report: dict[str, Any],
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
) -> ProseQualityReport:
    """Validate one findings report and its exact-copy render evidence."""

    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render_json,
        profile=profile,
    )
    parsed = _parse_quality_report(report)
    value = parsed.model_dump(mode="json")
    render_hash = canonical_json_sha256(render_json)
    if (
        value["report_kind"] != "findings"
        or value["scene_id"] != render_json["scene_id"]
        or value["source_render_hashes"] != [render_hash]
        or value["position_mapping"] is not None
        or value["overall_preference"] is not None
        or value["dimension_preferences"]
    ):
        raise CompilerContractError("compiler_prose_quality_findings_binding_invalid")
    blocks = {item["block_id"]: item["text"] for item in render_json["blocks"]}
    finding_keys: set[str] = set()
    for finding in value["findings"]:
        finding_key = canonical_json_sha256(finding)
        if finding_key in finding_keys:
            raise CompilerContractError("compiler_prose_quality_finding_duplicate")
        finding_keys.add(finding_key)
        _validate_evidence(finding["evidence"], blocks)
    return parsed


def validate_quality_pairwise_report(
    report: dict[str, Any],
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    preservation_consensus: dict[str, Any],
    position_mapping: Mapping[str, str],
) -> ProseQualityReport:
    """Validate one blind pairwise report against server-owned identities."""

    original, polished = validate_quality_pair_inputs(
        checklist=checklist,
        original_render=original_render,
        polished_render=polished_render,
        profile=profile,
        preservation_consensus=preservation_consensus,
    )
    original_hash = canonical_json_sha256(original)
    polished_hash = canonical_json_sha256(polished)
    if position_mapping not in (
        {"a": "original", "b": "polished"},
        {"a": "polished", "b": "original"},
    ):
        raise CompilerContractError("compiler_prose_quality_position_mapping_invalid")
    parsed = _parse_quality_report(report)
    value = parsed.model_dump(mode="json")
    if (
        value["report_kind"] != "pairwise"
        or value["scene_id"] != original["scene_id"]
        or value["source_render_hashes"] != [original_hash, polished_hash]
        or value["position_mapping"] != position_mapping
        or value["findings"]
        or value["overall_preference"] is None
        or [item["dimension"] for item in value["dimension_preferences"]]
        != list(QUALITY_DIMENSIONS)
    ):
        raise CompilerContractError("compiler_prose_quality_pairwise_binding_invalid")
    return parsed


def validate_quality_pair_inputs(
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    preservation_consensus: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fail before Pairwise Provider calls unless lineage and preservation are exact."""

    original = validate_scene_render(
        original_render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    polished = validate_scene_render(
        polished_render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    original_hash = canonical_json_sha256(original)
    polished_hash = canonical_json_sha256(polished)
    if original_hash == polished_hash:
        raise CompilerContractError("compiler_prose_quality_pair_identical")
    if (
        polished["stage"] != "polished"
        or original["stage"] not in {"writer", "rewrite_1", "rewrite_2"}
        or polished["round"] != original["round"]
        or polished["previous_render_hash"] != original_hash
        or (polished["scene_id"], polished["scene_ordinal"])
        != (original["scene_id"], original["scene_ordinal"])
    ):
        raise CompilerContractError("compiler_prose_quality_pair_lineage_invalid")
    validate_semantic_acceptance(
        preservation_consensus,
        checklist=checklist,
        render=polished,
        profile=profile,
    )
    return original, polished


def resolve_mirrored_quality(
    first_report: dict[str, Any], second_report: dict[str, Any]
) -> MirroredQualityDecision:
    """Accept polished only on stable mirrored wins with no dimension regression."""

    try:
        first = ProseQualityReport.model_validate(first_report).model_dump(mode="json")
        second = ProseQualityReport.model_validate(second_report).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_quality_mirrored_reports_invalid") from error
    if (
        first["report_kind"] != "pairwise"
        or second["report_kind"] != "pairwise"
        or first["scene_id"] != second["scene_id"]
        or first["source_render_hashes"] != second["source_render_hashes"]
        or first["position_mapping"] != {"a": "original", "b": "polished"}
        or second["position_mapping"] != {"a": "polished", "b": "original"}
    ):
        raise CompilerContractError("compiler_prose_quality_mirrored_binding_invalid")
    overall = (_mapped_preference(first), _mapped_preference(second))
    dimension_results = [
        _mapped_preference(report, preference=item["preference"])
        for report in (first, second)
        for item in report["dimension_preferences"]
    ]
    if overall == ("polished", "polished") and "original" not in dimension_results:
        reason: QualitySelectionReason = "polished_accepted"
        accepted = True
    elif overall[0] != overall[1] or "tie" in overall:
        reason = "quality_unstable"
        accepted = False
    else:
        reason = "quality_rollback"
        accepted = False
    return MirroredQualityDecision(
        accept_polished=accepted,
        selection_reason=reason,
        report_hashes=(
            canonical_json_sha256(first),
            canonical_json_sha256(second),
        ),
    )


def _parse_quality_report(report: dict[str, Any]) -> ProseQualityReport:
    try:
        return ProseQualityReport.model_validate(report)
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_quality_report_invalid") from error


def _validate_evidence(
    evidence_items: list[dict[str, Any]], blocks: dict[str, str]
) -> None:
    seen: set[str] = set()
    for evidence in evidence_items:
        key = canonical_json_sha256(evidence)
        text = blocks.get(evidence["block_id"])
        start, end = evidence["start_char"], evidence["end_char"]
        if (
            key in seen
            or text is None
            or start >= end
            or end > len(text)
            or text[start:end] != evidence["text"]
        ):
            raise CompilerContractError("compiler_prose_quality_evidence_invalid")
        seen.add(key)


def _mapped_preference(
    report: dict[str, Any], *, preference: str | None = None
) -> QualityIdentity:
    selected = preference if preference is not None else report["overall_preference"]
    if selected == "tie":
        return "tie"
    return cast(QualityIdentity, report["position_mapping"][selected])


__all__ = [
    "QUALITY_DIMENSIONS",
    "MirroredQualityDecision",
    "resolve_mirrored_quality",
    "validate_quality_findings_report",
    "validate_quality_pairwise_report",
    "validate_quality_pair_inputs",
    "validate_semantic_acceptance",
]
