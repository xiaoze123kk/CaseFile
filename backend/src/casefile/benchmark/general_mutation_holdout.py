"""Private M3.4 General Mutation Holdout package validation."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from casefile.benchmark.eval_core import EvalSuite, EvalTask

HOLDOUT_SCHEMA_VERSION = "casefile-general-mutation-holdout-v1"
HOLDOUT_DESCRIPTOR_VERSION = "casefile-general-mutation-holdout-descriptor-v1"
HOLDOUT_SUITE_ID = "general-mutation-capability-holdout-v1"
GATE_POLICY_VERSION = "general-mutation-gate-v1"
DEFAULT_DESCRIPTOR = (
    Path(__file__).with_name("policies") / "general-mutation-holdout-v1-descriptor.json"
)
FAMILY_DISTRIBUTION = {
    "existing_update": 4,
    "multi_field": 3,
    "create": 4,
    "cross_reference": 4,
    "multi_object": 3,
    "delete": 3,
    "closure_sensitive": 3,
}


class HoldoutContractError(ValueError):
    """Stable private-package validation error."""


def load_holdout_suite(suite_path: Path, *, descriptor_path: Path | None = None) -> EvalSuite:
    suite_file = suite_path.resolve()
    package_root = suite_file.parent
    raw = _read_object(suite_file)
    if set(raw) != {"schema_version", "suite_id", "suite_role", "gate_policy_version", "tasks"}:
        raise HoldoutContractError("general_mutation_holdout_suite_keys_invalid")
    if (
        raw["schema_version"] != HOLDOUT_SCHEMA_VERSION
        or raw["suite_id"] != HOLDOUT_SUITE_ID
        or raw["suite_role"] != "holdout"
        or raw["gate_policy_version"] != GATE_POLICY_VERSION
    ):
        raise HoldoutContractError("general_mutation_holdout_identity_invalid")
    manifests = raw["tasks"]
    if not isinstance(manifests, list) or len(manifests) != 24:
        raise HoldoutContractError("general_mutation_holdout_task_count_invalid")
    tasks: list[EvalTask] = []
    package_paths = {suite_file}
    oracle_paths: set[Path] = set()
    reference_paths: set[Path] = set()
    canonical_tasks: list[Any] = []
    for relative in manifests:
        manifest_path = _private_path(package_root, relative)
        manifest = _read_object(manifest_path)
        if set(manifest) != {
            "task_id",
            "family",
            "input",
            "oracle",
            "reference",
            "tags",
            "difficulty",
        }:
            raise HoldoutContractError("general_mutation_holdout_task_keys_invalid")
        input_path = _private_path(package_root, manifest["input"])
        oracle_path = _private_path(package_root, manifest["oracle"])
        reference_path = _private_path(package_root, manifest["reference"])
        input_value = _read_object(input_path)
        oracle = _read_object(oracle_path)
        reference = _read_object(reference_path)
        if set(input_value) != {"fixture", "message"}:
            raise HoldoutContractError("general_mutation_holdout_input_invalid")
        if set(oracle) != {"acceptable_statuses", "required_state", "forbidden_changes"}:
            raise HoldoutContractError("general_mutation_holdout_oracle_invalid")
        serialized_input = json.dumps(input_value, ensure_ascii=False).lower()
        if "oracle" in serialized_input or "reference" in serialized_input:
            raise HoldoutContractError("general_mutation_holdout_oracle_leaked")
        fixture_path = (_repo_root(package_root) / str(input_value["fixture"])).resolve()
        try:
            fixture_path.relative_to(package_root)
        except ValueError as error:
            raise HoldoutContractError("general_mutation_holdout_fixture_not_private") from error
        if not fixture_path.is_file():
            raise HoldoutContractError("general_mutation_holdout_fixture_missing")
        package_paths.update({manifest_path, input_path, oracle_path, reference_path, fixture_path})
        oracle_paths.add(oracle_path)
        reference_paths.add(reference_path)
        canonical_tasks.append({"manifest": manifest, "input": input_value, "oracle": oracle})
        tasks.append(
            EvalTask(
                task_id=str(manifest["task_id"]),
                policy_key=(str(manifest["family"]), GATE_POLICY_VERSION),
                automation="agent",
                input=input_value,
                oracle=oracle,
                reference_path=str(reference_path),
                tags=tuple(str(item) for item in cast(Sequence[Any], manifest["tags"])),
                difficulty=str(manifest["difficulty"]),
                topology=str(manifest["family"]),
            )
        )
        if not reference:
            raise HoldoutContractError("general_mutation_holdout_reference_invalid")
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise HoldoutContractError("general_mutation_holdout_task_id_duplicate")
    if len({_hash(item) for item in canonical_tasks}) != len(canonical_tasks):
        raise HoldoutContractError("general_mutation_holdout_task_duplicate")
    if Counter(task.policy_key[0] for task in tasks) != Counter(FAMILY_DISTRIBUTION):
        raise HoldoutContractError("general_mutation_holdout_distribution_invalid")
    author = _read_object(package_root / "author-attestation.json")
    reviewer = _read_object(package_root / "reviewer-attestation.json")
    _validate_attestation(author, role="author", task_count=24)
    _validate_attestation(reviewer, role="reviewer", task_count=24)
    package_paths.update(
        {package_root / "author-attestation.json", package_root / "reviewer-attestation.json"}
    )
    package_fingerprint = _paths_hash(package_paths, package_root)
    oracle_fingerprint = _paths_hash(oracle_paths, package_root)
    reference_fingerprint = _paths_hash(reference_paths, package_root)
    review_fingerprint = _hash({"author": author, "reviewer": reviewer})
    descriptor = _read_object((descriptor_path or DEFAULT_DESCRIPTOR).resolve())
    expected_descriptor = {
        "schema_version": HOLDOUT_DESCRIPTOR_VERSION,
        "suite_id": HOLDOUT_SUITE_ID,
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
        raise HoldoutContractError("general_mutation_holdout_descriptor_mismatch")
    return EvalSuite(
        suite_id=HOLDOUT_SUITE_ID,
        suite_kind="capability",
        schema_version=HOLDOUT_SCHEMA_VERSION,
        tasks=tuple(tasks),
        fingerprint=package_fingerprint,
        suite_role="holdout",
        metadata={
            "oracle_fingerprint": oracle_fingerprint,
            "reference_fingerprint": reference_fingerprint,
            "review_fingerprint": review_fingerprint,
            "gate_policy_version": GATE_POLICY_VERSION,
        },
    )


def _validate_attestation(value: Mapping[str, Any], *, role: str, task_count: int) -> None:
    if set(value) != {
        "schema_version",
        "role",
        "suite_id",
        "task_count",
        "declarations",
        "signed_at",
    }:
        raise HoldoutContractError("general_mutation_holdout_attestation_keys_invalid")
    if (
        value["schema_version"] != "casefile-general-mutation-holdout-attestation-v1"
        or value["role"] != role
        or value["suite_id"] != HOLDOUT_SUITE_ID
        or value["task_count"] != task_count
    ):
        raise HoldoutContractError("general_mutation_holdout_attestation_invalid")
    declarations = value["declarations"]
    if not isinstance(declarations, list) or not declarations or not all(declarations):
        raise HoldoutContractError("general_mutation_holdout_attestation_invalid")


def _private_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise HoldoutContractError("general_mutation_holdout_path_invalid")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise HoldoutContractError("general_mutation_holdout_path_escape") from error
    return resolved


def _repo_root(package_root: Path) -> Path:
    for parent in package_root.parents:
        if (parent / "backend/pyproject.toml").is_file():
            return parent
    raise HoldoutContractError("general_mutation_holdout_repo_root_missing")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HoldoutContractError(f"general_mutation_holdout_json_invalid:{path}") from error
    if not isinstance(value, dict):
        raise HoldoutContractError("general_mutation_holdout_object_required")
    return value


def _paths_hash(paths: set[Path], root: Path) -> str:
    value = [
        (path.relative_to(root).as_posix(), json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(paths, key=lambda item: item.as_posix())
    ]
    return _hash(value)


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_DESCRIPTOR",
    "FAMILY_DISTRIBUTION",
    "GATE_POLICY_VERSION",
    "HOLDOUT_SCHEMA_VERSION",
    "HoldoutContractError",
    "load_holdout_suite",
]
