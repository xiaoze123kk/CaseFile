"""Pure semantic ownership gate for N4.4 model Scene Fill proposals."""

from __future__ import annotations

from collections import Counter
from typing import Any, NoReturn

from casefile_contracts import SceneSemanticFillProposal
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)


class SceneFillValidationError(CompilerContractError):
    """Scene Fill rejection with safe, deterministic failure evidence."""

    def __init__(self, reason_code: str, evidence: dict[str, Any]) -> None:
        self.evidence = evidence
        super().__init__(reason_code)


def validate_scene_semantic_fill(
    proposal: SceneSemanticFillProposal | dict[str, Any],
    *,
    batch_view: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = (
            proposal.model_dump(mode="json")
            if isinstance(proposal, SceneSemanticFillProposal)
            else SceneSemanticFillProposal.model_validate(proposal).model_dump(mode="json")
        )
    except ValidationError as error:
        raise SceneFillValidationError(
            "compiler_scene_fill_contract_invalid",
            {"batch_id": batch_view.get("batch_id"), "json_path": "/"},
        ) from error
    if value["batch_id"] != batch_view["batch_id"]:
        _raise_validation_error(
            "compiler_scene_fill_batch_mismatch",
            batch_id=str(batch_view["batch_id"]),
            json_path="/batch_id",
        )
    expected_scene_ids = list(batch_view["scene_ids"])
    actual_scene_ids = [scene["scene_id"] for scene in value["scenes"]]
    if actual_scene_ids != expected_scene_ids:
        _raise_validation_error(
            "compiler_scene_fill_scene_coverage_invalid",
            batch_id=str(batch_view["batch_id"]),
            json_path="/scenes",
        )
    constraints = {scene["scene_id"]: scene for scene in batch_view["scenes"]}
    catalog = {_ref_key(item["object_ref"]) for item in batch_view["object_catalog"]}
    for scene_index, scene_fill in enumerate(value["scenes"]):
        _validate_scene_fill(
            scene_fill,
            constraints[scene_fill["scene_id"]],
            catalog,
            batch_id=str(batch_view["batch_id"]),
            scene_index=scene_index,
        )
    return value


def _validate_scene_fill(
    fill: dict[str, Any],
    constraint: dict[str, Any],
    catalog: set[str],
    *,
    batch_id: str,
    scene_index: int,
) -> None:
    scene_path = f"/scenes/{scene_index}"
    scene_id = str(fill["scene_id"])
    model_text = "\n".join(
        [
            fill["dramatic_goal"],
            fill["conflict"],
            fill["outcome"],
            *(beat["directive"] for beat in fill["beats"]),
        ]
    )
    if any(entry_key in model_text for entry_key in constraint["forbidden_reveal_entry_keys"]):
        _raise_validation_error(
            "compiler_scene_fill_forbidden_reveal",
            batch_id=batch_id,
            scene_id=scene_id,
            json_path=scene_path,
        )
    expected_obligations = {item["obligation_key"]: item for item in constraint["obligations"]}
    seen_obligations: Counter[str] = Counter()
    seen_local_keys: set[str] = set()
    participants = {_ref_key(ref) for ref in constraint["participant_refs"]}
    allowed_basis = {_ref_key(ref) for ref in constraint["basis_refs"]}
    allowed_basis.update(
        _ref_key(ref)
        for obligation in constraint["obligations"]
        for ref in obligation["basis_refs"]
    )
    for beat_index, beat in enumerate(fill["beats"]):
        beat_path = f"{scene_path}/beats/{beat_index}"
        local_key = beat["local_key"]
        if local_key in seen_local_keys:
            _raise_validation_error(
                "compiler_scene_fill_local_key_duplicate",
                batch_id=batch_id,
                scene_id=scene_id,
                beat_local_key=str(local_key),
                json_path=f"{beat_path}/local_key",
            )
        for dependency_index, dependency in enumerate(beat["depends_on"]):
            if dependency not in seen_local_keys:
                _raise_validation_error(
                    "compiler_scene_fill_dependency_invalid",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=f"{beat_path}/depends_on/{dependency_index}",
                )
        for ref_index, ref in enumerate(beat["actor_refs"]):
            if _ref_key(ref) not in participants:
                _raise_validation_error(
                    "compiler_scene_fill_actor_invalid",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=f"{beat_path}/actor_refs/{ref_index}",
                    emitted_ref=ref,
                    allowed_refs=participants,
                )
        for json_path, ref in _beat_refs(beat, beat_path):
            if _ref_key(ref) not in catalog:
                _raise_validation_error(
                    "compiler_scene_fill_reference_invalid",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=json_path,
                    emitted_ref=ref,
                    allowed_refs=catalog,
                )
        for ref_index, ref in enumerate(beat["basis_refs"]):
            if _ref_key(ref) not in allowed_basis:
                _raise_validation_error(
                    "compiler_scene_fill_provenance_invalid",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=f"{beat_path}/basis_refs/{ref_index}",
                    emitted_ref=ref,
                    allowed_refs=allowed_basis,
                )
        fulfilled = beat["fulfills_obligation_keys"]
        if not fulfilled and not beat["depends_on"]:
            _raise_validation_error(
                "compiler_scene_fill_unanchored_beat",
                batch_id=batch_id,
                scene_id=scene_id,
                beat_local_key=str(local_key),
                json_path=beat_path,
            )
        for obligation_index, obligation_key in enumerate(fulfilled):
            obligation = expected_obligations.get(obligation_key)
            if obligation is None:
                _raise_validation_error(
                    "compiler_scene_fill_obligation_unknown",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=f"{beat_path}/fulfills_obligation_keys/{obligation_index}",
                )
            if obligation["kind"] != beat["kind"]:
                _raise_validation_error(
                    "compiler_scene_fill_obligation_kind_mismatch",
                    batch_id=batch_id,
                    scene_id=scene_id,
                    beat_local_key=str(local_key),
                    json_path=f"{beat_path}/kind",
                )
            seen_obligations[obligation_key] += 1
        seen_local_keys.add(local_key)
    if seen_obligations != Counter({key: 1 for key in expected_obligations}):
        _raise_validation_error(
            "compiler_scene_fill_obligation_coverage_invalid",
            batch_id=batch_id,
            scene_id=scene_id,
            json_path=f"{scene_path}/beats",
        )


def _beat_refs(
    beat: dict[str, Any], beat_path: str
) -> list[tuple[str, dict[str, str]]]:
    refs: list[tuple[str, dict[str, str]]] = []
    for field in ("actor_refs", "target_refs", "basis_refs"):
        refs.extend(
            (f"{beat_path}/{field}/{index}", ref)
            for index, ref in enumerate(beat[field])
        )
    for transition_index, transition in enumerate(beat["knowledge_transitions"]):
        transition_path = f"{beat_path}/knowledge_transitions/{transition_index}"
        refs.extend(
            [
                (f"{transition_path}/subject_ref", transition["subject_ref"]),
                (f"{transition_path}/object_ref", transition["object_ref"]),
            ]
        )
        refs.extend(
            (f"{transition_path}/basis_refs/{index}", ref)
            for index, ref in enumerate(transition["basis_refs"])
        )
    for assertion_index, assertion in enumerate(beat["location_assertions"]):
        assertion_path = f"{beat_path}/location_assertions/{assertion_index}"
        refs.extend(
            [
                (f"{assertion_path}/subject_ref", assertion["subject_ref"]),
                (f"{assertion_path}/location_ref", assertion["location_ref"]),
            ]
        )
        for field in ("story_time_refs", "basis_refs"):
            refs.extend(
                (f"{assertion_path}/{field}/{index}", ref)
                for index, ref in enumerate(assertion[field])
            )
    return refs


def _raise_validation_error(
    reason_code: str,
    *,
    batch_id: str,
    json_path: str,
    scene_id: str | None = None,
    beat_local_key: str | None = None,
    emitted_ref: dict[str, str] | None = None,
    allowed_refs: set[str] | None = None,
) -> NoReturn:
    evidence: dict[str, Any] = {
        "batch_id": batch_id,
        "json_path": json_path,
    }
    if scene_id is not None:
        evidence["scene_id"] = scene_id
    if beat_local_key is not None:
        evidence["beat_local_key"] = beat_local_key
    if emitted_ref is not None:
        evidence["emitted_ref"] = emitted_ref
    if allowed_refs is not None:
        ordered = sorted(allowed_refs)
        evidence["allowed_ref_count"] = len(ordered)
        evidence["allowed_ref_hash"] = canonical_json_sha256(ordered)
    raise SceneFillValidationError(reason_code, evidence)


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


__all__ = ["SceneFillValidationError", "validate_scene_semantic_fill"]
