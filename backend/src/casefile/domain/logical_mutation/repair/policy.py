"""Versioned, fail-closed repair eligibility registry."""

from __future__ import annotations

from casefile.domain.logical_mutation.graph import COLLECTION_BY_TYPE
from casefile.domain.logical_mutation.models import ClosureLevel
from casefile.domain.logical_mutation.repair.models import RepairAutomation, RepairPolicy

REPAIR_POLICY_V1 = "closure-repair-v1"
SUPPORTED_REPAIR_POLICY_VERSIONS = frozenset({REPAIR_POLICY_V1})

_ALL_ROLES = (
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
)


def _policy(
    rule_code: str,
    closure_level: ClosureLevel,
    automation: RepairAutomation,
    repair_kinds: tuple[str, ...] = (),
    *,
    required_roles: tuple[str, ...] = (),
    allowed_roles: tuple[str, ...] = _ALL_ROLES,
    max_operations: int = 0,
) -> RepairPolicy:
    return RepairPolicy(
        rule_code=rule_code,
        closure_level=closure_level,
        automation=automation,
        allowed_repair_kinds=repair_kinds,
        required_object_roles=required_roles,
        allowed_object_roles=allowed_roles,
        max_operations=max_operations,
    )


_AGENT_POLICIES = (
    _policy(
        "claim_supported_without_support",
        "repair_required",
        "agent",
        ("attach_support", "downgrade_claim_status"),
        required_roles=("subject",),
        allowed_roles=("subject",),
        max_operations=4,
    ),
    _policy(
        "claim_refuted_without_refutation",
        "repair_required",
        "agent",
        ("attach_refutation", "change_claim_status"),
        required_roles=("subject",),
        allowed_roles=("subject",),
        max_operations=4,
    ),
    _policy(
        "claim_dependency_incompatible",
        "repair_required",
        "agent",
        ("repair_dependency_claim", "change_claim_status"),
        required_roles=("subject", "prerequisite"),
        allowed_roles=("subject", "prerequisite"),
        max_operations=4,
    ),
)

_MANUAL_POLICIES = (
    _policy(
        "critical_claim_support_lost",
        "repair_required",
        "manual",
        ("attach_alternative_evidence", "downgrade_claim_status"),
        required_roles=("subject",),
    ),
    _policy(
        "hypothesis_required_claim_incompatible",
        "repair_required",
        "manual",
        ("repair_required_claim", "change_hypothesis_status"),
        required_roles=("subject", "prerequisite"),
    ),
    _policy(
        "resolution_basis_weakened",
        "repair_required",
        "manual",
        ("repair_hypothesis", "make_resolution_undetermined"),
        required_roles=("resolution", "related"),
    ),
    _policy(
        "hypothesis_assessment_status_conflict",
        "repair_required",
        "manual",
        ("review_assessments", "change_hypothesis_status"),
        required_roles=("subject",),
    ),
    _policy(
        "reasoning_required_path_without_information_input",
        "repair_required",
        "manual",
        ("attach_information_input", "make_path_optional"),
        required_roles=("path",),
    ),
    _policy(
        "reasoning_required_path_incompatible_claim_input",
        "repair_required",
        "manual",
        ("repair_input_claim", "make_path_optional"),
        required_roles=("path", "prerequisite"),
    ),
    _policy(
        "resolution_required_claim_incompatible",
        "repair_required",
        "manual",
        ("repair_required_claim", "make_resolution_undetermined"),
        required_roles=("resolution", "prerequisite"),
    ),
    _policy(
        "resolution_basis_path_unhealthy",
        "repair_required",
        "manual",
        ("repair_reasoning_path", "make_resolution_undetermined"),
        required_roles=("resolution", "path"),
    ),
    _policy(
        "knowledge_state_available_before_source",
        "repair_required",
        "manual",
        required_roles=("evidence", "entity", "event"),
    ),
    _policy(
        "temporal_exclusivity_violation",
        "repair_required",
        "manual",
        required_roles=("event", "entity"),
    ),
    _policy(
        "new_object_not_integrated",
        "repair_required",
        "manual",
        ("connect_object",),
        required_roles=("subject",),
    ),
    *tuple(
        _policy(
            f"new_{object_type}_not_reasoning_integrated",
            "repair_required",
            "manual",
            ("connect_object",),
            required_roles=("subject",),
        )
        for object_type in COLLECTION_BY_TYPE
    ),
)

_HARD_RULES = (
    "logical_relation_cycle",
    "evidence_support_reciprocity_violation",
    "evidence_refute_reciprocity_violation",
    "claim_dependency_cycle",
    "relative_time_cycle",
    "reasoning_path_target_type_invalid",
    "reasoning_step_output_type_invalid",
    "root_structure_delete_requires_explicit_restructure",
    "structure_lock_conflict",
)

_WARNING_RULES = (
    "claim_disputed_without_two_sided_evidence",
    "claim_support_refute_overlap",
    "competing_hypothesis_group_incomplete",
    "missing_evidence_assessment",
    "unscoped_evidence_assessment",
    "temporal_travel_time_violation",
)

_INELIGIBLE_POLICIES = (
    *tuple(_policy(code, "hard_invariant", "ineligible") for code in _HARD_RULES),
    *tuple(_policy(code, "warning", "ineligible") for code in _WARNING_RULES),
    _policy(
        "new_object_not_integrated",
        "warning",
        "ineligible",
        ("connect_object",),
    ),
    *tuple(
        _policy(
            f"new_{object_type}_not_reasoning_integrated",
            "warning",
            "ineligible",
            ("connect_object",),
        )
        for object_type in COLLECTION_BY_TYPE
    ),
)

_REPAIR_POLICIES_V1 = {
    (item.rule_code, item.closure_level): item
    for item in (*_AGENT_POLICIES, *_MANUAL_POLICIES, *_INELIGIBLE_POLICIES)
}


def validate_repair_policy_version(version: str) -> str:
    if version not in SUPPORTED_REPAIR_POLICY_VERSIONS:
        raise ValueError(f"repair_policy_version_unsupported:{version}")
    return version


def repair_policy(
    rule_code: str,
    closure_level: ClosureLevel,
    *,
    version: str = REPAIR_POLICY_V1,
) -> RepairPolicy:
    validate_repair_policy_version(version)
    try:
        return _REPAIR_POLICIES_V1[(rule_code, closure_level)]
    except KeyError as error:
        raise ValueError(
            f"repair_policy_unknown_rule:{rule_code}:{closure_level}"
        ) from error


def repair_policies(*, version: str = REPAIR_POLICY_V1) -> tuple[RepairPolicy, ...]:
    validate_repair_policy_version(version)
    return tuple(
        _REPAIR_POLICIES_V1[key]
        for key in sorted(_REPAIR_POLICIES_V1)
    )
