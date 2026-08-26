"""Pure NovelPlan semantic validation, canonicalization, and identity."""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.planner_input import (
    PLANNER_INPUT_V2_SCHEMA_ID,
    PLANNER_INPUT_V3_SCHEMA_ID,
    validate_planner_input_bundle,
)
from casefile_contracts import NovelPlanCandidate, NovelPlanIR

NOVEL_PLAN_CANDIDATE_SCHEMA_ID = "compiler.novel-plan-candidate.v1"
NOVEL_PLAN_SCHEMA_ID = "compiler.novel-plan.v1"
STORY_PLANNER_COMPONENT_VERSION = "compiler.story-planner.v1"
STORY_PLANNER_REPAIR_VERSION = "compiler.story-plan-mode-repair.v1"
STORY_PLANNER_STRUCTURAL_REPAIR_VERSION = "compiler.story-plan-structural-patch.v1"

_COLLECTION_TYPE = {
    "resolution_specs": "resolution_spec",
    "entities": "entity",
    "relationships": "relationship",
    "locations": "location",
    "events": "event",
    "information_units": "information_unit",
    "claims": "claim",
    "hypotheses": "hypothesis",
    "reasoning_paths": "reasoning_path",
    "constraints": "constraint",
    "structure_locks": "structure_lock",
}
_RUNTIME_KEYS = {
    "compile_run_id",
    "task_run_id",
    "agent_step_run_id",
    "user_id",
    "database_id",
}
_TEMPORAL_VIOLATION_CODES = {
    "compiler_story_plan_temporal_order_invalid",
    "compiler_story_plan_flashback_invalid",
    "compiler_story_plan_flashforward_invalid",
}
_PRESERVED_NONLINEAR_MODES = ("flashback", "flashforward")


@dataclass(frozen=True, slots=True)
class NovelPlanViolation:
    """One deterministic semantic counterexample without repair instructions."""

    code: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NovelPlanValidationReport:
    """Ordered semantic violations for audit and future bounded experiments."""

    valid: bool
    violations: tuple[NovelPlanViolation, ...]


@dataclass(frozen=True, slots=True)
class NovelPlanRepairResult:
    """One deterministic, candidate-preserving temporal repair result."""

    candidate: dict[str, Any]
    applied: bool
    changes: tuple[dict[str, str], ...]
    before: NovelPlanValidationReport
    after: NovelPlanValidationReport
    repair_version: str = STORY_PLANNER_REPAIR_VERSION


def validate_novel_plan_candidate(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> NovelPlanCandidate:
    """Validate model-owned arrangement without repairing semantic violations."""

    report, parsed = _inspect_novel_plan_candidate(candidate, planner_input=planner_input)
    if not report.valid:
        raise CompilerContractError(report.violations[0].code)
    assert parsed is not None
    return parsed


def inspect_novel_plan_candidate(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> NovelPlanValidationReport:
    """Return deterministic counterexamples while preserving fail-closed callers."""

    report, _ = _inspect_novel_plan_candidate(candidate, planner_input=planner_input)
    return report


def repair_novel_plan_candidate(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> NovelPlanRepairResult:
    """Repair only temporal presentation modes while preserving narrative intent."""

    before = inspect_novel_plan_candidate(candidate, planner_input=planner_input)
    original = copy.deepcopy(candidate)
    if before.valid or not _only_temporal_violations(before):
        return NovelPlanRepairResult(
            candidate=original,
            applied=False,
            changes=(),
            before=before,
            after=before,
        )
    try:
        current = NovelPlanCandidate.model_validate(candidate).model_dump(mode="json")
    except ValidationError:
        return NovelPlanRepairResult(
            candidate=original,
            applied=False,
            changes=(),
            before=before,
            after=before,
        )

    parsed_input = validate_planner_input_bundle(planner_input)
    allowed_modes = tuple(parsed_input["profile"]["allowed_presentation_modes"])
    preserved_counts = _nonlinear_mode_counts(current)
    max_steps = min(len(current["scenes"]), 16)
    current_report = before
    for _step in range(max_steps):
        candidates = _temporal_mode_candidates(
            current,
            report=current_report,
            allowed_modes=allowed_modes,
        )
        ranked: list[tuple[int, int, str, dict[str, Any], NovelPlanValidationReport]] = []
        for proposed in candidates:
            if not _preserves_candidate(original, proposed, preserved_counts=preserved_counts):
                continue
            proposed_report = inspect_novel_plan_candidate(proposed, planner_input=parsed_input)
            if not proposed_report.valid and not _only_temporal_violations(proposed_report):
                continue
            ranked.append(
                (
                    len(proposed_report.violations),
                    len(_presentation_mode_changes(original, proposed)),
                    canonical_json_sha256(proposed),
                    proposed,
                    proposed_report,
                )
            )
        if not ranked:
            break
        ranked.sort(key=lambda item: item[:3])
        violation_count, _change_count, _hash, proposed, proposed_report = ranked[0]
        if violation_count >= len(current_report.violations):
            break
        current = proposed
        current_report = proposed_report
        if current_report.valid:
            changes = _presentation_mode_changes(original, current)
            return NovelPlanRepairResult(
                candidate=current,
                applied=bool(changes),
                changes=changes,
                before=before,
                after=current_report,
            )

    return NovelPlanRepairResult(
        candidate=original,
        applied=False,
        changes=(),
        before=before,
        after=before,
    )


def _inspect_novel_plan_candidate(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> tuple[NovelPlanValidationReport, NovelPlanCandidate | None]:
    try:
        parsed, value, catalog, parsed_input = _validate_candidate_foundation(
            candidate,
            planner_input=planner_input,
        )
    except CompilerContractError as error:
        return (
            NovelPlanValidationReport(
                valid=False,
                violations=(NovelPlanViolation(code=error.reason_code, details={}),),
            ),
            None,
        )

    scenes = value["scenes"]
    violations = (
        *_exposure_violations(scenes, parsed_input.get("exposure_plan")),
        *_semantic_obligation_violations(scenes, parsed_input),
        *_resolution_violations(scenes, catalog),
        *_story_time_violations(scenes, parsed_input["narrative_ir"]),
    )
    return NovelPlanValidationReport(valid=not violations, violations=violations), parsed


def _validate_candidate_foundation(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> tuple[
    NovelPlanCandidate,
    dict[str, Any],
    set[tuple[str, str]],
    dict[str, Any],
]:

    planner_input = validate_planner_input_bundle(planner_input)
    try:
        parsed = NovelPlanCandidate.model_validate(candidate)
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_output_invalid") from error
    value = parsed.model_dump(mode="json")
    _validate_no_runtime_identity(value)

    chapters = value["chapters"]
    scenes = value["scenes"]
    _unique_contiguous(chapters, "chapter_id", "ordinal", "compiler_story_plan_chapter_invalid")
    _unique_contiguous(scenes, "scene_id", "discourse_order", "compiler_story_plan_scene_invalid")
    chapter_ids = {item["chapter_id"] for item in chapters}
    if any(scene["chapter_id"] not in chapter_ids for scene in scenes):
        raise CompilerContractError("compiler_story_plan_chapter_reference_invalid")

    scene_ids = {item["scene_id"] for item in scenes}
    graph: dict[str, set[str]] = {}
    for scene in scenes:
        dependencies = set(scene["prerequisite_scene_ids"])
        if scene["scene_id"] in dependencies or not dependencies <= scene_ids:
            raise CompilerContractError("compiler_story_plan_dependency_invalid")
        graph[scene["scene_id"]] = dependencies
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise CompilerContractError("compiler_story_plan_dependency_cycle") from error

    narrative_ir = planner_input["narrative_ir"]
    catalog, source_refs = _narrative_catalog(narrative_ir)
    for scene in scenes:
        _validate_scene_refs(scene, catalog)
        if any(_ref_key(ref) not in source_refs for ref in scene["basis_refs"]):
            raise CompilerContractError("compiler_story_plan_provenance_invalid")
        for ref in scene["story_time_refs"]:
            if ref["object_type"] != "event":
                raise CompilerContractError("compiler_story_plan_temporal_invalid")

    profile = planner_input["profile"]
    constraints = planner_input["planning_constraints"]
    if (
        len(chapters) != constraints["target_chapters"]
        or len(scenes) != constraints["target_scenes"]
    ):
        raise CompilerContractError("compiler_story_plan_profile_target_invalid")
    allowed_modes = set(profile["allowed_presentation_modes"])
    if any(scene["presentation_mode"] not in allowed_modes for scene in scenes):
        raise CompilerContractError("compiler_story_plan_presentation_mode_invalid")

    return parsed, value, catalog, planner_input


def canonicalize_novel_plan(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
    planner_version: str,
    component_fingerprint: str,
) -> dict[str, Any]:
    parsed = validate_novel_plan_candidate(candidate, planner_input=planner_input)
    value = parsed.model_dump(mode="json")
    _, source_refs = _narrative_catalog(planner_input["narrative_ir"])
    chapters = sorted(value["chapters"], key=lambda item: item["ordinal"])
    scenes: list[dict[str, Any]] = []
    chapter_index: dict[str, list[str]] = defaultdict(list)
    dependency_index: dict[str, list[str]] = {}
    for scene in sorted(value["scenes"], key=lambda item: item["discourse_order"]):
        basis = sorted({_ref_key(ref) for ref in scene["basis_refs"]})
        source = [source_refs[key] for key in basis]
        canonical_scene = {**scene, "source_refs": source}
        scenes.append(canonical_scene)
        chapter_index[scene["chapter_id"]].append(scene["scene_id"])
        dependency_index[scene["scene_id"]] = sorted(scene["prerequisite_scene_ids"])

    fingerprint = planner_input_fingerprint_json(planner_input)
    output = {
        "schema_id": NOVEL_PLAN_SCHEMA_ID,
        "planner_version": planner_version,
        "source": {
            "planner_input_hash": canonical_json_sha256(planner_input),
            "narrative_ir_hash": fingerprint["narrative_ir_hash"],
            "profile_hash": fingerprint["profile_hash"],
            "exposure_hash": fingerprint["exposure_hash"],
            "component_fingerprint": component_fingerprint,
        },
        "chapters": chapters,
        "scenes": scenes,
        "indexes": {
            "chapter_scene_ids": dict(sorted(chapter_index.items())),
            "scene_dependencies": dict(sorted(dependency_index.items())),
        },
    }
    try:
        return NovelPlanIR.model_validate(output).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_plan_canonicalization_failed") from error


def story_planner_component_fingerprint(
    *,
    planner_input: dict[str, Any],
    prompt_version: str,
    prompt_sha256: str,
    provider: str,
    model_id: str,
    provider_config_version: int,
    planner_model_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_fp = planner_input_fingerprint_json(planner_input)
    fingerprint = {
        "component_version": STORY_PLANNER_COMPONENT_VERSION,
        "candidate_repair_version": STORY_PLANNER_REPAIR_VERSION,
        "structural_repair_version": STORY_PLANNER_STRUCTURAL_REPAIR_VERSION,
        "candidate_schema_id": NOVEL_PLAN_CANDIDATE_SCHEMA_ID,
        "novel_plan_schema_id": NOVEL_PLAN_SCHEMA_ID,
        **input_fp,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "provider": provider,
        "model_id": model_id,
        "provider_config_version": provider_config_version,
    }
    if planner_model_view is not None:
        fingerprint["planner_model_view_schema_id"] = planner_model_view.get("schema_id")
        fingerprint["planner_model_view_hash"] = canonical_json_sha256(planner_model_view)
    return fingerprint


def planner_input_fingerprint_json(planner_input: dict[str, Any]) -> dict[str, Any]:
    planner_input = validate_planner_input_bundle(planner_input)
    exposure = planner_input.get("exposure_plan")
    return {
        "planner_input_schema_id": planner_input["schema_id"],
        "narrative_ir_hash": canonical_json_sha256(planner_input["narrative_ir"]),
        "profile_hash": canonical_json_sha256(planner_input["profile"]),
        "exposure_hash": None if exposure is None else canonical_json_sha256(exposure),
        "planner_view_hash": (
            canonical_json_sha256(planner_input["planner_view"])
            if planner_input["schema_id"]
            in {PLANNER_INPUT_V2_SCHEMA_ID, PLANNER_INPUT_V3_SCHEMA_ID}
            else None
        ),
    }


def _narrative_catalog(
    narrative_ir: dict[str, Any],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], dict[str, Any]]]:
    catalog: set[tuple[str, str]] = set()
    source_refs: dict[tuple[str, str], dict[str, Any]] = {}
    root_ref = narrative_ir["source"]["casefile_ref"]
    root_key = _ref_key(root_ref)
    catalog.add(root_key)
    source_refs[root_key] = narrative_ir["source"]["root_source_refs"][0]
    for collection, object_type in _COLLECTION_TYPE.items():
        for envelope in narrative_ir["objects"][collection]:
            key = _ref_key(envelope["object_ref"])
            if key[0] != object_type:
                raise CompilerContractError("compiler_story_plan_narrative_catalog_invalid")
            catalog.add(key)
            source_refs[key] = envelope["source_ref"]
    return catalog, source_refs


def _validate_scene_refs(scene: dict[str, Any], catalog: set[tuple[str, str]]) -> None:
    refs = (
        list(scene["participant_refs"])
        + list(scene["event_refs"])
        + list(scene["story_time_refs"])
        + list(scene["basis_refs"])
    )
    if scene["pov_ref"] is not None:
        refs.append(scene["pov_ref"])
    if scene["location_ref"] is not None:
        refs.append(scene["location_ref"])
    refs.extend(item["resolution_ref"] for item in scene["resolutions"])
    if any(_ref_key(ref) not in catalog for ref in refs):
        raise CompilerContractError("compiler_story_plan_reference_invalid")
    if scene["pov_ref"] is not None and scene["pov_ref"]["object_type"] != "entity":
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if scene["location_ref"] is not None and scene["location_ref"]["object_type"] != "location":
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if any(ref["object_type"] != "event" for ref in scene["event_refs"]):
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if any(
        item["resolution_ref"]["object_type"] != "resolution_spec" for item in scene["resolutions"]
    ):
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")


def _exposure_violations(
    scenes: list[dict[str, Any]], exposure: dict[str, Any] | None
) -> tuple[NovelPlanViolation, ...]:
    if exposure is None:
        return ()
    entries = exposure["frozen_payload"].get("entries", [])
    expected = [item["entry_key"] for item in entries]
    introduced: list[str] = []
    seen: set[str] = set()
    introduced_at: dict[str, str] = {}
    expected_set = set(expected)
    for scene in scenes:
        for placement in scene["exposure"]:
            key = placement["entry_key"]
            if key not in expected_set:
                return (
                    NovelPlanViolation(
                        code="compiler_story_plan_exposure_reference_invalid",
                        details={"scene_id": scene["scene_id"], "entry_key": key},
                    ),
                )
            if placement["action"] == "introduce":
                if key in seen:
                    return (
                        NovelPlanViolation(
                            code="compiler_story_plan_exposure_duplicate",
                            details={
                                "scene_id": scene["scene_id"],
                                "entry_key": key,
                                "first_scene_id": introduced_at[key],
                            },
                        ),
                    )
                seen.add(key)
                introduced_at[key] = scene["scene_id"]
                introduced.append(key)
            elif key not in seen:
                return (
                    NovelPlanViolation(
                        code="compiler_story_plan_exposure_before_introduction",
                        details={"scene_id": scene["scene_id"], "entry_key": key},
                    ),
                )
    if introduced != expected:
        mismatch = next(
            (
                index
                for index in range(max(len(expected), len(introduced)))
                if index >= len(expected)
                or index >= len(introduced)
                or expected[index] != introduced[index]
            ),
            0,
        )
        return (
            NovelPlanViolation(
                code="compiler_story_plan_exposure_violation",
                details={
                    "expected_introduce_order": expected,
                    "actual_introduce_order": introduced,
                    "first_mismatch_index": mismatch,
                },
            ),
        )
    return ()


def _semantic_obligation_violations(
    scenes: list[dict[str, Any]],
    planner_input: dict[str, Any],
) -> tuple[NovelPlanViolation, ...]:
    if planner_input.get("schema_id") != PLANNER_INPUT_V3_SCHEMA_ID:
        return ()
    obligations = planner_input["planner_view"]["hard_constraints"][
        "semantic_obligations"
    ]
    violations: list[NovelPlanViolation] = []
    for obligation in obligations:
        scoped_scenes = [
            scene
            for scene in scenes
            if any(
                placement["entry_key"] == obligation["entry_key"]
                for placement in scene["exposure"]
            )
        ]
        kind = obligation["kind"]
        if kind == "participant_coverage":
            eligible = {_ref_key(ref) for ref in obligation["eligible_refs"]}
            observed = {
                _ref_key(ref)
                for scene in scoped_scenes
                for ref in scene["participant_refs"]
                if _ref_key(ref) in eligible
            }
            required = int(obligation["min_distinct"])
            if len(observed) < required:
                violations.append(
                    NovelPlanViolation(
                        code="compiler_story_plan_participant_coverage_unmet",
                        details={
                            "obligation_key": obligation["obligation_key"],
                            "entry_key": obligation["entry_key"],
                            "min_distinct": required,
                            "actual_distinct": len(observed),
                        },
                    )
                )
            continue
        required_refs = {_ref_key(ref) for ref in obligation["required_refs"]}
        observed_basis = {
            _ref_key(ref)
            for scene in scoped_scenes
            for ref in scene["basis_refs"]
        }
        missing = sorted(required_refs - observed_basis)
        if missing:
            code = (
                "compiler_story_plan_basis_coverage_unmet"
                if kind == "basis_ref_coverage"
                else "compiler_story_plan_hypothesis_coverage_unmet"
            )
            violations.append(
                NovelPlanViolation(
                    code=code,
                    details={
                        "obligation_key": obligation["obligation_key"],
                        "entry_key": obligation["entry_key"],
                        "missing_refs": [
                            {"object_type": object_type, "object_id": object_id}
                            for object_type, object_id in missing
                        ],
                    },
                )
            )
    return tuple(violations)


def _resolution_violations(
    scenes: list[dict[str, Any]], catalog: set[tuple[str, str]]
) -> tuple[NovelPlanViolation, ...]:
    required = {key for key in catalog if key[0] == "resolution_spec"}
    terminal: dict[tuple[str, str], str] = {}
    for scene in scenes:
        for placement in scene["resolutions"]:
            action = placement["action"]
            key = _ref_key(placement["resolution_ref"])
            if action in {"resolve", "intentionally_unresolved"}:
                if key in terminal:
                    return (
                        NovelPlanViolation(
                            code="compiler_story_plan_resolution_duplicate",
                            details={
                                "scene_id": scene["scene_id"],
                                "resolution_ref": {
                                    "object_type": key[0],
                                    "object_id": key[1],
                                },
                            },
                        ),
                    )
                terminal[key] = action
    if set(terminal) != required:
        missing = sorted(required - set(terminal))
        return (
            NovelPlanViolation(
                code="compiler_story_plan_resolution_uncovered",
                details={
                    "missing_resolution_refs": [
                        {"object_type": item[0], "object_id": item[1]} for item in missing
                    ]
                },
            ),
        )
    return ()


def _story_time_violations(
    scenes: list[dict[str, Any]], narrative_ir: dict[str, Any]
) -> tuple[NovelPlanViolation, ...]:
    event_times: dict[str, datetime] = {}
    for envelope in narrative_ir["objects"]["events"]:
        time = envelope["value"].get("time") or {}
        raw = (
            time.get("value") if time.get("kind") in {"exact", "approximate"} else time.get("start")
        )
        if isinstance(raw, str):
            try:
                event_times[envelope["object_ref"]["object_id"]] = datetime.fromisoformat(raw)
            except ValueError:
                continue

    previous: datetime | None = None
    previous_scene_id: str | None = None
    violations: list[NovelPlanViolation] = []
    for scene in sorted(scenes, key=lambda item: item["discourse_order"]):
        anchors = [
            event_times.get(ref["object_id"])
            for ref in scene["story_time_refs"]
            if ref["object_type"] == "event"
        ]
        known = [value for value in anchors if value is not None]
        if not known:
            continue
        current = min(known)
        mode = scene["presentation_mode"]
        if previous is not None:
            if mode == "linear" and current < previous:
                code = "compiler_story_plan_temporal_order_invalid"
            elif mode == "flashback" and current > previous:
                code = "compiler_story_plan_flashback_invalid"
            elif mode == "flashforward" and current < previous:
                code = "compiler_story_plan_flashforward_invalid"
            else:
                code = None
            if code is not None:
                violations.append(
                    NovelPlanViolation(
                        code=code,
                        details={
                            "scene_id": scene["scene_id"],
                            "previous_scene_id": previous_scene_id,
                            "previous_time": previous.isoformat(),
                            "current_time": current.isoformat(),
                            "presentation_mode": mode,
                        },
                    )
                )
        previous = current
        previous_scene_id = scene["scene_id"]
    return tuple(violations)


def _only_temporal_violations(report: NovelPlanValidationReport) -> bool:
    return bool(report.violations) and all(
        violation.code in _TEMPORAL_VIOLATION_CODES for violation in report.violations
    )


def _temporal_mode_candidates(
    candidate: dict[str, Any],
    *,
    report: NovelPlanValidationReport,
    allowed_modes: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    scenes = candidate["scenes"]
    scene_indexes = {scene["scene_id"]: index for index, scene in enumerate(scenes)}
    proposed: dict[str, dict[str, Any]] = {}
    for violation in report.violations:
        scene_id = violation.details.get("scene_id")
        if not isinstance(scene_id, str) or scene_id not in scene_indexes:
            continue
        index = scene_indexes[scene_id]
        original_mode = scenes[index]["presentation_mode"]
        for replacement in allowed_modes:
            if replacement == original_mode:
                continue
            direct = copy.deepcopy(candidate)
            direct["scenes"][index]["presentation_mode"] = replacement
            proposed[canonical_json_sha256(direct)] = direct
        for donor_index, donor in enumerate(scenes):
            donor_mode = donor["presentation_mode"]
            if donor_index == index or donor_mode == original_mode:
                continue
            swapped = copy.deepcopy(candidate)
            swapped["scenes"][index]["presentation_mode"] = donor_mode
            swapped["scenes"][donor_index]["presentation_mode"] = original_mode
            proposed[canonical_json_sha256(swapped)] = swapped
    return tuple(proposed[key] for key in sorted(proposed))


def _nonlinear_mode_counts(candidate: dict[str, Any]) -> dict[str, int]:
    return {
        mode: sum(scene["presentation_mode"] == mode for scene in candidate["scenes"])
        for mode in _PRESERVED_NONLINEAR_MODES
    }


def _preserves_candidate(
    original: dict[str, Any],
    proposed: dict[str, Any],
    *,
    preserved_counts: dict[str, int],
) -> bool:
    if _without_presentation_modes(original) != _without_presentation_modes(proposed):
        return False
    proposed_counts = _nonlinear_mode_counts(proposed)
    return all(proposed_counts[mode] >= count for mode, count in preserved_counts.items())


def _without_presentation_modes(candidate: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(candidate)
    for scene in value.get("scenes", []):
        scene.pop("presentation_mode", None)
    return value


def _presentation_mode_changes(
    original: dict[str, Any], proposed: dict[str, Any]
) -> tuple[dict[str, str], ...]:
    original_modes = {
        scene["scene_id"]: scene["presentation_mode"] for scene in original.get("scenes", [])
    }
    return tuple(
        {
            "scene_id": scene["scene_id"],
            "field": "presentation_mode",
            "before": original_modes[scene["scene_id"]],
            "after": scene["presentation_mode"],
        }
        for scene in sorted(proposed.get("scenes", []), key=lambda item: item["discourse_order"])
        if original_modes.get(scene["scene_id"]) != scene["presentation_mode"]
    )


def _unique_contiguous(
    values: list[dict[str, Any]], id_key: str, ordinal_key: str, error_code: str
) -> None:
    identities = [item[id_key] for item in values]
    ordinals = [item[ordinal_key] for item in values]
    if len(set(identities)) != len(identities) or sorted(ordinals) != list(
        range(1, len(values) + 1)
    ):
        raise CompilerContractError(error_code)


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref["object_type"]), str(ref["object_id"])


def _validate_no_runtime_identity(value: Any) -> None:
    if isinstance(value, dict):
        if _RUNTIME_KEYS.intersection(value):
            raise CompilerContractError("compiler_story_plan_runtime_identity_forbidden")
        for item in value.values():
            _validate_no_runtime_identity(item)
    elif isinstance(value, list):
        for item in value:
            _validate_no_runtime_identity(item)


__all__ = [
    "NOVEL_PLAN_CANDIDATE_SCHEMA_ID",
    "NOVEL_PLAN_SCHEMA_ID",
    "STORY_PLANNER_COMPONENT_VERSION",
    "STORY_PLANNER_REPAIR_VERSION",
    "STORY_PLANNER_STRUCTURAL_REPAIR_VERSION",
    "NovelPlanRepairResult",
    "NovelPlanValidationReport",
    "NovelPlanViolation",
    "canonicalize_novel_plan",
    "inspect_novel_plan_candidate",
    "repair_novel_plan_candidate",
    "story_planner_component_fingerprint",
    "validate_novel_plan_candidate",
]
