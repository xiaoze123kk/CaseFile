"""Frozen closure-repair envelope construction and application-side replay proof."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from casefile.domain.logical_mutation import CLOSURE_POLICY_VERSION, MutationSet, UpdateField
from casefile.domain.logical_mutation.repair import (
    REPAIR_POLICY_V1,
    ClosureRepairContextV1,
    ClosureRepairResult,
    RepairProposal,
    RepairUpdateOperation,
    run_closure_repair,
)
from casefile.domain.verification_engine import VerificationEngine

ClosureRepairMode = Literal["off", "shadow", "suggest"]
REPAIR_LIFECYCLE_ENVELOPE_V1 = "closure-repair-lifecycle-v1"
REPAIR_LIFECYCLE_ENVELOPE_V2 = "closure-repair-lifecycle-v2"


@dataclass(frozen=True, slots=True)
class ValidatedClosureRepair:
    mode: ClosureRepairMode
    status: str
    reason_code: str
    companion_operations: tuple[dict[str, Any], ...] = ()


class RepairTaskBinding(Protocol):
    id: int
    draft_id: int
    input_draft_revision: int
    input_jsonb: dict[str, Any]


class _ReplayProposer:
    def __init__(self, proposals: Sequence[RepairProposal]) -> None:
        self._proposals = tuple(proposals)
        self._index = 0

    def propose(self, context: ClosureRepairContextV1, *, round_no: int) -> RepairProposal:
        if self._index >= len(self._proposals) or round_no != self._index + 1:
            raise ValueError("repair_envelope_round_missing")
        proposal = self._proposals[self._index]
        self._index += 1
        if proposal.context_hash != context.context_hash:
            raise ValueError("repair_envelope_context_hash_mismatch")
        return proposal


def prepare_chat_repair_suggestions(
    frozen_document: Mapping[str, Any],
    task: RepairTaskBinding,
    suggestions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], ValidatedClosureRepair]:
    primary = [deepcopy(dict(item)) for item in suggestions]
    mutation = primary_mutation_from_suggestions(
        frozen_document,
        draft_id=int(task.draft_id),
        base_revision=int(task.input_draft_revision),
        task_run_id=int(task.id),
        suggestions=primary,
    )
    try:
        validation = validate_closure_repair_envelope(
            frozen_document,
            mutation,
            envelope,
            original_intent=str(task.input_jsonb.get("message", "")),
        )
    except ValueError as error:
        raise RuntimeError(f"Closure repair proof rejected: {error}") from error
    for companion in validation.companion_operations:
        primary.append(
            {
                "object_id": companion["object_id"],
                "path": companion["field_path"],
                "value": deepcopy(companion["new_value"]),
                "reason": companion["reason"],
                "origin": "closure_repair",
                "repair_round": companion["repair_round"],
                "repair_obligation_keys": list(companion["obligation_keys"]),
            }
        )
    return primary, validation


def repair_completion_payload(
    validation: ValidatedClosureRepair | None,
    primary_operation_count: int,
    total_operation_count: int,
    envelope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if validation is None:
        return {
            "mode": "off",
            "status": "not_run",
            "reason_code": "general_mutation_owns_patch_set",
            "primary_operation_count": primary_operation_count,
            "companion_operation_count": 0,
            "envelope": None,
        }
    return {
        "mode": validation.mode,
        "status": validation.status,
        "reason_code": validation.reason_code,
        "primary_operation_count": primary_operation_count,
        "companion_operation_count": total_operation_count - primary_operation_count,
        "envelope": deepcopy(envelope),
    }


def primary_mutation_from_suggestions(
    frozen_document: Mapping[str, Any],
    *,
    draft_id: int,
    base_revision: int,
    task_run_id: int,
    suggestions: Sequence[Mapping[str, Any]],
) -> MutationSet:
    objects = _objects_by_id(frozen_document)
    operations: list[UpdateField] = []
    for ordinal, suggestion in enumerate(suggestions, start=1):
        object_id = _required_string(suggestion, "object_id")
        field_path = _required_string(suggestion, "path")
        object_value = objects.get(object_id)
        if object_value is None:
            raise ValueError(f"repair_primary_object_missing:{object_id}")
        revision = object_value.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("repair_primary_object_revision_invalid")
        operations.append(
            UpdateField(
                operation_id=f"primary_t{task_run_id}_{ordinal:02d}",
                object_id=object_id,
                field_path=field_path,
                new_value=deepcopy(suggestion.get("value")),
                old_value=deepcopy(_pointer_value(object_value, field_path)),
                expected_object_revision=revision,
            )
        )
    return primary_mutation_from_mutation_set(MutationSet(
        mutation_set_id=f"chat_t{task_run_id}",
        base_draft_id=draft_id,
        base_revision=base_revision,
        operations=tuple(operations),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_VERSION,
    ))


def primary_mutation_from_mutation_set(mutation_set: MutationSet) -> MutationSet:
    """Common Closure Repair primary seam for legacy suggestions and M4 plans."""

    if mutation_set.actor != "agent" or mutation_set.mode != "normal":
        raise ValueError("repair_primary_mutation_policy_invalid")
    if mutation_set.closure_policy_version != CLOSURE_POLICY_VERSION:
        raise ValueError("repair_primary_mutation_policy_stale")
    return mutation_set


def closure_repair_envelope(
    *,
    mode: ClosureRepairMode,
    result: ClosureRepairResult,
) -> dict[str, Any]:
    return {
        "envelope_version": REPAIR_LIFECYCLE_ENVELOPE_V2,
        "repair_protocol_version": "semantic_alternatives_v3",
        "context_version": "closure-repair-context-v3",
        "mode": mode,
        "closure_policy_version": result.original_simulation.closure_policy_version,
        "repair_policy_version": REPAIR_POLICY_V1,
        "baseline_hash": result.original_simulation.baseline_hash,
        "original_candidate_hash": result.original_simulation.candidate_hash,
        **_result_payload(result),
    }


def validate_closure_repair_envelope(
    frozen_document: Mapping[str, Any],
    primary_mutation: MutationSet,
    envelope: Mapping[str, Any] | None,
    *,
    original_intent: str,
) -> ValidatedClosureRepair:
    if envelope is None:
        return ValidatedClosureRepair("off", "not_run", "repair_mode_off")
    envelope_version = envelope.get("envelope_version")
    if envelope_version not in {
        REPAIR_LIFECYCLE_ENVELOPE_V1,
        REPAIR_LIFECYCLE_ENVELOPE_V2,
    }:
        raise ValueError("repair_envelope_version_invalid")
    protocol_version = (
        "allowed_writes_v2"
        if envelope_version == REPAIR_LIFECYCLE_ENVELOPE_V1
        else envelope.get("repair_protocol_version")
    )
    if protocol_version not in {"allowed_writes_v2", "semantic_alternatives_v3"}:
        raise ValueError("repair_envelope_protocol_invalid")
    if (
        envelope_version == REPAIR_LIFECYCLE_ENVELOPE_V2
        and envelope.get("context_version") != "closure-repair-context-v3"
    ):
        raise ValueError("repair_envelope_context_version_invalid")
    mode_value = envelope.get("mode")
    if mode_value not in {"off", "shadow", "suggest"}:
        raise ValueError("repair_envelope_mode_invalid")
    mode = cast(ClosureRepairMode, mode_value)
    if envelope.get("closure_policy_version") != CLOSURE_POLICY_VERSION:
        raise ValueError("repair_envelope_closure_policy_stale")
    if envelope.get("repair_policy_version") != REPAIR_POLICY_V1:
        raise ValueError("repair_envelope_repair_policy_stale")

    verifier = VerificationEngine(
        profile="fast", closure_policy_version=primary_mutation.closure_policy_version
    )
    original = verifier.simulate_mutation_set(frozen_document, primary_mutation)
    if envelope.get("baseline_hash") != original.baseline_hash:
        raise ValueError("repair_envelope_baseline_hash_mismatch")
    if envelope.get("original_candidate_hash") != original.candidate_hash:
        raise ValueError("repair_envelope_candidate_hash_mismatch")

    proposals = _proposals(envelope.get("rounds"))
    replayed = run_closure_repair(
        frozen_document,
        primary_mutation,
        original,
        _ReplayProposer(proposals),
        original_intent=original_intent,
        protocol_version=cast(Any, protocol_version),
    )
    expected = _result_payload(replayed)
    supplied = {key: deepcopy(envelope.get(key)) for key in expected}
    if supplied != expected:
        raise ValueError("repair_envelope_replay_mismatch")

    companions: tuple[dict[str, Any], ...] = ()
    if mode == "suggest" and replayed.repaired:
        companions = tuple(
            cast(dict[str, Any], item.as_dict()) for item in replayed.companion_operations
        )
    return ValidatedClosureRepair(
        mode=mode,
        status=replayed.status,
        reason_code=replayed.reason_code,
        companion_operations=companions,
    )


def _result_payload(result: ClosureRepairResult) -> dict[str, Any]:
    final = result.final_simulation
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "rounds": [item.as_dict() for item in result.rounds],
        "companion_operations": [item.as_dict() for item in result.companion_operations],
        "final_candidate_hash": None if final is None else final.candidate_hash,
        "final_can_apply": None if final is None else final.can_apply,
    }


def _proposals(value: Any) -> tuple[RepairProposal, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError("repair_envelope_rounds_invalid")
    result: list[RepairProposal] = []
    for expected_round, raw_round in enumerate(value, start=1):
        if not isinstance(raw_round, Mapping) or raw_round.get("round_no") != expected_round:
            raise ValueError("repair_envelope_round_invalid")
        raw_proposal = raw_round.get("proposal")
        if not isinstance(raw_proposal, Mapping):
            raise ValueError("repair_envelope_proposal_invalid")
        raw_operations = raw_proposal.get("operations")
        if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, str | bytes):
            raise ValueError("repair_envelope_proposal_invalid")
        operations: list[RepairUpdateOperation] = []
        for raw in raw_operations:
            if not isinstance(raw, Mapping):
                raise ValueError("repair_envelope_proposal_invalid")
            keys = raw.get("obligation_keys")
            if not isinstance(keys, Sequence) or isinstance(keys, str | bytes):
                raise ValueError("repair_envelope_obligation_keys_invalid")
            operations.append(
                RepairUpdateOperation(
                    tuple(_required_string_value(item) for item in keys),
                    _required_string(raw, "object_id"),
                    _required_string(raw, "field_path"),
                    deepcopy(raw.get("new_value")),
                    _required_string(raw, "reason"),
                )
            )
        selected = raw_proposal.get("selected_alternative_id")
        if selected is not None and not isinstance(selected, str):
            raise ValueError("repair_envelope_alternative_id_invalid")
        result.append(
            RepairProposal(
                _required_string(raw_proposal, "context_hash"),
                tuple(operations),
                selected_alternative_id=selected,
            )
        )
    return tuple(result)


def _objects_by_id(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for values in document.values():
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                result[str(value["id"])] = value
    return result


def _pointer_value(value: Mapping[str, Any], path: str) -> Any:
    if not path.startswith("/"):
        raise ValueError("repair_primary_path_invalid")
    if path == "/description" and "description" not in value:
        return None
    current: Any = value
    for raw_part in path[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError("repair_primary_path_missing")
    return current


def _required_string(value: Mapping[str, Any], key: str) -> str:
    return _required_string_value(value.get(key))


def _required_string_value(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("repair_envelope_string_invalid")
    return value


__all__ = [
    "ClosureRepairMode",
    "REPAIR_LIFECYCLE_ENVELOPE_V1",
    "REPAIR_LIFECYCLE_ENVELOPE_V2",
    "ValidatedClosureRepair",
    "closure_repair_envelope",
    "prepare_chat_repair_suggestions",
    "repair_completion_payload",
    "primary_mutation_from_suggestions",
    "primary_mutation_from_mutation_set",
    "validate_closure_repair_envelope",
]
