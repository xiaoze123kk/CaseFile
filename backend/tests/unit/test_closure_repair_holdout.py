from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from casefile.benchmark.closure_repair_holdout import (
    HOLDOUT_GATE_VERSION,
    HOLDOUT_SUITE_ID,
    HoldoutContractError,
    _canonical_hash,
    _validate_attestation_hash,
    _validate_descriptor,
    _validate_distribution,
    _validate_release_cohort,
    load_holdout_suite,
)
from casefile.benchmark.eval_core import EvalTask

FAMILIES = (
    "claim_dependency_incompatible",
    "claim_refuted_without_refutation",
    "claim_supported_without_support",
)
DIFFICULTIES = ("basic", "alternative", "decoy", "dense")


def _task(
    task_id: str,
    *,
    family: str,
    automation: Literal["agent", "manual", "ineligible"],
    difficulty: str = "basic",
    topology: str = "independent",
    staged: bool = False,
) -> EvalTask:
    level = "hard_invariant" if automation == "ineligible" else "repair_required"
    return EvalTask(
        task_id=task_id,
        policy_key=(family, level),
        automation=automation,
        input={},
        oracle={},
        reference_path="reference.json",
        tags=(),
        difficulty=difficulty,
        topology=topology,
        staged=staged,
    )


def _tasks() -> tuple[EvalTask, ...]:
    agent: list[EvalTask] = []
    staged_remaining = 6
    for family in FAMILIES:
        for difficulty in DIFFICULTIES:
            for topology in ("chain", "radial"):
                staged = topology == "chain" and staged_remaining > 0
                agent.append(
                    _task(
                        f"{family}_{difficulty}_{topology}",
                        family=family,
                        automation="agent",
                        difficulty=difficulty,
                        topology=f"{family}_{topology}",
                        staged=staged,
                    )
                )
                staged_remaining -= int(staged)
    abstention = tuple(
        _task(f"manual_{index}", family=f"manual_rule_{index}", automation="manual")
        for index in range(9)
    ) + tuple(
        _task(
            f"ineligible_{index}",
            family=f"hard_rule_{index}",
            automation="ineligible",
        )
        for index in range(9)
    )
    return tuple(agent) + abstention


def _cohort(tasks: tuple[EvalTask, ...]) -> list[str]:
    selected: list[str] = []
    family_indexes = ((0, 2, 6), (0, 4, 6), (2, 4, 6))
    for family, indexes in zip(FAMILIES, family_indexes, strict=True):
        family_tasks = [task for task in tasks if task.policy_key[0] == family]
        selected.extend(family_tasks[index].task_id for index in indexes)
    selected.extend(task.task_id for task in tasks if task.automation == "manual")
    selected = selected[:14]
    selected.extend(
        task.task_id for task in tasks if task.automation == "ineligible" and len(selected) < 18
    )
    return selected


def test_holdout_distribution_and_release_cohort_are_exact() -> None:
    tasks = _tasks()
    _validate_distribution(tasks)
    cohort = _validate_release_cohort(_cohort(tasks), tasks)
    assert len(cohort) == 18


@pytest.mark.parametrize(
    "mutation",
    (
        lambda tasks: tasks[:-1],
        lambda tasks: tasks[:-1] + (tasks[0],),
        lambda tasks: tuple(
            _task(
                task.task_id,
                family=task.policy_key[0],
                automation=task.automation,
                difficulty=task.difficulty,
                topology=task.topology,
                staged=False,
            )
            for task in tasks
        ),
    ),
)
def test_holdout_distribution_fails_closed(mutation: object) -> None:
    with pytest.raises(HoldoutContractError):
        _validate_distribution(mutation(_tasks()))  # type: ignore[operator]


def test_holdout_release_cohort_rejects_unknown_and_duplicate_tasks() -> None:
    tasks = _tasks()
    with pytest.raises(HoldoutContractError, match="cohort_invalid"):
        _validate_release_cohort(_cohort(tasks)[:-1], tasks)
    with pytest.raises(HoldoutContractError, match="cohort_unknown"):
        _validate_release_cohort(_cohort(tasks)[:-1] + ["missing"], tasks)


def test_holdout_attestation_hash_rejects_tampering() -> None:
    payload = {"role": "independent_reviewer", "decision": "accepted"}
    value = {**payload, "attestation_payload_hash": _canonical_hash(payload)}
    _validate_attestation_hash(value)
    value["decision"] = "rejected"
    with pytest.raises(HoldoutContractError, match="attestation_hash_invalid"):
        _validate_attestation_hash(value)


def test_holdout_descriptor_rejects_fingerprint_mismatch() -> None:
    tasks = _tasks()
    cohort = _cohort(tasks)
    descriptor = {
        "schema_version": "casefile-closure-repair-holdout-descriptor-v1",
        "suite_id": HOLDOUT_SUITE_ID,
        "suite_role": "holdout",
        "gate_policy_version": HOLDOUT_GATE_VERSION,
        "private_package_fingerprint": "wrong",
        "oracle_fingerprint": "oracle",
        "review_fingerprint": "review",
        "task_count": 42,
        "agent_task_count": 24,
        "manual_task_count": 9,
        "ineligible_task_count": 9,
        "family_distribution": {family: 8 for family in sorted(FAMILIES)},
        "difficulty_distribution": {difficulty: 6 for difficulty in sorted(DIFFICULTIES)},
        "staged_agent_task_count": 6,
        "release_cohort_task_count": 18,
        "release_cohort_fingerprint": _canonical_hash(cohort),
    }
    with pytest.raises(HoldoutContractError, match="descriptor_mismatch"):
        _validate_descriptor(
            descriptor,
            tasks=tasks,
            release_cohort=cohort,
            package_fingerprint="package",
            oracle_fingerprint="oracle",
            review_fingerprint="review",
        )


def test_holdout_loader_fails_closed_when_private_package_is_missing(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(HoldoutContractError, match="json_invalid"):
        load_holdout_suite(tmp_path / "missing-suite.json", descriptor_path=descriptor)
