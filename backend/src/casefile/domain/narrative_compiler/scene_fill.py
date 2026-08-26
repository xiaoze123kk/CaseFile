"""Pure semantic ownership gate for N4.4 model Scene Fill proposals."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import CompilerContractError
from casefile_contracts import SceneSemanticFillProposal


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
        raise CompilerContractError("compiler_scene_fill_contract_invalid") from error
    if value["batch_id"] != batch_view["batch_id"]:
        raise CompilerContractError("compiler_scene_fill_batch_mismatch")
    expected_scene_ids = list(batch_view["scene_ids"])
    actual_scene_ids = [scene["scene_id"] for scene in value["scenes"]]
    if actual_scene_ids != expected_scene_ids:
        raise CompilerContractError("compiler_scene_fill_scene_coverage_invalid")
    constraints = {scene["scene_id"]: scene for scene in batch_view["scenes"]}
    catalog = {_ref_key(item["object_ref"]) for item in batch_view["object_catalog"]}
    for scene_fill in value["scenes"]:
        _validate_scene_fill(scene_fill, constraints[scene_fill["scene_id"]], catalog)
    return value


def _validate_scene_fill(
    fill: dict[str, Any], constraint: dict[str, Any], catalog: set[str]
) -> None:
    model_text = "\n".join(
        [
            fill["dramatic_goal"],
            fill["conflict"],
            fill["outcome"],
            *(beat["directive"] for beat in fill["beats"]),
        ]
    )
    if any(entry_key in model_text for entry_key in constraint["forbidden_reveal_entry_keys"]):
        raise CompilerContractError("compiler_scene_fill_forbidden_reveal")
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
    for beat in fill["beats"]:
        local_key = beat["local_key"]
        if local_key in seen_local_keys:
            raise CompilerContractError("compiler_scene_fill_local_key_duplicate")
        for dependency in beat["depends_on"]:
            if dependency not in seen_local_keys:
                raise CompilerContractError("compiler_scene_fill_dependency_invalid")
        actor_keys = {_ref_key(ref) for ref in beat["actor_refs"]}
        if not actor_keys <= participants:
            raise CompilerContractError("compiler_scene_fill_actor_invalid")
        all_refs = [*beat["actor_refs"], *beat["target_refs"], *beat["basis_refs"]]
        for transition in beat["knowledge_transitions"]:
            all_refs.extend(
                [transition["subject_ref"], transition["object_ref"], *transition["basis_refs"]]
            )
        for assertion in beat["location_assertions"]:
            all_refs.extend(
                [
                    assertion["subject_ref"],
                    assertion["location_ref"],
                    *assertion["story_time_refs"],
                    *assertion["basis_refs"],
                ]
            )
        if {_ref_key(ref) for ref in all_refs} - catalog:
            raise CompilerContractError("compiler_scene_fill_reference_invalid")
        if not {_ref_key(ref) for ref in beat["basis_refs"]} <= allowed_basis:
            raise CompilerContractError("compiler_scene_fill_provenance_invalid")
        fulfilled = beat["fulfills_obligation_keys"]
        if not fulfilled and not beat["depends_on"]:
            raise CompilerContractError("compiler_scene_fill_unanchored_beat")
        for obligation_key in fulfilled:
            obligation = expected_obligations.get(obligation_key)
            if obligation is None:
                raise CompilerContractError("compiler_scene_fill_obligation_unknown")
            if obligation["kind"] != beat["kind"]:
                raise CompilerContractError("compiler_scene_fill_obligation_kind_mismatch")
            seen_obligations[obligation_key] += 1
        seen_local_keys.add(local_key)
    if seen_obligations != Counter({key: 1 for key in expected_obligations}):
        raise CompilerContractError("compiler_scene_fill_obligation_coverage_invalid")


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


__all__ = ["validate_scene_semantic_fill"]
