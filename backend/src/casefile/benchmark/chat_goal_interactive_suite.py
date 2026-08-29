"""Strict M3.8 Interactive Goal dev and private-Holdout suite contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import rfc8785
from pydantic import Field, ValidationError, model_validator

from casefile.agent_runtime.goal.filter import goal_candidate_filter
from casefile.agent_runtime.models import StrictAgentOutput
from casefile.contracts import ContractValidationError, validate_casefile

SUITE_SCHEMA_VERSION = "casefile-chat-goal-interactive-suite-v2"
SCENARIO_SCHEMA_VERSION = "casefile-chat-goal-interactive-scenario-v2"
ATTESTATION_SCHEMA_VERSION = "casefile-chat-goal-interactive-attestation-v2"
DESCRIPTOR_SCHEMA_VERSION = "casefile-chat-goal-interactive-descriptor-v2"
SUITE_ID = "casefile-chat-goal-interactive-holdout-v2"
GATE_POLICY_VERSION = "casefile-chat-goal-interactive-gate-v2"
TRIALS_PER_SCENARIO = 3

GoalStatus = Literal[
    "interpreting",
    "running",
    "waiting_clarification",
    "waiting_patch_review",
    "stale",
    "completed",
    "cancelled",
    "superseded",
    "failed",
]

DETECTABLE_FORBIDDEN_EFFECTS = frozenset(
    {
        "auto_apply",
        "unsafe_apply",
        "stale_apply",
        "duplicate_apply",
        "invalid_observation_reuse",
        "lost_delivery",
        "reordered_delivery",
        "cross_goal_delivery",
        "duplicate_continuation",
        "post_cancel_mutation",
        "post_superseded_mutation",
        "goal_lineage_error",
        "cross_project_lineage",
        "internal_leak",
        "sensitive_leak",
        "relationship_mutation",
        "duplicate_goal",
        "unexpected_patch",
        "midrun_follow_up_queued",
    }
)
REQUIRED_ATTESTATION_DECLARATIONS = frozenset(
    {
        "coverage_matrix_reviewed",
        "input_oracle_reference_separation_reviewed",
        "reference_feasibility_replayed",
        "scenario_oracles_independently_reviewed",
    }
)

FAMILY_DISTRIBUTION = {
    "steer_refine": 3,
    "steer_constraint": 3,
    "steer_obligation": 3,
    "replace_lineage": 3,
    "follow_up_lineage": 3,
    "clarification_resume": 3,
    "patch_review_resume": 3,
    "stale_interrupt_safety": 3,
}

DEFAULT_DEV_SUITE = (
    Path(__file__).parents[4]
    / "fixtures"
    / "chat_goal_interactive_benchmark"
    / "v2"
    / "dev-suite.json"
)
DEFAULT_DESCRIPTOR = (
    Path(__file__).with_name("policies")
    / "chat-goal-interactive-holdout-v2-descriptor.json"
)


class InteractiveSuiteError(ValueError):
    """Stable fail-closed suite/package validation error."""


class InjectionPoint(StrictAgentOutput):
    kind: Literal["safe_point", "goal_status", "goal_completed"]
    safe_point: Literal["before_controller", "after_capability", "before_finalizer"] | None = (
        None
    )
    capability: Literal["analyze", "audit", "propose_mutation"] | None = None
    ordinal: int | None = Field(default=None, ge=1, le=8)
    goal_status: Literal[
        "running",
        "waiting_clarification",
        "waiting_patch_review",
        "stale",
        "completed",
    ] | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> InjectionPoint:
        if self.kind == "safe_point":
            if self.safe_point is None or self.goal_status is not None:
                raise ValueError("interactive_injection_safe_point_invalid")
        elif self.kind == "goal_status":
            if self.goal_status is None or self.safe_point is not None:
                raise ValueError("interactive_injection_goal_status_invalid")
        elif self.safe_point is not None or self.goal_status is not None:
            raise ValueError("interactive_injection_goal_completed_invalid")
        return self


class InteractiveInjectedMessage(StrictAgentOutput):
    delivery_mode: Literal["steer", "follow_up", "replace"]
    message: str = Field(min_length=1, max_length=4_000)


class InteractiveAction(StrictAgentOutput):
    at: InjectionPoint
    action: Literal[
        "messages",
        "cancel",
        "patch_apply",
        "patch_reject",
        "external_revision",
    ]
    messages: list[InteractiveInjectedMessage] | None = Field(
        default=None, min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def valid_shape(self) -> InteractiveAction:
        if self.action == "messages":
            if not self.messages:
                raise ValueError("interactive_message_action_invalid")
        elif self.messages is not None:
            raise ValueError("interactive_non_message_action_invalid")
        return self


class InteractiveScenarioInput(StrictAgentOutput):
    fixture: str = Field(min_length=1, max_length=500)
    initial_message: str = Field(min_length=1, max_length=4_000)
    actions: list[InteractiveAction] = Field(min_length=1, max_length=4)


class InteractiveStateAssertion(StrictAgentOutput):
    collection: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    where: dict[str, Any] = Field(default_factory=dict)
    count: int | None = Field(default=None, ge=0)


class InteractiveStateOracle(StrictAgentOutput):
    acceptable_statuses: list[str] = Field(min_length=1, max_length=6)
    required_state: list[InteractiveStateAssertion] = Field(min_length=1, max_length=12)
    forbidden_changes: list[str] = Field(default_factory=list, max_length=20)


class InteractiveExpectedTransition(StrictAgentOutput):
    goal: Literal["initial", "latest"]
    from_status: GoalStatus | None = None
    to_status: GoalStatus
    reason_code: str | None = None


class InteractiveExpectedMessageOutcome(StrictAgentOutput):
    delivery_mode: Literal["steer", "follow_up", "replace"]
    result: Literal["accepted", "rejected"]
    final_delivery_status: Literal["consumed", "cancelled"] | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> InteractiveExpectedMessageOutcome:
        if self.result == "accepted":
            if self.final_delivery_status is None or self.error_code is not None:
                raise ValueError("interactive_message_outcome_accepted_invalid")
        elif self.error_code is None or self.final_delivery_status is not None:
            raise ValueError("interactive_message_outcome_rejected_invalid")
        return self


class InteractiveExpectedEffects(StrictAgentOutput):
    goal_session_count: int = Field(ge=1, le=4)
    final_status: GoalStatus | None = None
    revision_count_min: int = Field(default=1, ge=1, le=8)
    amendment_kinds: list[
        Literal[
            "initial",
            "refine",
            "add_constraint",
            "add_obligation",
            "remove_obligation",
            "post_apply",
        ]
    ] = Field(default_factory=list, max_length=6)
    goal_text_all: list[str] = Field(default_factory=list, max_length=12)
    obligation_delta: int | None = Field(default=None, ge=-8, le=8)
    predecessor_status: GoalStatus | None = None
    successor_status: GoalStatus | None = None
    min_task_slices: int = Field(default=1, ge=1, le=12)
    min_reused_observations: int = Field(default=0, ge=0, le=20)
    min_recomputed_observations: int = Field(default=0, ge=0, le=20)
    post_apply_revision: bool = False
    verification_trigger: Literal["post_apply"] | None = None
    draft_revision_delta: int = Field(default=0, ge=0, le=4)
    patch_statuses: list[Literal["pending", "stale", "applied", "rejected"]] = Field(
        default_factory=list, max_length=6
    )
    patch_operation_types: list[
        Literal["create_object", "update_field", "delete_object"]
    ] = Field(default_factory=list, max_length=3)
    patch_target_collections: list[str] = Field(default_factory=list, max_length=8)
    required_transitions: list[InteractiveExpectedTransition] = Field(
        default_factory=list, max_length=12
    )
    state_oracle: InteractiveStateOracle | None = None


class InteractiveScenarioOracle(StrictAgentOutput):
    effects: InteractiveExpectedEffects
    message_outcomes: list[InteractiveExpectedMessageOutcome] = Field(
        default_factory=list, max_length=12
    )
    forbidden: list[
        Literal[
            "auto_apply",
            "unsafe_apply",
            "stale_apply",
            "duplicate_apply",
            "invalid_observation_reuse",
            "lost_delivery",
            "reordered_delivery",
            "cross_goal_delivery",
            "duplicate_continuation",
            "post_cancel_mutation",
            "post_superseded_mutation",
            "goal_lineage_error",
            "cross_project_lineage",
            "internal_leak",
            "sensitive_leak",
            "relationship_mutation",
            "duplicate_goal",
            "unexpected_patch",
            "midrun_follow_up_queued",
        ]
    ] = Field(min_length=1, max_length=40)


class InteractiveScenarioReference(StrictAgentOutput):
    schema_version: Literal["casefile-chat-goal-interactive-reference-v2"] = (
        "casefile-chat-goal-interactive-reference-v2"
    )
    feasibility: Literal["deterministic_replay", "reviewed_runtime_contract"]
    covered_effects: list[str] = Field(min_length=1, max_length=30)
    trace_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class InteractiveScenario(StrictAgentOutput):
    schema_version: Literal["casefile-chat-goal-interactive-scenario-v2"] = (
        "casefile-chat-goal-interactive-scenario-v2"
    )
    scenario_id: str = Field(pattern=r"^interactive_[a-z0-9_]+$")
    family: Literal[
        "steer_refine",
        "steer_constraint",
        "steer_obligation",
        "replace_lineage",
        "follow_up_lineage",
        "clarification_resume",
        "patch_review_resume",
        "stale_interrupt_safety",
    ]
    safety: bool = False
    input: InteractiveScenarioInput
    oracle: InteractiveScenarioOracle
    reference: InteractiveScenarioReference
    tags: list[str] = Field(default_factory=list, max_length=12)
    difficulty: Literal["dev", "formal"]

    @model_validator(mode="after")
    def oracle_matches_trace(self) -> InteractiveScenario:
        messages = [
            message
            for action in self.input.actions
            for message in (action.messages or [])
        ]
        outcomes = self.oracle.message_outcomes
        if len(messages) != len(outcomes) or any(
            message.delivery_mode != outcome.delivery_mode
            for message, outcome in zip(messages, outcomes, strict=True)
        ):
            raise ValueError("interactive_message_outcome_trace_mismatch")
        if len(set(self.oracle.forbidden)) != len(self.oracle.forbidden):
            raise ValueError("interactive_forbidden_effect_duplicate")
        effect_names = set(
            self.oracle.effects.model_dump(
                mode="json", exclude_defaults=True, exclude_none=True
            )
        )
        if self.oracle.message_outcomes:
            effect_names.add("message_outcomes")
        effect_names.add("forbidden")
        if set(self.reference.covered_effects) != effect_names or len(
            self.reference.covered_effects
        ) != len(effect_names):
            raise ValueError("interactive_reference_effect_coverage_invalid")
        trace_fingerprint = canonical_hash(
            {
                "initial_message": self.input.initial_message,
                "actions": [item.model_dump(mode="json") for item in self.input.actions],
                "oracle": self.oracle.model_dump(mode="json"),
            }
        )
        if self.reference.trace_contract_fingerprint != trace_fingerprint:
            raise ValueError("interactive_reference_trace_fingerprint_invalid")
        expected_evidence_fingerprint = canonical_hash(
            {
                "feasibility": self.reference.feasibility,
                "covered_effects": sorted(effect_names),
                "trace_contract_fingerprint": trace_fingerprint,
            }
        )
        if self.reference.evidence_fingerprint != expected_evidence_fingerprint:
            raise ValueError("interactive_reference_evidence_fingerprint_invalid")
        return self


class InteractiveSuite(StrictAgentOutput):
    schema_version: Literal["casefile-chat-goal-interactive-suite-v2"] = (
        "casefile-chat-goal-interactive-suite-v2"
    )
    suite_id: str
    suite_role: Literal["dev", "holdout"]
    gate_policy_version: Literal["casefile-chat-goal-interactive-gate-v2"] = (
        "casefile-chat-goal-interactive-gate-v2"
    )
    trials_per_scenario: Literal[3] = 3
    scenarios: list[InteractiveScenario]
    fingerprint: str
    metadata: dict[str, Any]


def load_dev_suite(path: Path = DEFAULT_DEV_SUITE) -> InteractiveSuite:
    raw = _read_object(path.resolve())
    if set(raw) != {
        "schema_version",
        "suite_id",
        "suite_role",
        "gate_policy_version",
        "trials_per_scenario",
        "scenarios",
    }:
        raise InteractiveSuiteError("interactive_dev_suite_keys_invalid")
    scenarios_raw = raw["scenarios"]
    if not isinstance(scenarios_raw, list):
        raise InteractiveSuiteError("interactive_dev_scenarios_invalid")
    if (
        raw["schema_version"] != SUITE_SCHEMA_VERSION
        or raw["suite_role"] != "dev"
        or raw["gate_policy_version"] != GATE_POLICY_VERSION
        or raw["trials_per_scenario"] != TRIALS_PER_SCENARIO
    ):
        raise InteractiveSuiteError("interactive_dev_suite_identity_invalid")
    try:
        scenarios = [InteractiveScenario.model_validate(item) for item in scenarios_raw]
    except ValidationError as error:
        raise InteractiveSuiteError("interactive_dev_scenario_contract_invalid") from error
    if len(scenarios) != len(FAMILY_DISTRIBUTION):
        raise InteractiveSuiteError("interactive_dev_scenario_count_invalid")
    if Counter(item.family for item in scenarios) != Counter(
        {family: 1 for family in FAMILY_DISTRIBUTION}
    ):
        raise InteractiveSuiteError("interactive_dev_family_distribution_invalid")
    _validate_unique_scenarios(scenarios)
    fingerprint = canonical_hash(raw)
    coverage = suite_coverage(scenarios)
    return InteractiveSuite.model_validate(
        {
            **raw,
            "scenarios": scenarios,
            "fingerprint": fingerprint,
            "metadata": {
                "package_fingerprint": fingerprint,
                "coverage": coverage,
            },
        }
    )


def load_private_holdout(
    suite_path: Path,
    *,
    descriptor_path: Path = DEFAULT_DESCRIPTOR,
) -> InteractiveSuite:
    suite_file = suite_path.resolve()
    package_root = suite_file.parent
    raw = _read_object(suite_file)
    if set(raw) != {
        "schema_version",
        "suite_id",
        "suite_role",
        "gate_policy_version",
        "trials_per_scenario",
        "scenarios",
    }:
        raise InteractiveSuiteError("interactive_holdout_suite_keys_invalid")
    if (
        raw["schema_version"] != SUITE_SCHEMA_VERSION
        or raw["suite_id"] != SUITE_ID
        or raw["suite_role"] != "holdout"
        or raw["gate_policy_version"] != GATE_POLICY_VERSION
        or raw["trials_per_scenario"] != TRIALS_PER_SCENARIO
    ):
        raise InteractiveSuiteError("interactive_holdout_identity_invalid")
    manifests = raw["scenarios"]
    if not isinstance(manifests, list) or len(manifests) != 24:
        raise InteractiveSuiteError("interactive_holdout_scenario_count_invalid")

    scenarios: list[InteractiveScenario] = []
    fixture_paths: set[Path] = set()
    canonical_oracles: list[dict[str, Any]] = []
    canonical_references: list[dict[str, Any]] = []
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise InteractiveSuiteError("interactive_holdout_manifest_invalid")
        if set(manifest) != {
            "schema_version",
            "scenario_id",
            "family",
            "safety",
            "input",
            "oracle",
            "reference",
            "tags",
            "difficulty",
        }:
            raise InteractiveSuiteError("interactive_holdout_manifest_keys_invalid")
        input_value = manifest["input"]
        oracle_value = manifest["oracle"]
        reference_value = manifest["reference"]
        if not all(
            isinstance(value, dict)
            for value in (input_value, oracle_value, reference_value)
        ):
            raise InteractiveSuiteError("interactive_holdout_scenario_parts_invalid")
        serialized_input = json.dumps(input_value, ensure_ascii=False).lower()
        if "oracle" in serialized_input or "reference" in serialized_input:
            raise InteractiveSuiteError("interactive_holdout_oracle_leaked")
        fixture = _private_path(package_root, input_value.get("fixture"))
        fixture_paths.add(fixture)
        canonical_oracles.append(oracle_value)
        canonical_references.append(reference_value)
        try:
            scenarios.append(
                InteractiveScenario.model_validate(
                    {
                        **manifest,
                        "input": {**input_value, "fixture": str(fixture)},
                        "oracle": oracle_value,
                        "reference": reference_value,
                    }
                )
            )
        except ValidationError as error:
            raise InteractiveSuiteError(
                "interactive_holdout_scenario_contract_invalid"
            ) from error

    _validate_unique_scenarios(scenarios)
    if Counter(item.family for item in scenarios) != Counter(FAMILY_DISTRIBUTION):
        raise InteractiveSuiteError("interactive_holdout_family_distribution_invalid")
    coverage = suite_coverage(scenarios)
    if not all(coverage["formal_checks"].values()):
        raise InteractiveSuiteError("interactive_holdout_coverage_incomplete")

    fixture_payloads = [
        (path.relative_to(package_root).as_posix(), _read_object(path))
        for path in sorted(fixture_paths, key=lambda item: item.as_posix())
    ]
    try:
        for _, fixture_payload in fixture_payloads:
            validate_casefile(fixture_payload)
    except ContractValidationError as error:
        raise InteractiveSuiteError(
            "interactive_holdout_fixture_contract_invalid"
        ) from error
    suite_content_fingerprint = canonical_hash(
        {"suite": raw, "fixtures": fixture_payloads}
    )
    oracle_fingerprint = canonical_hash(canonical_oracles)
    reference_fingerprint = canonical_hash(canonical_references)
    author = _read_object(_private_path(package_root, "author-attestation.json"))
    reviewer = _read_object(_private_path(package_root, "reviewer-attestation.json"))
    _validate_attestation(
        author,
        role="author",
        suite_content_fingerprint=suite_content_fingerprint,
        oracle_fingerprint=oracle_fingerprint,
        reference_fingerprint=reference_fingerprint,
    )
    _validate_attestation(
        reviewer,
        role="reviewer",
        suite_content_fingerprint=suite_content_fingerprint,
        oracle_fingerprint=oracle_fingerprint,
        reference_fingerprint=reference_fingerprint,
    )
    if author["attester_id"] == reviewer["attester_id"]:
        raise InteractiveSuiteError("interactive_holdout_review_not_independent")
    package_fingerprint = canonical_hash(
        {
            "suite": raw,
            "fixtures": fixture_payloads,
            "attestations": {"author": author, "reviewer": reviewer},
        }
    )
    review_fingerprint = canonical_hash({"author": author, "reviewer": reviewer})
    descriptor = _read_object(descriptor_path.resolve())
    expected_descriptor = {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "suite_role": "holdout",
        "gate_policy_version": GATE_POLICY_VERSION,
        "task_count": 24,
        "family_distribution": FAMILY_DISTRIBUTION,
        "suite_content_fingerprint": suite_content_fingerprint,
        "private_package_fingerprint": package_fingerprint,
        "oracle_fingerprint": oracle_fingerprint,
        "reference_fingerprint": reference_fingerprint,
        "review_fingerprint": review_fingerprint,
    }
    if descriptor != expected_descriptor:
        raise InteractiveSuiteError("interactive_holdout_descriptor_mismatch")
    return InteractiveSuite(
        schema_version="casefile-chat-goal-interactive-suite-v2",
        suite_id=SUITE_ID,
        suite_role="holdout",
        gate_policy_version="casefile-chat-goal-interactive-gate-v2",
        trials_per_scenario=3,
        scenarios=scenarios,
        fingerprint=package_fingerprint,
        metadata={
            "package_fingerprint": package_fingerprint,
            "suite_content_fingerprint": suite_content_fingerprint,
            "oracle_fingerprint": oracle_fingerprint,
            "reference_fingerprint": reference_fingerprint,
            "review_fingerprint": review_fingerprint,
            "coverage": coverage,
        },
    )


def suite_coverage(scenarios: list[InteractiveScenario]) -> dict[str, Any]:
    actions = [action for scenario in scenarios for action in scenario.input.actions]
    messages: list[
        tuple[
            InteractiveScenario,
            InteractiveAction,
            InteractiveInjectedMessage,
            InteractiveExpectedMessageOutcome,
        ]
    ] = []
    for scenario in scenarios:
        cursor = 0
        for action in scenario.input.actions:
            for message in action.messages or []:
                messages.append(
                    (scenario, action, message, scenario.oracle.message_outcomes[cursor])
                )
                cursor += 1
    action_counts = Counter(action.action for action in actions)
    delivery_counts = Counter(message.delivery_mode for _, _, message, _ in messages)
    safe_point_counts = Counter(
        action.at.safe_point for action in actions if action.at.kind == "safe_point"
    )
    injection_kind_counts = Counter(action.at.kind for action in actions)
    forbidden_counts = Counter(
        effect for scenario in scenarios for effect in scenario.oracle.forbidden
    )
    patch_statuses = {
        status
        for scenario in scenarios
        for status in scenario.oracle.effects.patch_statuses
    }
    patch_operation_types = {
        operation_type
        for scenario in scenarios
        for operation_type in scenario.oracle.effects.patch_operation_types
    }
    fixture_count = len({scenario.input.fixture for scenario in scenarios})
    multi_message_scenarios = sum(
        any(len(action.messages or []) >= 2 for action in scenario.input.actions)
        for scenario in scenarios
    )
    triple_message_scenarios = sum(
        any(len(action.messages or []) == 3 for action in scenario.input.actions)
        for scenario in scenarios
    )
    early_follow_up_rejections = sum(
        message.delivery_mode == "follow_up"
        and action.at.kind == "safe_point"
        and outcome.result == "rejected"
        and outcome.error_code == "agent_goal_state_conflict"
        for _, action, message, outcome in messages
    )
    accepted_follow_ups = sum(
        message.delivery_mode == "follow_up" and outcome.result == "accepted"
        for _, _, message, outcome in messages
    )
    obligation_amendments = {
        amendment
        for scenario in scenarios
        if scenario.family == "steer_obligation"
        for amendment in scenario.oracle.effects.amendment_kinds
    }
    after_capability_kinds = {
        action.at.capability
        for action in actions
        if action.at.kind == "safe_point"
        and action.at.safe_point == "after_capability"
        and action.at.capability is not None
    }
    replace_mixed_fifo = any(
        scenario.family == "replace_lineage"
        and any(
            len(action.messages or []) >= 2
            and {message.delivery_mode for message in action.messages or []}
            >= {"steer", "replace"}
            for action in scenario.input.actions
        )
        for scenario in scenarios
    )
    cancel_points = {
        action.at.safe_point if action.at.kind == "safe_point" else action.at.goal_status
        for action in actions
        if action.action == "cancel"
    }
    critical_forbidden = {
        "auto_apply",
        "relationship_mutation",
        "lost_delivery",
        "reordered_delivery",
        "cross_goal_delivery",
        "duplicate_continuation",
        "stale_apply",
        "post_cancel_mutation",
        "post_superseded_mutation",
        "midrun_follow_up_queued",
    }
    formal_checks = {
        "exact_24_and_8x3": len(scenarios) == 24
        and Counter(item.family for item in scenarios) == Counter(FAMILY_DISTRIBUTION),
        "safety_scenarios_at_least_9": sum(item.safety for item in scenarios) >= 9,
        "all_three_safe_points": safe_point_counts["before_controller"] >= 3
        and safe_point_counts["after_capability"] >= 3
        and safe_point_counts["before_finalizer"] >= 3,
        "waiting_and_completed_injections": injection_kind_counts["goal_status"] >= 6
        and injection_kind_counts["goal_completed"] >= 2,
        "all_action_kinds": action_counts["messages"] >= 16
        and action_counts["cancel"] >= 1
        and action_counts["patch_apply"] >= 2
        and action_counts["patch_reject"] >= 1
        and action_counts["external_revision"] >= 1,
        "all_delivery_modes": delivery_counts["steer"] >= 9
        and delivery_counts["replace"] >= 3
        and delivery_counts["follow_up"] >= 3,
        "multi_message_fifo": multi_message_scenarios >= 2
        and triple_message_scenarios >= 1,
        "replace_mixed_fifo": replace_mixed_fifo,
        "early_follow_up_rejected": early_follow_up_rejections >= 1
        and accepted_follow_ups >= 2,
        "obligation_add_and_remove": {"add_obligation", "remove_obligation"}.issubset(
            obligation_amendments
        ),
        "patch_lifecycle": {"applied", "rejected", "stale"}.issubset(patch_statuses),
        "patch_operation_diversity": {
            "create_object",
            "update_field",
            "delete_object",
        }.issubset(patch_operation_types),
        "multiple_fixtures": fixture_count >= 3,
        "after_capability_diversity": {
            "analyze",
            "audit",
            "propose_mutation",
        }.issubset(after_capability_kinds),
        "cancel_state_diversity": "stale" in cancel_points
        and any(item in cancel_points for item in {"before_controller", "after_capability"}),
        "reuse_and_recompute": sum(
            item.oracle.effects.min_reused_observations > 0 for item in scenarios
        )
        >= 3
        and sum(
            item.oracle.effects.min_recomputed_observations > 0 for item in scenarios
        )
        >= 3,
        "state_oracles": sum(
            item.oracle.effects.state_oracle is not None for item in scenarios
        )
        >= 2,
        "transition_oracles": sum(
            bool(item.oracle.effects.required_transitions) for item in scenarios
        )
        >= 12,
        "all_references_replayed": all(
            item.reference.feasibility == "deterministic_replay" for item in scenarios
        ),
        "critical_forbidden_effects": critical_forbidden.issubset(forbidden_counts),
    }
    return {
        "family_counts": dict(sorted(Counter(item.family for item in scenarios).items())),
        "safety_scenario_count": sum(item.safety for item in scenarios),
        "action_counts": dict(sorted(action_counts.items())),
        "delivery_mode_counts": dict(sorted(delivery_counts.items())),
        "safe_point_counts": {
            str(key): value for key, value in sorted(safe_point_counts.items())
        },
        "injection_kind_counts": dict(sorted(injection_kind_counts.items())),
        "multi_message_scenario_count": multi_message_scenarios,
        "triple_message_scenario_count": triple_message_scenarios,
        "early_follow_up_rejection_count": early_follow_up_rejections,
        "accepted_follow_up_count": accepted_follow_ups,
        "patch_status_coverage": sorted(patch_statuses),
        "patch_operation_type_coverage": sorted(patch_operation_types),
        "fixture_count": fixture_count,
        "obligation_amendment_coverage": sorted(obligation_amendments),
        "after_capability_coverage": sorted(after_capability_kinds),
        "cancel_point_coverage": sorted(str(item) for item in cancel_points),
        "forbidden_effect_counts": dict(sorted(forbidden_counts.items())),
        "formal_checks": formal_checks,
    }


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _validate_unique_scenarios(scenarios: list[InteractiveScenario]) -> None:
    if any(
        not goal_candidate_filter(item.input.initial_message).candidate
        for item in scenarios
    ):
        raise InteractiveSuiteError("interactive_initial_goal_not_candidate")
    for scenario in scenarios:
        messages = [
            message
            for action in scenario.input.actions
            for message in action.messages or []
        ]
        for message, outcome in zip(
            messages, scenario.oracle.message_outcomes, strict=True
        ):
            if (
                outcome.result == "accepted"
                and message.delivery_mode in {"replace", "follow_up"}
                and not goal_candidate_filter(message.message).candidate
            ):
                raise InteractiveSuiteError("interactive_successor_goal_not_candidate")
    ids = [item.scenario_id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise InteractiveSuiteError("interactive_scenario_id_duplicate")
    initial_messages = [stable_text_hash(item.input.initial_message) for item in scenarios]
    if len(initial_messages) != len(set(initial_messages)):
        raise InteractiveSuiteError("interactive_initial_message_duplicate")
    injected_messages = [
        stable_text_hash(message.message)
        for item in scenarios
        for action in item.input.actions
        for message in (action.messages or [])
    ]
    if len(injected_messages) != len(set(injected_messages)):
        raise InteractiveSuiteError("interactive_injected_message_duplicate")
    inputs = [canonical_hash(item.input.model_dump(mode="json")) for item in scenarios]
    if len(inputs) != len(set(inputs)):
        raise InteractiveSuiteError("interactive_scenario_input_duplicate")
    payloads = [
        canonical_hash(
            {
                "family": item.family,
                "safety": item.safety,
                "input": item.input.model_dump(mode="json"),
                "oracle": item.oracle.model_dump(mode="json"),
                "reference": item.reference.model_dump(mode="json"),
                "tags": item.tags,
                "difficulty": item.difficulty,
            }
        )
        for item in scenarios
    ]
    if len(payloads) != len(set(payloads)):
        raise InteractiveSuiteError("interactive_scenario_duplicate")


def stable_text_hash(value: str) -> str:
    return canonical_hash(" ".join(value.split()).casefold())


def _validate_attestation(
    value: Mapping[str, Any],
    *,
    role: str,
    suite_content_fingerprint: str,
    oracle_fingerprint: str,
    reference_fingerprint: str,
) -> None:
    if set(value) != {
        "schema_version",
        "role",
        "attester_id",
        "suite_id",
        "task_count",
        "suite_content_fingerprint",
        "oracle_fingerprint",
        "reference_fingerprint",
        "declarations",
        "signed_at",
    }:
        raise InteractiveSuiteError("interactive_holdout_attestation_keys_invalid")
    if (
        value["schema_version"] != ATTESTATION_SCHEMA_VERSION
        or value["role"] != role
        or not isinstance(value["attester_id"], str)
        or not value["attester_id"].strip()
        or value["suite_id"] != SUITE_ID
        or value["task_count"] != 24
        or value["suite_content_fingerprint"] != suite_content_fingerprint
        or value["oracle_fingerprint"] != oracle_fingerprint
        or value["reference_fingerprint"] != reference_fingerprint
        or not isinstance(value["declarations"], list)
        or set(value["declarations"]) != REQUIRED_ATTESTATION_DECLARATIONS
        or len(value["declarations"]) != len(REQUIRED_ATTESTATION_DECLARATIONS)
        or not isinstance(value["signed_at"], str)
        or "T" not in value["signed_at"]
    ):
        raise InteractiveSuiteError("interactive_holdout_attestation_invalid")


def _private_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise InteractiveSuiteError("interactive_holdout_path_invalid")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise InteractiveSuiteError("interactive_holdout_path_escape") from error
    if not resolved.is_file():
        raise InteractiveSuiteError("interactive_holdout_file_missing")
    return resolved


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InteractiveSuiteError("interactive_holdout_json_invalid") from error
    if not isinstance(value, dict):
        raise InteractiveSuiteError("interactive_holdout_object_required")
    return value


__all__ = [
    "DEFAULT_DESCRIPTOR",
    "DEFAULT_DEV_SUITE",
    "FAMILY_DISTRIBUTION",
    "GATE_POLICY_VERSION",
    "InteractiveAction",
    "InteractiveExpectedEffects",
    "InteractiveExpectedMessageOutcome",
    "InteractiveExpectedTransition",
    "InteractiveInjectedMessage",
    "InteractiveScenario",
    "InteractiveScenarioInput",
    "InteractiveScenarioOracle",
    "InteractiveSuite",
    "InteractiveSuiteError",
    "InjectionPoint",
    "SUITE_ID",
    "TRIALS_PER_SCENARIO",
    "canonical_hash",
    "load_dev_suite",
    "load_private_holdout",
    "suite_coverage",
]
