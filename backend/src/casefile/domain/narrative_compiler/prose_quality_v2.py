"""Pure B3 v4 pointwise quality, edit-window, patch, and delta rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.prose_checklist import (
    normalize_scene_polish_candidate,
    validate_scene_render,
)
from casefile.domain.narrative_compiler.prose_quality import (
    QUALITY_DIMENSIONS,
    validate_quality_pair_inputs,
    validate_semantic_acceptance,
)
from casefile_contracts import (
    ProsePolishPatchCandidate,
    ProseQualityAssessment,
    ProseQualityDelta,
    SceneRender,
)

PROSE_EDIT_WINDOW_POLICY_VERSION: Final = "prose-edit-window-policy-v1"
PROSE_EDIT_WINDOW_MAX_COUNT: Final = 3
PROSE_EDIT_WINDOW_MAX_COVERAGE_NUMERATOR: Final = 2
PROSE_EDIT_WINDOW_MAX_COVERAGE_DENOMINATOR: Final = 5
PROSE_EDIT_WINDOW_CONTEXT_SENTENCES: Final = 1
PROSE_EDIT_WINDOW_POLICY: Final = {
    "version": PROSE_EDIT_WINDOW_POLICY_VERSION,
    "max_windows": PROSE_EDIT_WINDOW_MAX_COUNT,
    "max_coverage_numerator": PROSE_EDIT_WINDOW_MAX_COVERAGE_NUMERATOR,
    "max_coverage_denominator": PROSE_EDIT_WINDOW_MAX_COVERAGE_DENOMINATOR,
    "context_sentences_each_side": PROSE_EDIT_WINDOW_CONTEXT_SENTENCES,
    "scope_overflow": "rollback_whole_scene",
}
PROSE_EDIT_WINDOW_POLICY_HASH: Final = canonical_json_sha256(PROSE_EDIT_WINDOW_POLICY)
PROSE_QUALITY_DELTA_POLICY_VERSION: Final = "prose-quality-delta-policy-v1"
PROSE_QUALITY_DELTA_POLICY: Final = {
    "version": PROSE_QUALITY_DELTA_POLICY_VERSION,
    "severity_order": ["none", "low", "medium", "high"],
    "acceptance": "no_dimension_regression_and_target_dimension_improvement",
}
PROSE_QUALITY_DELTA_POLICY_HASH: Final = canonical_json_sha256(
    PROSE_QUALITY_DELTA_POLICY
)

WindowBuildStatus = Literal["ready", "noop", "scope_exceeded"]


@dataclass(frozen=True, slots=True)
class EditableWindowBuild:
    """Deterministic edit authorization derived from model evidence."""

    status: WindowBuildStatus
    manifest: dict[str, Any]


def validate_quality_assessment(
    assessment: dict[str, Any],
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
) -> ProseQualityAssessment:
    """Bind a complete five-dimension assessment to one accepted render."""

    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render_json,
        profile=profile,
    )
    try:
        parsed = ProseQualityAssessment.model_validate(assessment)
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_quality_assessment_invalid") from error
    value = parsed.model_dump(mode="json")
    if (
        value["scene_id"] != render_json["scene_id"]
        or value["render_hash"] != canonical_json_sha256(render_json)
        or [item["dimension"] for item in value["dimensions"]]
        != list(QUALITY_DIMENSIONS)
    ):
        raise CompilerContractError("compiler_prose_quality_assessment_binding_invalid")
    blocks = {item["block_id"]: item["text"] for item in render_json["blocks"]}
    for item in value["dimensions"]:
        evidence = item["evidence"]
        if (item["severity"] == "none") != (not evidence):
            raise CompilerContractError(
                "compiler_prose_quality_assessment_evidence_required"
            )
        _validate_exact_evidence(evidence, blocks)
    return parsed


def assessment_has_findings(assessment: dict[str, Any]) -> bool:
    """Return whether a validated assessment contains any target dimension."""

    try:
        parsed = ProseQualityAssessment.model_validate(assessment).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_quality_assessment_invalid") from error
    return any(item["severity"] != "none" for item in parsed["dimensions"])


def build_editable_window_manifest(
    *,
    assessment: dict[str, Any],
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    evidence_catalog: list[dict[str, Any]],
) -> EditableWindowBuild:
    """Expand cited catalog sentences and enforce the 3-window/40% boundary."""

    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    assessment_json = validate_quality_assessment(
        assessment,
        checklist=checklist,
        render=render_json,
        profile=profile,
        semantic_consensus=semantic_consensus,
    ).model_dump(mode="json")
    catalog = _validated_catalog(evidence_catalog, render_json)
    block_order = {
        item["block_id"]: index for index, item in enumerate(render_json["blocks"])
    }
    by_block: dict[str, list[dict[str, Any]]] = {}
    catalog_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in catalog:
        by_block.setdefault(item["block_id"], []).append(item)
        catalog_by_key[_evidence_key(item)] = item
    for entries in by_block.values():
        entries.sort(key=lambda item: (item["start_char"], item["end_char"]))

    intervals: list[dict[str, Any]] = []
    for dimension in assessment_json["dimensions"]:
        if dimension["severity"] == "none":
            continue
        for evidence in dimension["evidence"]:
            catalog_item = catalog_by_key.get(_evidence_key(evidence))
            if catalog_item is None:
                raise CompilerContractError(
                    "compiler_prose_quality_assessment_catalog_mismatch"
                )
            entries = by_block[catalog_item["block_id"]]
            index = entries.index(catalog_item)
            lower = max(0, index - PROSE_EDIT_WINDOW_CONTEXT_SENTENCES)
            upper = min(len(entries), index + PROSE_EDIT_WINDOW_CONTEXT_SENTENCES + 1)
            expanded = entries[lower:upper]
            intervals.append(
                {
                    "block_id": catalog_item["block_id"],
                    "block_order": block_order[catalog_item["block_id"]],
                    "start_char": expanded[0]["start_char"],
                    "end_char": expanded[-1]["end_char"],
                    "evidence_ids": {catalog_item["evidence_id"]},
                    "target_dimensions": {dimension["dimension"]},
                }
            )

    merged = _merge_intervals(intervals)
    block_text = {item["block_id"]: item["text"] for item in render_json["blocks"]}
    windows: list[dict[str, Any]] = []
    for index, item in enumerate(merged, start=1):
        text = block_text[item["block_id"]][item["start_char"] : item["end_char"]]
        windows.append(
            {
                "window_id": f"window_{index:03d}",
                "block_id": item["block_id"],
                "start_char": item["start_char"],
                "end_char": item["end_char"],
                "original_text": text,
                "original_text_hash": canonical_json_sha256(text),
                "evidence_ids": sorted(item["evidence_ids"]),
                "target_dimensions": [
                    value for value in QUALITY_DIMENSIONS if value in item["target_dimensions"]
                ],
            }
        )
    render_chars = sum(len(item["text"]) for item in render_json["blocks"])
    coverage_chars = sum(item["end_char"] - item["start_char"] for item in windows)
    manifest = {
        "schema_id": "compiler.prose-editable-window-manifest.v1",
        "source_render_hash": canonical_json_sha256(render_json),
        "assessment_hash": canonical_json_sha256(assessment_json),
        "policy_version": PROSE_EDIT_WINDOW_POLICY_VERSION,
        "policy_hash": PROSE_EDIT_WINDOW_POLICY_HASH,
        "max_windows": PROSE_EDIT_WINDOW_MAX_COUNT,
        "max_coverage_numerator": PROSE_EDIT_WINDOW_MAX_COVERAGE_NUMERATOR,
        "max_coverage_denominator": PROSE_EDIT_WINDOW_MAX_COVERAGE_DENOMINATOR,
        "render_chars": render_chars,
        "coverage_chars": coverage_chars,
        "windows": windows,
    }
    if not windows:
        return EditableWindowBuild("noop", manifest)
    scope_exceeded = (
        len(windows) > PROSE_EDIT_WINDOW_MAX_COUNT
        or coverage_chars * PROSE_EDIT_WINDOW_MAX_COVERAGE_DENOMINATOR
        > render_chars * PROSE_EDIT_WINDOW_MAX_COVERAGE_NUMERATOR
    )
    return EditableWindowBuild("scope_exceeded" if scope_exceeded else "ready", manifest)


def apply_prose_polish_patch(
    candidate: dict[str, Any],
    *,
    manifest: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    current_render: dict[str, Any],
    component_input_hash: str,
) -> SceneRender | None:
    """Apply authorized replacements while retaining all text outside windows."""

    try:
        patch = ProsePolishPatchCandidate.model_validate(candidate).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_polish_patch_candidate_invalid") from error
    current = validate_scene_render(
        current_render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    current_hash = canonical_json_sha256(current)
    if (
        manifest.get("schema_id") != "compiler.prose-editable-window-manifest.v1"
        or manifest.get("source_render_hash") != current_hash
        or manifest.get("policy_version") != PROSE_EDIT_WINDOW_POLICY_VERSION
        or manifest.get("policy_hash") != PROSE_EDIT_WINDOW_POLICY_HASH
        or patch["source_render_hash"] != current_hash
        or patch["window_manifest_hash"] != canonical_json_sha256(manifest)
    ):
        raise CompilerContractError("compiler_prose_polish_patch_binding_invalid")
    edits = patch["edits"]
    edit_ids = [item["window_id"] for item in edits]
    if len(edit_ids) != len(set(edit_ids)):
        raise CompilerContractError("compiler_prose_polish_patch_window_duplicate")
    if not edits:
        return None
    windows = {item["window_id"]: item for item in manifest.get("windows", [])}
    block_texts = {item["block_id"]: item["text"] for item in current["blocks"]}
    edits_by_block: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for edit in edits:
        window = windows.get(edit["window_id"])
        if window is None or edit["original_text_hash"] != window["original_text_hash"]:
            raise CompilerContractError("compiler_prose_polish_patch_window_invalid")
        source_text = block_texts.get(window["block_id"])
        start, end = window["start_char"], window["end_char"]
        if (
            source_text is None
            or start < 0
            or start >= end
            or end > len(source_text)
            or source_text[start:end] != window["original_text"]
            or canonical_json_sha256(source_text[start:end])
            != window["original_text_hash"]
        ):
            raise CompilerContractError("compiler_prose_polish_patch_window_stale")
        edits_by_block.setdefault(window["block_id"], []).append((window, edit))
    for block_id, block_edits in edits_by_block.items():
        text = block_texts[block_id]
        for window, edit in sorted(
            block_edits, key=lambda pair: pair[0]["start_char"], reverse=True
        ):
            text = (
                text[: window["start_char"]]
                + edit["replacement_text"]
                + text[window["end_char"] :]
            )
        block_texts[block_id] = text
    full_candidate = {
        "schema_id": "compiler.scene-render-candidate.v1",
        "blocks": [{"text": block_texts[item["block_id"]]} for item in current["blocks"]],
    }
    return normalize_scene_polish_candidate(
        full_candidate,
        checklist=checklist,
        profile=profile,
        current_render=current,
        component_input_hash=component_input_hash,
    )


def resolve_quality_delta(
    *,
    original_assessment: dict[str, Any],
    polished_assessment: dict[str, Any],
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    original_semantic_consensus: dict[str, Any],
    preservation_consensus: dict[str, Any],
) -> ProseQualityDelta:
    """Accept only a target improvement with no five-dimension regression."""

    original, polished = validate_quality_pair_inputs(
        checklist=checklist,
        original_render=original_render,
        polished_render=polished_render,
        profile=profile,
        preservation_consensus=preservation_consensus,
    )
    before = validate_quality_assessment(
        original_assessment,
        checklist=checklist,
        render=original,
        profile=profile,
        semantic_consensus=original_semantic_consensus,
    ).model_dump(mode="json")
    after = validate_quality_assessment(
        polished_assessment,
        checklist=checklist,
        render=polished,
        profile=profile,
        semantic_consensus=preservation_consensus,
    ).model_dump(mode="json")
    severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    before_by_dimension = {item["dimension"]: item["severity"] for item in before["dimensions"]}
    after_by_dimension = {item["dimension"]: item["severity"] for item in after["dimensions"]}
    targeted = [
        dimension
        for dimension in QUALITY_DIMENSIONS
        if before_by_dimension[dimension] != "none"
    ]
    if not targeted:
        raise CompilerContractError("compiler_prose_quality_delta_no_targets")
    deltas = []
    for dimension in QUALITY_DIMENSIONS:
        before_rank = severity_rank[before_by_dimension[dimension]]
        after_rank = severity_rank[after_by_dimension[dimension]]
        deltas.append(
            {
                "dimension": dimension,
                "before": before_by_dimension[dimension],
                "after": after_by_dimension[dimension],
                "improved": after_rank < before_rank,
                "regressed": after_rank > before_rank,
            }
        )
    accept = not any(item["regressed"] for item in deltas) and any(
        item["improved"] and item["dimension"] in targeted for item in deltas
    )
    return ProseQualityDelta.model_validate(
        {
            "schema_id": "compiler.prose-quality-delta.v1",
            "original_render_hash": canonical_json_sha256(original),
            "polished_render_hash": canonical_json_sha256(polished),
            "original_assessment_hash": canonical_json_sha256(before),
            "polished_assessment_hash": canonical_json_sha256(after),
            "targeted_dimensions": targeted,
            "dimension_deltas": deltas,
            "accept_polished": accept,
            "selection_reason": "polished_accepted" if accept else "quality_rollback",
        }
    )


def _validated_catalog(
    evidence_catalog: list[dict[str, Any]], render: dict[str, Any]
) -> list[dict[str, Any]]:
    blocks = {item["block_id"]: item["text"] for item in render["blocks"]}
    ids: set[str] = set()
    keys: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in evidence_catalog:
        if set(item) != {
            "evidence_id",
            "block_id",
            "start_char",
            "end_char",
            "text",
        }:
            raise CompilerContractError("compiler_prose_quality_evidence_catalog_invalid")
        evidence_id = item["evidence_id"]
        key = _evidence_key(item)
        if not isinstance(evidence_id, str) or evidence_id in ids or key in keys:
            raise CompilerContractError("compiler_prose_quality_evidence_catalog_invalid")
        _validate_exact_evidence([item], blocks)
        ids.add(evidence_id)
        keys.add(key)
        result.append(dict(item))
    return result


def _validate_exact_evidence(
    evidence_items: list[dict[str, Any]], blocks: dict[str, str]
) -> None:
    seen: set[tuple[Any, ...]] = set()
    for evidence in evidence_items:
        key = _evidence_key(evidence)
        block_id = evidence.get("block_id")
        text = blocks.get(block_id) if isinstance(block_id, str) else None
        start = evidence.get("start_char")
        end = evidence.get("end_char")
        if (
            key in seen
            or text is None
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or start >= end
            or end > len(text)
            or text[start:end] != evidence.get("text")
        ):
            raise CompilerContractError("compiler_prose_quality_assessment_evidence_invalid")
        seen.add(key)


def _evidence_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("block_id"),
        item.get("start_char"),
        item.get("end_char"),
        item.get("text"),
    )


def _merge_intervals(intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in sorted(
        intervals,
        key=lambda value: (
            value["block_order"],
            value["start_char"],
            value["end_char"],
        ),
    ):
        if (
            merged
            and merged[-1]["block_id"] == item["block_id"]
            and item["start_char"] <= merged[-1]["end_char"] + 1
        ):
            merged[-1]["end_char"] = max(merged[-1]["end_char"], item["end_char"])
            merged[-1]["evidence_ids"].update(item["evidence_ids"])
            merged[-1]["target_dimensions"].update(item["target_dimensions"])
        else:
            merged.append(
                {
                    "block_id": item["block_id"],
                    "block_order": item["block_order"],
                    "start_char": item["start_char"],
                    "end_char": item["end_char"],
                    "evidence_ids": set(item["evidence_ids"]),
                    "target_dimensions": set(item["target_dimensions"]),
                }
            )
    return merged


__all__ = [
    "EditableWindowBuild",
    "PROSE_EDIT_WINDOW_MAX_COUNT",
    "PROSE_EDIT_WINDOW_MAX_COVERAGE_DENOMINATOR",
    "PROSE_EDIT_WINDOW_MAX_COVERAGE_NUMERATOR",
    "PROSE_EDIT_WINDOW_POLICY",
    "PROSE_EDIT_WINDOW_POLICY_HASH",
    "PROSE_EDIT_WINDOW_POLICY_VERSION",
    "PROSE_QUALITY_DELTA_POLICY",
    "PROSE_QUALITY_DELTA_POLICY_HASH",
    "PROSE_QUALITY_DELTA_POLICY_VERSION",
    "apply_prose_polish_patch",
    "assessment_has_findings",
    "build_editable_window_manifest",
    "resolve_quality_delta",
    "validate_quality_assessment",
]
