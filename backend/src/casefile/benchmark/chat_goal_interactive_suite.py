"""Strict M3.8 Interactive Goal dev and private-Holdout suite contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import rfc8785
from pydantic import Field, model_validator

from casefile.agent_runtime.models import StrictAgentOutput

SUITE_SCHEMA_VERSION = "casefile-chat-goal-interactive-suite-v1"
SCENARIO_SCHEMA_VERSION = "casefile-chat-goal-interactive-scenario-v1"
ATTESTATION_SCHEMA_VERSION = "casefile-chat-goal-interactive-attestation-v1"
DESCRIPTOR_SCHEMA_VERSION = "casefile-chat-goal-interactive-descriptor-v1"
SUITE_ID = "casefile-chat-goal-interactive-holdout-v1"
GATE_POLICY_VERSION = "casefile-chat-goal-interactive-gate-v1"
TRIALS_PER_SCENARIO = 3

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
    / "v1"
    / "dev-suite.json"
)
DEFAULT_DESCRIPTOR = (
    Path(__file__).with_name("policies")
    / "chat-goal-interactive-holdout-v1-descriptor.json"
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


class InteractiveAction(StrictAgentOutput):
    at: InjectionPoint
    action: Literal[
        "message",
        "cancel",
        "patch_apply",
        "patch_reject",
        "external_revision",
    ]
    delivery_mode: Literal["steer", "follow_up", "replace"] | None = None
    message: str | None = Field(default=None, min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def valid_shape(self) -> InteractiveAction:
        if self.action == "message":
            if self.delivery_mode is None or self.message is None:
                raise ValueError("interactive_message_action_invalid")
        elif self.delivery_mode is not None or self.message is not None:
            raise ValueError("interactive_non_message_action_invalid")
        return self


class InteractiveScenarioInput(StrictAgentOutput):
    fixture: str = Field(min_length=1, max_length=500)
    initial_message: str = Field(min_length=1, max_length=4_000)
    actions: list[InteractiveAction] = Field(min_length=1, max_length=4)


class InteractiveScenarioOracle(StrictAgentOutput):
    expected: dict[str, Any]
    forbidden: list[str] = Field(default_factory=list, max_length=40)


class InteractiveScenario(StrictAgentOutput):
    schema_version: Literal["casefile-chat-goal-interactive-scenario-v1"] = (
        "casefile-chat-goal-interactive-scenario-v1"
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
    reference: dict[str, Any]
    tags: list[str] = Field(default_factory=list, max_length=12)
    difficulty: Literal["dev", "formal"]


class InteractiveSuite(StrictAgentOutput):
    schema_version: Literal["casefile-chat-goal-interactive-suite-v1"] = (
        "casefile-chat-goal-interactive-suite-v1"
    )
    suite_id: str
    suite_role: Literal["dev", "holdout"]
    gate_policy_version: Literal["casefile-chat-goal-interactive-gate-v1"] = (
        "casefile-chat-goal-interactive-gate-v1"
    )
    trials_per_scenario: Literal[3] = 3
    scenarios: list[InteractiveScenario]
    fingerprint: str
    metadata: dict[str, str]


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
    scenarios = [InteractiveScenario.model_validate(item) for item in scenarios_raw]
    if len(scenarios) != len(FAMILY_DISTRIBUTION):
        raise InteractiveSuiteError("interactive_dev_scenario_count_invalid")
    if Counter(item.family for item in scenarios) != Counter(
        {family: 1 for family in FAMILY_DISTRIBUTION}
    ):
        raise InteractiveSuiteError("interactive_dev_family_distribution_invalid")
    _validate_unique_scenarios(scenarios)
    fingerprint = canonical_hash(raw)
    return InteractiveSuite.model_validate(
        {
            **raw,
            "scenarios": scenarios,
            "fingerprint": fingerprint,
            "metadata": {"package_fingerprint": fingerprint},
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

    _validate_unique_scenarios(scenarios)
    if Counter(item.family for item in scenarios) != Counter(FAMILY_DISTRIBUTION):
        raise InteractiveSuiteError("interactive_holdout_family_distribution_invalid")

    author = _read_object(_private_path(package_root, "author-attestation.json"))
    reviewer = _read_object(_private_path(package_root, "reviewer-attestation.json"))
    _validate_attestation(author, role="author")
    _validate_attestation(reviewer, role="reviewer")
    fixture_payloads = [
        (path.relative_to(package_root).as_posix(), _read_object(path))
        for path in sorted(fixture_paths, key=lambda item: item.as_posix())
    ]
    package_fingerprint = canonical_hash(
        {
            "suite": raw,
            "fixtures": fixture_payloads,
            "attestations": {"author": author, "reviewer": reviewer},
        }
    )
    oracle_fingerprint = canonical_hash(canonical_oracles)
    reference_fingerprint = canonical_hash(canonical_references)
    review_fingerprint = canonical_hash({"author": author, "reviewer": reviewer})
    descriptor = _read_object(descriptor_path.resolve())
    expected_descriptor = {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "suite_role": "holdout",
        "gate_policy_version": GATE_POLICY_VERSION,
        "task_count": 24,
        "family_distribution": FAMILY_DISTRIBUTION,
        "private_package_fingerprint": package_fingerprint,
        "oracle_fingerprint": oracle_fingerprint,
        "reference_fingerprint": reference_fingerprint,
        "review_fingerprint": review_fingerprint,
    }
    if descriptor != expected_descriptor:
        raise InteractiveSuiteError("interactive_holdout_descriptor_mismatch")
    return InteractiveSuite(
        schema_version="casefile-chat-goal-interactive-suite-v1",
        suite_id=SUITE_ID,
        suite_role="holdout",
        gate_policy_version="casefile-chat-goal-interactive-gate-v1",
        trials_per_scenario=3,
        scenarios=scenarios,
        fingerprint=package_fingerprint,
        metadata={
            "package_fingerprint": package_fingerprint,
            "oracle_fingerprint": oracle_fingerprint,
            "reference_fingerprint": reference_fingerprint,
            "review_fingerprint": review_fingerprint,
        },
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _validate_unique_scenarios(scenarios: list[InteractiveScenario]) -> None:
    ids = [item.scenario_id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise InteractiveSuiteError("interactive_scenario_id_duplicate")
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
                "reference": item.reference,
                "tags": item.tags,
                "difficulty": item.difficulty,
            }
        )
        for item in scenarios
    ]
    if len(payloads) != len(set(payloads)):
        raise InteractiveSuiteError("interactive_scenario_duplicate")


def _validate_attestation(value: Mapping[str, Any], *, role: str) -> None:
    if set(value) != {
        "schema_version",
        "role",
        "suite_id",
        "task_count",
        "declarations",
        "signed_at",
    }:
        raise InteractiveSuiteError("interactive_holdout_attestation_keys_invalid")
    if (
        value["schema_version"] != ATTESTATION_SCHEMA_VERSION
        or value["role"] != role
        or value["suite_id"] != SUITE_ID
        or value["task_count"] != 24
        or not isinstance(value["declarations"], list)
        or not value["declarations"]
        or not all(value["declarations"])
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
]
