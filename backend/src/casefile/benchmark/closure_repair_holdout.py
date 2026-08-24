"""Private Closure Repair Holdout v1 package validation and loading."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from casefile.benchmark.eval_core import EvalSuite, EvalTask
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.domain.logical_mutation.repair import REPAIR_POLICY_V1, repair_policies

HOLDOUT_SCHEMA_VERSION = "casefile-closure-repair-holdout-v1"
HOLDOUT_DESCRIPTOR_VERSION = "casefile-closure-repair-holdout-descriptor-v1"
HOLDOUT_SUITE_ID = "closure-repair-capability-holdout-v1"
HOLDOUT_GATE_VERSION = "closure-repair-backend-shadow-gate-v1"
HOLDOUT_SCHEMA_VERSION_V2 = "casefile-closure-repair-holdout-v2"
HOLDOUT_DESCRIPTOR_VERSION_V2 = "casefile-closure-repair-holdout-descriptor-v2"
HOLDOUT_SUITE_ID_V2 = "closure-repair-capability-holdout-v2"
HOLDOUT_GATE_VERSION_V2 = "closure-repair-gate-v2"
DEFAULT_DESCRIPTOR_PATH = (
    Path(__file__).with_name("policies") / "closure-repair-holdout-v1-descriptor.json"
)
DEFAULT_DESCRIPTOR_PATH_V2 = (
    Path(__file__).with_name("policies") / "closure-repair-holdout-v2-descriptor.json"
)
_SUITE_IDENTITIES = {
    HOLDOUT_SCHEMA_VERSION: (
        HOLDOUT_SUITE_ID,
        HOLDOUT_GATE_VERSION,
        HOLDOUT_DESCRIPTOR_VERSION,
        DEFAULT_DESCRIPTOR_PATH,
    ),
    HOLDOUT_SCHEMA_VERSION_V2: (
        HOLDOUT_SUITE_ID_V2,
        HOLDOUT_GATE_VERSION_V2,
        HOLDOUT_DESCRIPTOR_VERSION_V2,
        DEFAULT_DESCRIPTOR_PATH_V2,
    ),
}

_FAMILIES = {
    "claim_dependency_incompatible",
    "claim_refuted_without_refutation",
    "claim_supported_without_support",
}
_DIFFICULTIES = {"basic", "alternative", "decoy", "dense"}
_SUITE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_role",
    "gate_policy_version",
    "tasks",
    "release_cohort",
}
_TASK_KEYS = {
    "task_id",
    "policy_key",
    "automation",
    "input",
    "oracle",
    "reference",
    "tags",
    "difficulty",
    "topology",
    "staged",
}
_INPUT_KEYS = {"document", "setup", "variant", "original_intent", "primary_mutation"}
_ORACLE_KEYS = {
    "expected_assessment",
    "expected_trigger",
    "acceptable_outcomes",
    "intent_assertions",
    "task_specific_assertions",
}
_DESCRIPTOR_KEYS = {
    "schema_version",
    "suite_id",
    "suite_role",
    "gate_policy_version",
    "private_package_fingerprint",
    "oracle_fingerprint",
    "review_fingerprint",
    "task_count",
    "agent_task_count",
    "manual_task_count",
    "ineligible_task_count",
    "family_distribution",
    "difficulty_distribution",
    "staged_agent_task_count",
    "release_cohort_task_count",
    "release_cohort_fingerprint",
}
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


class HoldoutContractError(ValueError):
    """A stable fail-closed private Holdout contract error."""


def load_holdout_suite(suite_path: Path, *, descriptor_path: Path | None = None) -> EvalSuite:
    suite_file = suite_path.resolve()
    root = suite_file.parent
    raw_suite = _read_object(suite_file)
    _exact_keys(raw_suite, _SUITE_KEYS, "holdout_suite_keys_invalid")
    identity = _SUITE_IDENTITIES.get(str(raw_suite["schema_version"]))
    if identity is None:
        raise HoldoutContractError("holdout_suite_identity_invalid")
    suite_id, gate_version, descriptor_version, default_descriptor = identity
    if (
        raw_suite["suite_id"] != suite_id
        or raw_suite["suite_role"] != "holdout"
        or raw_suite["gate_policy_version"] != gate_version
    ):
        raise HoldoutContractError("holdout_suite_identity_invalid")
    raw_task_paths = raw_suite["tasks"]
    if not isinstance(raw_task_paths, list) or len(raw_task_paths) != 42:
        raise HoldoutContractError("holdout_task_count_invalid")
    tasks: list[EvalTask] = []
    package_paths = {suite_file}
    oracle_paths: set[Path] = set()
    task_composites: dict[str, Any] = {}
    for relative in raw_task_paths:
        task_path = _resolve_private_path(root, suite_file.parent, relative, "task")
        raw_task = _read_object(task_path)
        _exact_keys(raw_task, _TASK_KEYS, "holdout_task_keys_invalid")
        task, resolved_paths, composite = _parse_task(raw_task, task_path, root)
        tasks.append(task)
        package_paths.update(resolved_paths)
        oracle_paths.add(resolved_paths[2])
        task_composites[task.task_id] = composite
    _validate_distribution(tasks)
    release_cohort = _validate_release_cohort(
        raw_suite["release_cohort"],
        tasks,
        require_production_reachable=(raw_suite["schema_version"] == HOLDOUT_SCHEMA_VERSION_V2),
    )
    if len({item.task_id for item in tasks}) != len(tasks):
        raise HoldoutContractError("holdout_task_id_duplicate")
    if len({_canonical_hash(value) for value in task_composites.values()}) != len(tasks):
        raise HoldoutContractError("holdout_task_canonical_duplicate")
    author_path = root / "author-attestation.json"
    reviewer_path = root / "reviewer-attestation.json"
    _validate_attestations(
        author_path,
        reviewer_path,
        tasks,
        root=root,
        task_composites=task_composites,
        suite_id=suite_id,
    )
    package_fingerprint = _paths_fingerprint(package_paths, root)
    oracle_fingerprint = _paths_fingerprint(oracle_paths, root)
    review_fingerprint = _paths_fingerprint({author_path, reviewer_path}, root)
    descriptor = _read_object((descriptor_path or default_descriptor).resolve())
    _validate_descriptor(
        descriptor,
        tasks=tasks,
        release_cohort=release_cohort,
        package_fingerprint=package_fingerprint,
        oracle_fingerprint=oracle_fingerprint,
        review_fingerprint=review_fingerprint,
        schema_version=descriptor_version,
        suite_id=suite_id,
        gate_version=gate_version,
    )
    return EvalSuite(
        suite_id=suite_id,
        suite_kind="capability",
        schema_version=str(raw_suite["schema_version"]),
        tasks=tuple(tasks),
        fingerprint=package_fingerprint,
        suite_role="holdout",
        metadata={
            "oracle_fingerprint": oracle_fingerprint,
            "review_fingerprint": review_fingerprint,
            "gate_policy_version": gate_version,
            "release_cohort": release_cohort,
            "release_cohort_fingerprint": descriptor["release_cohort_fingerprint"],
        },
    )


def _parse_task(
    raw: Mapping[str, Any], task_path: Path, root: Path
) -> tuple[EvalTask, tuple[Path, Path, Path, Path, Path], dict[str, Any]]:
    task_id = raw["task_id"]
    if not isinstance(task_id, str) or not _SNAKE_CASE.fullmatch(task_id):
        raise HoldoutContractError("holdout_task_id_invalid")
    policy_key = raw["policy_key"]
    if (
        not isinstance(policy_key, list)
        or len(policy_key) != 2
        or not all(isinstance(item, str) and item for item in policy_key)
    ):
        raise HoldoutContractError("holdout_policy_key_invalid")
    automation = raw["automation"]
    if automation not in {"agent", "manual", "ineligible"}:
        raise HoldoutContractError("holdout_automation_invalid")
    difficulty = raw["difficulty"]
    topology = raw["topology"]
    staged = raw["staged"]
    if (
        difficulty not in _DIFFICULTIES
        or not isinstance(topology, str)
        or not _SNAKE_CASE.fullmatch(topology)
    ):
        raise HoldoutContractError("holdout_stratification_invalid")
    if not isinstance(staged, bool) or (automation != "agent" and staged):
        raise HoldoutContractError("holdout_staged_invalid")
    input_path = _resolve_private_path(root, task_path.parent, raw["input"], "input")
    oracle_path = _resolve_private_path(root, task_path.parent, raw["oracle"], "oracle")
    reference_path = _resolve_private_path(root, task_path.parent, raw["reference"], "reference")
    input_value = _read_object(input_path)
    oracle = _read_object(oracle_path)
    reference = _read_object(reference_path)
    _exact_keys(input_value, _INPUT_KEYS, "holdout_input_keys_invalid")
    _exact_keys(oracle, _ORACLE_KEYS, "holdout_oracle_keys_invalid")
    document_path = _resolve_private_path(
        root, input_path.parent, input_value["document"], "document"
    )
    document = _read_object(document_path)
    try:
        validate_casefile(document)
    except ContractValidationError as error:
        raise HoldoutContractError("holdout_document_contract_invalid") from error
    if "oracle" in json.dumps(input_value, ensure_ascii=False).lower():
        raise HoldoutContractError("holdout_oracle_leaked_into_input")
    expected_by_automation = {
        "agent": "eligible",
        "manual": "manual_required",
        "ineligible": "blocked",
    }
    if oracle["expected_assessment"] != expected_by_automation[automation]:
        raise HoldoutContractError("holdout_expected_assessment_invalid")
    if oracle["expected_trigger"] != [policy_key[0]]:
        raise HoldoutContractError("holdout_expected_trigger_invalid")
    if oracle["intent_assertions"] != ["primary_mutation_preserved"]:
        raise HoldoutContractError("holdout_intent_assertions_invalid")
    outcomes = oracle["acceptable_outcomes"]
    if not isinstance(outcomes, list) or not outcomes:
        raise HoldoutContractError("holdout_acceptable_outcomes_invalid")
    tags = raw["tags"]
    if not isinstance(tags, list) or not all(isinstance(item, str) and item for item in tags):
        raise HoldoutContractError("holdout_tags_invalid")
    parsed_policy_key = (str(policy_key[0]), str(policy_key[1]))
    _validate_policy_binding(parsed_policy_key, cast(str, automation))
    _validate_reference(
        reference, task_id=task_id, automation=cast(str, automation), outcomes=outcomes
    )
    resolved_input = deepcopy(input_value)
    resolved_input["document"] = str(document_path)
    task = EvalTask(
        task_id=task_id,
        policy_key=parsed_policy_key,
        automation=cast(Any, automation),
        input=resolved_input,
        oracle=deepcopy(oracle),
        reference_path=str(reference_path),
        tags=tuple(cast(list[str], tags)),
        difficulty=str(difficulty),
        topology=topology,
        staged=staged,
    )
    return (
        task,
        (task_path, input_path, oracle_path, reference_path, document_path),
        {
            "task": raw,
            "input": input_value,
            "oracle": oracle,
            "reference": reference,
            "document": document,
        },
    )


def _validate_policy_binding(policy_key: tuple[str, str], automation: str) -> None:
    registry: dict[tuple[str, str], str] = {
        (str(item.rule_code), str(item.closure_level)): str(item.automation)
        for item in repair_policies(version=REPAIR_POLICY_V1)
    }
    if registry.get(policy_key) != automation:
        raise HoldoutContractError("holdout_policy_binding_invalid")
    if automation == "agent" and policy_key[0] not in _FAMILIES:
        raise HoldoutContractError("holdout_agent_family_invalid")


def _validate_reference(
    value: Mapping[str, Any], *, task_id: str, automation: str, outcomes: Any
) -> None:
    expected_type = "repair" if automation == "agent" else "abstention"
    expected_keys = (
        (
            {"task_id", "type", "operations"}
            if "operations" in value
            else {"task_id", "type", "rounds"}
        )
        if expected_type == "repair"
        else {"task_id", "type", "expected_status"}
    )
    if (
        set(value) != expected_keys
        or value.get("task_id") != task_id
        or value.get("type") != expected_type
    ):
        raise HoldoutContractError("holdout_reference_contract_invalid")
    if expected_type == "repair":
        operations = value.get("operations")
        rounds = value.get("rounds")
        if operations is None and not (
            isinstance(rounds, list)
            and [item.get("round_no") for item in rounds if isinstance(item, dict)] == [1, 2]
            and all(
                isinstance(item, dict)
                and set(item) == {"round_no", "operations"}
                and isinstance(item["operations"], list)
                and item["operations"]
                for item in rounds
            )
        ):
            raise HoldoutContractError("holdout_reference_rounds_invalid")
        if operations is not None and (not isinstance(operations, list) or not operations):
            raise HoldoutContractError("holdout_reference_operations_invalid")
    elif value.get("expected_status") not in outcomes:
        raise HoldoutContractError("holdout_reference_status_invalid")


def _validate_distribution(tasks: Sequence[EvalTask]) -> None:
    automation = Counter(item.automation for item in tasks)
    if automation != {"agent": 24, "manual": 9, "ineligible": 9}:
        raise HoldoutContractError("holdout_automation_distribution_invalid")
    agent = [item for item in tasks if item.automation == "agent"]
    if Counter(item.policy_key[0] for item in agent) != {family: 8 for family in _FAMILIES}:
        raise HoldoutContractError("holdout_family_distribution_invalid")
    if any(
        Counter(item.difficulty for item in agent if item.policy_key[0] == family)
        != {difficulty: 2 for difficulty in _DIFFICULTIES}
        for family in _FAMILIES
    ):
        raise HoldoutContractError("holdout_difficulty_distribution_invalid")
    if sum(item.staged for item in agent) != 6:
        raise HoldoutContractError("holdout_staged_distribution_invalid")


def _validate_release_cohort(
    value: Any,
    tasks: Sequence[EvalTask],
    *,
    require_production_reachable: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != 18 or len(set(value)) != 18:
        raise HoldoutContractError("holdout_release_cohort_invalid")
    by_id = {item.task_id: item for item in tasks}
    if any(not isinstance(item, str) or item not in by_id for item in value):
        raise HoldoutContractError("holdout_release_cohort_unknown")
    cohort = [by_id[cast(str, item)] for item in value]
    agent = [item for item in cohort if item.automation == "agent"]
    abstention = [item for item in cohort if item.automation != "agent"]
    if len(agent) != 9 or len(abstention) != 9:
        raise HoldoutContractError("holdout_release_cohort_classes_invalid")
    if Counter(item.policy_key[0] for item in agent) != {family: 3 for family in _FAMILIES}:
        raise HoldoutContractError("holdout_release_cohort_families_invalid")
    if min(Counter(item.automation for item in abstention).values()) < 4:
        raise HoldoutContractError("holdout_release_cohort_abstention_invalid")
    if set(item.difficulty for item in agent) != _DIFFICULTIES:
        raise HoldoutContractError("holdout_release_cohort_difficulty_invalid")
    if require_production_reachable and any(
        item.input["primary_mutation"].get("operation_type") != "update_field" for item in cohort
    ):
        raise HoldoutContractError("holdout_release_cohort_mutation_unsupported")
    return tuple(cast(list[str], value))


def _validate_attestations(
    author_path: Path,
    reviewer_path: Path,
    tasks: Sequence[EvalTask],
    *,
    root: Path,
    task_composites: Mapping[str, Any],
    suite_id: str,
) -> None:
    ids = sorted(item.task_id for item in tasks)
    author = _read_object(author_path)
    reviewer = _read_object(reviewer_path)
    for value, role in ((author, "author"), (reviewer, "independent_reviewer")):
        if value.get("role") != role or value.get("accepted_task_ids") != ids:
            raise HoldoutContractError("holdout_review_attestation_invalid")
        if value.get("suite_id") != suite_id or value.get("decision") != "accepted":
            raise HoldoutContractError("holdout_review_decision_invalid")
    if author.get("reviewer_id") == reviewer.get("reviewer_id"):
        raise HoldoutContractError("holdout_review_independence_invalid")
    _validate_attestation_hash(author)
    _validate_attestation_hash(reviewer)
    package_hashes = author.get("package_file_hashes")
    if not isinstance(package_hashes, dict) or not package_hashes:
        raise HoldoutContractError("holdout_author_file_hashes_invalid")
    for relative, expected in package_hashes.items():
        path = _resolve_private_path(root, root, relative, "attested_file")
        if not isinstance(expected, str) or sha256(path.read_bytes()).hexdigest() != expected:
            raise HoldoutContractError("holdout_author_file_hash_mismatch")
    reviewed = reviewer.get("reviewed_task_fingerprint")
    expected_reviewed = _canonical_hash(
        [(task_id, value) for task_id, value in sorted(task_composites.items())]
    )
    if reviewed != expected_reviewed:
        raise HoldoutContractError("holdout_reviewer_task_hash_mismatch")


def _validate_attestation_hash(value: Mapping[str, Any]) -> None:
    expected = value.get("attestation_payload_hash")
    payload = {key: item for key, item in value.items() if key != "attestation_payload_hash"}
    if not isinstance(expected, str) or expected != _canonical_hash(payload):
        raise HoldoutContractError("holdout_review_attestation_hash_invalid")


def _validate_descriptor(
    descriptor: Mapping[str, Any],
    *,
    tasks: Sequence[EvalTask],
    release_cohort: Sequence[str],
    package_fingerprint: str,
    oracle_fingerprint: str,
    review_fingerprint: str,
    schema_version: str,
    suite_id: str,
    gate_version: str,
) -> None:
    _exact_keys(descriptor, _DESCRIPTOR_KEYS, "holdout_descriptor_keys_invalid")
    automation = Counter(item.automation for item in tasks)
    family = Counter(item.policy_key[0] for item in tasks if item.automation == "agent")
    difficulty = Counter(item.difficulty for item in tasks if item.automation == "agent")
    expected = {
        "schema_version": schema_version,
        "suite_id": suite_id,
        "suite_role": "holdout",
        "gate_policy_version": gate_version,
        "private_package_fingerprint": package_fingerprint,
        "oracle_fingerprint": oracle_fingerprint,
        "review_fingerprint": review_fingerprint,
        "task_count": 42,
        "agent_task_count": 24,
        "manual_task_count": 9,
        "ineligible_task_count": 9,
        "family_distribution": dict(sorted(family.items())),
        "difficulty_distribution": dict(sorted(difficulty.items())),
        "staged_agent_task_count": 6,
        "release_cohort_task_count": 18,
        "release_cohort_fingerprint": _canonical_hash(list(release_cohort)),
    }
    if dict(descriptor) != expected or automation != {"agent": 24, "manual": 9, "ineligible": 9}:
        raise HoldoutContractError("holdout_descriptor_mismatch")


def holdout_descriptor_payload(suite_path: Path) -> dict[str, Any]:
    """Build the public descriptor after both private attestations exist."""
    suite_file = suite_path.resolve()
    root = suite_file.parent
    raw = _read_object(suite_file)
    identity = _SUITE_IDENTITIES.get(str(raw.get("schema_version")))
    if identity is None:
        raise HoldoutContractError("holdout_suite_identity_invalid")
    suite_id, gate_version, descriptor_version, _descriptor_path = identity
    paths = {suite_file}
    oracles: set[Path] = set()
    tasks: list[EvalTask] = []
    task_composites: dict[str, Any] = {}
    for relative in cast(list[str], raw.get("tasks", [])):
        task_path = _resolve_private_path(root, root, relative, "task")
        task, resolved, composite = _parse_task(_read_object(task_path), task_path, root)
        tasks.append(task)
        task_composites[task.task_id] = composite
        paths.update(resolved)
        oracles.add(resolved[2])
    _validate_distribution(tasks)
    cohort = _validate_release_cohort(
        raw.get("release_cohort"),
        tasks,
        require_production_reachable=(raw.get("schema_version") == HOLDOUT_SCHEMA_VERSION_V2),
    )
    author = root / "author-attestation.json"
    reviewer = root / "reviewer-attestation.json"
    _validate_attestations(
        author,
        reviewer,
        tasks,
        root=root,
        task_composites=task_composites,
        suite_id=suite_id,
    )
    automation = Counter(item.automation for item in tasks)
    family = Counter(item.policy_key[0] for item in tasks if item.automation == "agent")
    difficulty = Counter(item.difficulty for item in tasks if item.automation == "agent")
    return {
        "schema_version": descriptor_version,
        "suite_id": suite_id,
        "suite_role": "holdout",
        "gate_policy_version": gate_version,
        "private_package_fingerprint": _paths_fingerprint(paths, root),
        "oracle_fingerprint": _paths_fingerprint(oracles, root),
        "review_fingerprint": _paths_fingerprint({author, reviewer}, root),
        "task_count": len(tasks),
        "agent_task_count": automation["agent"],
        "manual_task_count": automation["manual"],
        "ineligible_task_count": automation["ineligible"],
        "family_distribution": dict(sorted(family.items())),
        "difficulty_distribution": dict(sorted(difficulty.items())),
        "staged_agent_task_count": sum(item.staged for item in tasks),
        "release_cohort_task_count": len(cohort),
        "release_cohort_fingerprint": _canonical_hash(list(cohort)),
    }


def _resolve_private_path(root: Path, base: Path, value: Any, kind: str) -> Path:
    if not isinstance(value, str) or not value:
        raise HoldoutContractError(f"holdout_{kind}_path_invalid")
    path = (base / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise HoldoutContractError(f"holdout_{kind}_path_escape") from error
    if not path.is_file():
        raise HoldoutContractError(f"holdout_{kind}_missing")
    return path


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HoldoutContractError(f"holdout_json_invalid:{path.name}") from error
    if not isinstance(value, dict):
        raise HoldoutContractError("holdout_json_object_required")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise HoldoutContractError(code)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _paths_fingerprint(paths: set[Path], root: Path) -> str:
    payload = [
        (str(path.relative_to(root)).replace("\\", "/"), _read_object(path))
        for path in sorted(paths)
    ]
    return _canonical_hash(payload)


__all__ = [
    "DEFAULT_DESCRIPTOR_PATH",
    "DEFAULT_DESCRIPTOR_PATH_V2",
    "HOLDOUT_SCHEMA_VERSION",
    "HOLDOUT_SCHEMA_VERSION_V2",
    "HOLDOUT_SUITE_ID_V2",
    "HoldoutContractError",
    "holdout_descriptor_payload",
    "load_holdout_suite",
]
