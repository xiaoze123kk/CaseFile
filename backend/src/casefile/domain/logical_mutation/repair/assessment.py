"""Turn one blocked mutation simulation into deterministic repair obligations."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from casefile.domain.logical_mutation.models import (
    ClosureLevel,
    ClosureObjectRef,
    ClosureObjectRole,
    MutationSet,
)
from casefile.domain.logical_mutation.policy import ACTIVE_APPLY_POLICY
from casefile.domain.logical_mutation.repair.models import (
    ClosureObligation,
    ClosureRepairAssessment,
    RepairPolicy,
)
from casefile.domain.logical_mutation.repair.policy import (
    REPAIR_POLICY_V1,
    repair_policy,
    validate_repair_policy_version,
)

if TYPE_CHECKING:
    from casefile.domain.verification_engine import MutationSimulation, VerificationFinding

_VALID_OBJECT_ROLES = {
    "subject",
    "prerequisite",
    "evidence",
    "path",
    "resolution",
    "event",
    "entity",
    "location",
    "lock",
    "related",
}


def assess_closure_repair(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    *,
    repair_policy_version: str = REPAIR_POLICY_V1,
) -> ClosureRepairAssessment:
    validate_repair_policy_version(repair_policy_version)
    if mutation_set.actor != "agent":
        return _not_applicable("repair_actor_ineligible")
    if (
        mutation_set.closure_policy_version != ACTIVE_APPLY_POLICY
        or simulation.closure_policy_version != ACTIVE_APPLY_POLICY
        or simulation.closure_policy_version != mutation_set.closure_policy_version
    ):
        return _not_applicable("repair_closure_policy_stale")
    if not simulation.valid or simulation.normalized_mutation is None:
        return _not_applicable("repair_simulation_incomplete")
    if simulation.can_apply:
        return _not_applicable("repair_not_required")

    contract_error = _validate_final_finding_contracts(
        simulation.final_findings,
        repair_policy_version=repair_policy_version,
    )
    if contract_error is not None:
        return _blocked(contract_error, (item.finding_key for item in simulation.final_findings))

    hard_keys = tuple(
        sorted(
            finding.finding_key
            for finding in simulation.final_findings
            if finding.payload.get("closure_level") == "hard_invariant"
        )
    )
    if hard_keys:
        return ClosureRepairAssessment(
            "blocked", "repair_hard_invariant_present", blocking_finding_keys=hard_keys
        )

    candidate_keys = (
        set(simulation.introduced_finding_keys)
        | set(simulation.worsened_finding_keys)
    ) & set(simulation.authorization_required_finding_keys)
    if not candidate_keys:
        return _not_applicable("repair_no_eligible_blocking_finding")

    findings_by_key = {item.finding_key: item for item in simulation.final_findings}
    obligations: list[ClosureObligation] = []
    for finding_key in sorted(candidate_keys):
        finding = findings_by_key.get(finding_key)
        if finding is None:
            return _blocked("repair_finding_contract_invalid", candidate_keys)
        try:
            obligation = _obligation_from_finding(
                mutation_set,
                simulation,
                finding,
                repair_policy_version=repair_policy_version,
            )
        except ValueError as error:
            reason_code = str(error).split(":", 1)[0]
            return _blocked(reason_code, candidate_keys)
        obligations.append(obligation)

    manual = tuple(
        sorted(
            item.source_finding_key
            for item in obligations
            if item.automation != "agent"
        )
    )
    if manual:
        return ClosureRepairAssessment(
            "manual_required",
            "repair_manual_required",
            tuple(obligations),
            blocking_finding_keys=tuple(sorted(candidate_keys)),
            manual_finding_keys=manual,
        )
    return ClosureRepairAssessment(
        "eligible",
        "repair_agent_eligible",
        tuple(obligations),
        blocking_finding_keys=tuple(sorted(candidate_keys)),
    )


def _obligation_from_finding(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    finding: VerificationFinding,
    *,
    repair_policy_version: str,
) -> ClosureObligation:
    level = finding.payload.get("closure_level")
    if level != "repair_required":
        raise ValueError("repair_finding_level_invalid")
    if finding.payload.get("closure_policy_version") != simulation.closure_policy_version:
        raise ValueError("repair_finding_policy_drift")
    policy = repair_policy(
        finding.rule_code,
        cast(ClosureLevel, level),
        version=repair_policy_version,
    )
    if policy.automation not in {"agent", "manual"}:
        raise ValueError("repair_finding_ineligible")
    object_refs = _validate_finding_contract(finding, policy)
    return ClosureObligation(
        obligation_key=_obligation_key(
            repair_policy_version,
            simulation.closure_policy_version,
            finding.finding_key,
        ),
        source_finding_key=finding.finding_key,
        rule_code=finding.rule_code,
        closure_policy_version=simulation.closure_policy_version,
        repair_policy_version=repair_policy_version,
        base_draft_id=mutation_set.base_draft_id,
        base_revision=mutation_set.base_revision,
        candidate_hash=simulation.candidate_hash,
        object_refs=object_refs,
        caused_by_operation_ids=_string_tuple(
            finding.payload.get("caused_by_operation_ids")
        ),
        dependency_path=_string_tuple(finding.payload.get("dependency_path")),
        allowed_repair_kinds=policy.allowed_repair_kinds,
        automation=policy.automation,
        max_operations=policy.max_operations,
        allow_create=policy.allow_create,
        allow_delete=policy.allow_delete,
    )


def _validate_final_finding_contracts(
    findings: Sequence[VerificationFinding],
    *,
    repair_policy_version: str,
) -> str | None:
    for finding in findings:
        level = finding.payload.get("closure_level")
        if level is None:
            continue
        if level not in {"hard_invariant", "repair_required", "warning"}:
            return "repair_finding_level_invalid"
        try:
            policy = repair_policy(
                finding.rule_code,
                cast(ClosureLevel, level),
                version=repair_policy_version,
            )
            _validate_finding_contract(finding, policy)
        except ValueError as error:
            return str(error).split(":", 1)[0]
    return None


def _validate_finding_contract(
    finding: VerificationFinding, policy: RepairPolicy
) -> tuple[ClosureObjectRef, ...]:
    repair_kinds = _string_tuple(finding.payload.get("repair_kinds"))
    if repair_kinds != policy.allowed_repair_kinds:
        raise ValueError("repair_policy_kind_drift")
    object_refs = _object_refs(finding.payload.get("object_refs"))
    _validate_object_roles(object_refs, policy)
    return object_refs


def _object_refs(value: Any) -> tuple[ClosureObjectRef, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError("repair_object_refs_invalid")
    result: list[ClosureObjectRef] = []
    identities: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("repair_object_refs_invalid")
        object_id = raw.get("object_id")
        role = raw.get("role")
        if (
            not isinstance(object_id, str)
            or not object_id.strip()
            or not isinstance(role, str)
            or role not in _VALID_OBJECT_ROLES
        ):
            raise ValueError("repair_object_refs_invalid")
        identity = (object_id, role)
        if identity in identities:
            raise ValueError("repair_object_ref_duplicate")
        identities.add(identity)
        result.append(ClosureObjectRef(object_id, cast(ClosureObjectRole, role)))
    if not result:
        raise ValueError("repair_object_refs_missing")
    return tuple(result)


def _validate_object_roles(
    object_refs: tuple[ClosureObjectRef, ...], policy: RepairPolicy
) -> None:
    roles = {item.role for item in object_refs}
    if not set(policy.required_object_roles).issubset(roles):
        raise ValueError("repair_policy_required_role_missing")
    if not roles.issubset(policy.allowed_object_roles):
        raise ValueError("repair_policy_object_role_invalid")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError("repair_finding_contract_invalid")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("repair_finding_contract_invalid")
    return tuple(value)


def _obligation_key(
    repair_policy_version: str, closure_policy_version: str, finding_key: str
) -> str:
    digest = hashlib.sha256(
        f"{repair_policy_version}\0{closure_policy_version}\0{finding_key}".encode()
    ).hexdigest()[:24]
    return f"obl_{digest}"


def _not_applicable(reason_code: str) -> ClosureRepairAssessment:
    return ClosureRepairAssessment("not_applicable", reason_code)


def _blocked(reason_code: str, keys: Iterable[str]) -> ClosureRepairAssessment:
    return ClosureRepairAssessment(
        "blocked", reason_code, blocking_finding_keys=tuple(sorted(keys))
    )
