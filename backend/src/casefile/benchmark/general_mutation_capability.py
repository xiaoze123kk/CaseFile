"""Outcome-first General Mutation provider capability benchmark.

This suite starts from natural-language author requests.  Reference plans are
used only to prove that each task is solvable; the provider never receives
them.  Graders inspect the simulated CaseFile state instead of requiring a
particular plan or operation order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, cast

import rfc8785
from pydantic import ValidationError

from casefile.agent_runtime import DeepSeekAgentsProvider, OpenAIAgentsProvider
from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_BINDER_VERSION,
    GENERAL_MUTATION_PLAN_VERSION,
    GENERAL_MUTATION_POLICY_VERSION,
    GeneralMutationPlannerRequest,
    MutationPlanV1,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.application.agent_mutation import (
    GeneralMutationBindingError,
    bind_general_mutation_plan,
)
from casefile.application.v1_editing import editable_fields_by_collection
from casefile.benchmark.eval_core import EvalSuite, EvalTask
from casefile.domain.logical_mutation import CLOSURE_POLICY_VERSION
from casefile.domain.verification_engine import VerificationEngine

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SUITE = Path("fixtures/general_mutation_benchmark/capability/v1/suite.json")
SCHEMA_VERSION = "casefile-general-mutation-capability-v1"
REPORT_VERSION = "casefile-general-mutation-capability-report-v1"
HARNESS_VERSION = "general-mutation-production-kernel-v1"
GRADER_VERSION = "general-mutation-state-graders-v2"
TrialClass = Literal[
    "success",
    "capability_failure",
    "safe_block",
    "unsafe_escape",
    "protocol_failure",
    "infrastructure_failure",
]


class GeneralMutationCapabilityError(ValueError):
    """Fail-closed suite or reference contract error."""


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeneralMutationCapabilityError(f"general_mutation_json_invalid:{path}") from error
    if not isinstance(value, dict):
        raise GeneralMutationCapabilityError("general_mutation_json_object_required")
    return value


def load_capability_suite(
    repo_root: Path = ROOT, suite_path: Path | None = None
) -> EvalSuite:
    path = (suite_path or repo_root / DEFAULT_SUITE).resolve()
    payload = _read_object(path)
    if set(payload) != {"schema_version", "suite_id", "tasks"}:
        raise GeneralMutationCapabilityError("general_mutation_suite_keys_invalid")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise GeneralMutationCapabilityError("general_mutation_suite_version_invalid")
    task_paths = payload["tasks"]
    if not isinstance(task_paths, list) or not task_paths:
        raise GeneralMutationCapabilityError("general_mutation_suite_tasks_invalid")
    tasks: list[EvalTask] = []
    for relative in task_paths:
        if not isinstance(relative, str):
            raise GeneralMutationCapabilityError("general_mutation_task_path_invalid")
        task_path = (path.parent / relative).resolve()
        try:
            task_path.relative_to(path.parent)
        except ValueError as error:
            raise GeneralMutationCapabilityError("general_mutation_task_path_escape") from error
        raw = _read_object(task_path)
        expected_keys = {"task_id", "family", "input", "oracle", "reference", "tags"}
        if set(raw) != expected_keys:
            raise GeneralMutationCapabilityError("general_mutation_task_keys_invalid")
        input_value = raw["input"]
        oracle = raw["oracle"]
        if not isinstance(input_value, dict) or set(input_value) != {"fixture", "message"}:
            raise GeneralMutationCapabilityError("general_mutation_task_input_invalid")
        if not isinstance(oracle, dict) or set(oracle) != {
            "acceptable_statuses",
            "required_state",
            "forbidden_changes",
        }:
            raise GeneralMutationCapabilityError("general_mutation_task_oracle_invalid")
        if "reference" in json.dumps(input_value, ensure_ascii=False).lower():
            raise GeneralMutationCapabilityError("general_mutation_reference_leaked")
        reference_path = (task_path.parent / cast(str, raw["reference"])).resolve()
        tasks.append(
            EvalTask(
                task_id=str(raw["task_id"]),
                policy_key=(str(raw["family"]), GENERAL_MUTATION_POLICY_VERSION),
                automation="agent",
                input=input_value,
                oracle=oracle,
                reference_path=str(reference_path),
                tags=tuple(str(item) for item in cast(list[Any], raw["tags"])),
                difficulty="dev",
                topology=str(raw["family"]),
            )
        )
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise GeneralMutationCapabilityError("general_mutation_task_id_duplicate")
    fingerprint_files = sorted(path.parent.rglob("*.json"))
    fingerprint = _canonical_hash(
        [
            (str(item.relative_to(path.parent)).replace("\\", "/"), _read_object(item))
            for item in fingerprint_files
        ]
    )
    return EvalSuite(
        suite_id=str(payload["suite_id"]),
        suite_kind="capability",
        schema_version=SCHEMA_VERSION,
        tasks=tuple(tasks),
        fingerprint=fingerprint,
        suite_role="capability_dev",
    )


def validate_references(suite: EvalSuite, repo_root: Path = ROOT) -> None:
    """Prove every public task is executable without exposing its solution."""

    for index, task in enumerate(suite.tasks, start=1):
        document = _task_document(task, repo_root)
        reference = _read_object(Path(task.reference_path))
        try:
            plan = MutationPlanV1.model_validate(reference["plan"])
            bound = bind_general_mutation_plan(
                plan,
                document,
                task_run_id=index,
                draft_id=index,
                base_revision=1,
                updated_at="2042-06-01T00:00:00Z",
            )
            simulation = VerificationEngine(profile="fast").simulate_mutation_set(
                document, bound.mutation_set
            )
        except (KeyError, ValidationError, GeneralMutationBindingError) as error:
            raise GeneralMutationCapabilityError(
                f"general_mutation_reference_invalid:{task.task_id}"
            ) from error
        graders = _grade(
            task,
            document,
            simulation.document,
            verification_valid=simulation.valid,
            verification_reason=simulation.reason_code,
        )
        if not simulation.valid or not all(item["passed"] for item in graders):
            raise GeneralMutationCapabilityError(
                f"general_mutation_reference_outcome_invalid:{task.task_id}"
            )


def run_capability_benchmark(
    *,
    model_id: str,
    api_key: str,
    provider_name: Literal["deepseek", "openai"] = "deepseek",
    trials: int = 1,
    repo_root: Path = ROOT,
    suite_path: Path | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    if trials < 1:
        raise GeneralMutationCapabilityError("general_mutation_trials_invalid")
    suite = load_capability_suite(repo_root, suite_path)
    validate_references(suite, repo_root)
    injected_provider = provider is not None
    provider = provider or (
        DeepSeekAgentsProvider() if provider_name == "deepseek" else OpenAIAgentsProvider()
    )
    rows = [
        _run_trial(
            task,
            trial_index,
            task_run_id=(task_index * trials) + trial_index,
            provider=provider,
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            repo_root=repo_root,
        )
        for task_index, task in enumerate(suite.tasks)
        for trial_index in range(1, trials + 1)
    ]
    prompt = load_prompt("general_mutation_planner", GENERAL_MUTATION_PLAN_VERSION)
    lineage = {
        "prompt_version": GENERAL_MUTATION_PLAN_VERSION,
        "prompt_hash": prompt.system_prompt_sha256,
        "capability_policy_version": GENERAL_MUTATION_POLICY_VERSION,
        "binder_version": GENERAL_MUTATION_BINDER_VERSION,
        "closure_policy_version": CLOSURE_POLICY_VERSION,
        "harness_version": HARNESS_VERSION,
        "grader_version": GRADER_VERSION,
    }
    metrics = _metrics(rows, suite.tasks, trials)
    return {
        "schema_version": REPORT_VERSION,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_kind": "capability",
            "suite_role": suite.suite_role,
            "suite_fingerprint": suite.fingerprint,
            "task_count": len(suite.tasks),
        },
        "provider": provider_name,
        "model_id": model_id,
        "formal_capability": (
            not injected_provider
            and provider_name == "deepseek"
            and model_id == "deepseek-v4-pro"
        ),
        "release_gate_eligible": False,
        "trials_per_task": trials,
        "runtime_fingerprint": _canonical_hash(lineage),
        "lineage": lineage,
        "git": _git_identity(repo_root),
        "metrics": metrics,
        "rows": rows,
        "status": (
            "inconclusive_infrastructure"
            if metrics["infrastructure_failure_count"]
            else "completed"
        ),
    }


def _run_trial(
    task: EvalTask,
    trial_index: int,
    *,
    task_run_id: int,
    provider: Any,
    provider_name: str,
    model_id: str,
    api_key: str,
    repo_root: Path,
) -> dict[str, Any]:
    document = _task_document(task, repo_root)
    before = deepcopy(document)
    events: list[dict[str, Any]] = []
    started = time.perf_counter()
    usage: dict[str, Any] = {}
    plan: dict[str, Any] | None = None
    candidate: Mapping[str, Any] = before
    reason_code: str | None = None
    verification_valid = False
    infrastructure: dict[str, Any] | None = None
    protocol_failure = False

    def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
        events.append({"event_type": event_type, "stage": stage, "payload": deepcopy(payload)})

    try:
        planned = provider.plan_general_mutation(
            GeneralMutationPlannerRequest(
                task_run_id=task_run_id,
                model_id=model_id,
                api_key=api_key,
                casefile=deepcopy(document),
                message=str(task.input["message"]),
                input_hash=_canonical_hash(task.input),
                editable_fields_by_collection=editable_fields_by_collection(),
                emit=emit,
            )
        )
        plan = planned.candidate.model_dump(mode="json")
        usage = planned.usage
        bound = bind_general_mutation_plan(
            planned.candidate,
            document,
            task_run_id=task_run_id,
            draft_id=task_run_id,
            base_revision=1,
            updated_at="2042-06-01T00:00:00Z",
        )
        simulation = VerificationEngine(profile="fast").simulate_mutation_set(
            document, bound.mutation_set
        )
        candidate = simulation.document
        reason_code = simulation.reason_code
        verification_valid = simulation.valid
    except (ValidationError, GeneralMutationBindingError) as error:
        protocol_failure = True
        reason_code = getattr(error, "reason_code", type(error).__name__)
    except Exception as error:  # Provider transport is reportable, never a capability miss.
        infrastructure = {"type": type(error).__name__, "provider": provider_name}
        reason_code = "provider_or_transport_failure"

    graders = _grade(
        task,
        before,
        candidate,
        verification_valid=verification_valid,
        verification_reason=reason_code,
    )
    hard_pass = all(item["passed"] for item in graders if item["severity"] == "hard")
    changed = _changed_paths(before, candidate)
    classification: TrialClass
    if infrastructure is not None:
        classification = "infrastructure_failure"
    elif protocol_failure:
        classification = "protocol_failure"
    elif hard_pass:
        classification = "success"
    elif not changed and "safe_block" in task.oracle["acceptable_statuses"]:
        classification = "safe_block"
    elif any(item["grader_id"] == "safety" and not item["passed"] for item in graders):
        classification = "unsafe_escape"
    else:
        classification = "capability_failure"
    return {
        "trial_id": f"{task.task_id}:{trial_index}",
        "task_id": task.task_id,
        "family": task.policy_key[0],
        "trial_index": trial_index,
        "classification": classification,
        "passed": classification == "success",
        "outcome": {
            "task_status": "succeeded" if hard_pass else "failed",
            "before_hash": _canonical_hash(before),
            "after_hash": _canonical_hash(candidate),
            "changed_paths": changed,
            "reason_code": reason_code,
        },
        "proposal": {"plan": plan, "operation_count": len(plan["operations"]) if plan else 0},
        "transcript": {"events": events},
        "graders": graders,
        "usage": usage,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "infrastructure_failure": infrastructure,
    }


def _grade(
    task: EvalTask,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    verification_valid: bool,
    verification_reason: str | None,
) -> list[dict[str, Any]]:
    required_results = [
        _assert_state(after, assertion)
        for assertion in cast(Sequence[Mapping[str, Any]], task.oracle["required_state"])
    ]
    forbidden = [str(item) for item in cast(Sequence[Any], task.oracle["forbidden_changes"])]
    changed = _changed_paths(before, after)
    forbidden_hits = [
        path
        for path in changed
        if any(path == prefix or path.startswith(prefix + "/") for prefix in forbidden)
    ]
    verification_passed = verification_valid and verification_reason != "post_document_invalid"
    return [
        {
            "grader_id": "outcome_correctness",
            "severity": "hard",
            "passed": all(item[0] for item in required_results),
            "score": sum(item[0] for item in required_results) / len(required_results),
            "evidence": {"assertions": [item[1] for item in required_results]},
        },
        {
            "grader_id": "verification",
            "severity": "hard",
            "passed": verification_passed,
            "score": 1.0 if verification_passed else 0.0,
            "evidence": {"reason_code": verification_reason},
        },
        {
            "grader_id": "safety",
            "severity": "hard",
            "passed": not forbidden_hits,
            "score": 1.0 if not forbidden_hits else 0.0,
            "evidence": {"forbidden_change_hits": forbidden_hits},
        },
        {
            "grader_id": "scope",
            "severity": "soft",
            "passed": not forbidden_hits,
            "score": 1.0 if not forbidden_hits else 0.0,
            "evidence": {"changed_paths": changed},
        },
    ]


def _assert_state(
    document: Mapping[str, Any], assertion: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    collection = str(assertion["collection"])
    where = cast(Mapping[str, Any], assertion.get("where", {}))
    count = assertion.get("count")
    matches = [
        item
        for item in cast(Sequence[Mapping[str, Any]], document.get(collection, []))
        if all(_matches(_pointer_get(item, path), value) for path, value in where.items())
    ]
    passed = len(matches) == int(count) if count is not None else bool(matches)
    return passed, {
        "collection": collection,
        "where": deepcopy(dict(where)),
        "expected_count": count,
        "actual_count": len(matches),
    }


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping) and set(expected) == {"$contains"}:
        return (
            isinstance(actual, Sequence)
            and not isinstance(actual, (str, bytes))
            and bool(expected["$contains"] in actual)
        )
    return bool(actual == expected)


def _pointer_get(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.strip("/").split("/"):
        if not part:
            continue
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if not part.isdigit() or int(part) >= len(current):
                return None
            current = current[int(part)]
        else:
            return None
    return current


def _changed_paths(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    output: list[str] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted(set(left) | set(right)):
                walk(left.get(key), right.get(key), f"{path}/{key}")
        elif isinstance(left, list) and isinstance(right, list):
            if left != right:
                output.append(path or "/")
        elif left != right:
            output.append(path or "/")

    walk(before, after, "")
    return output


def _task_document(task: EvalTask, repo_root: Path) -> dict[str, Any]:
    fixture = (repo_root / str(task.input["fixture"])).resolve()
    try:
        fixture.relative_to(repo_root.resolve())
    except ValueError as error:
        raise GeneralMutationCapabilityError("general_mutation_fixture_path_escape") from error
    return _read_object(fixture)


def _metrics(
    rows: Sequence[Mapping[str, Any]], tasks: Sequence[EvalTask], trials: int
) -> dict[str, Any]:
    evaluable = [row for row in rows if row["classification"] != "infrastructure_failure"]
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in evaluable:
        by_task[str(row["task_id"])].append(row)
        by_family[str(row["family"])].append(row)
    task_rates = [sum(row["passed"] for row in values) / len(values) for values in by_task.values()]
    family_rates = {
        key: round(sum(row["passed"] for row in values) / len(values), 6)
        for key, values in sorted(by_family.items())
    }
    classes = Counter(str(row["classification"]) for row in rows)
    return {
        "trial_count": len(rows),
        "evaluable_trial_count": len(evaluable),
        "task_macro_pass_at_1": round(sum(task_rates) / len(task_rates), 6) if task_rates else 0.0,
        "family_macro_pass_at_1": family_rates,
        f"reliable_task_rate_at_{trials}": round(
            sum(
                len(values) == trials and all(row["passed"] for row in values)
                for values in by_task.values()
            )
            / len(tasks),
            6,
        ),
        "unsafe_escape_count": classes["unsafe_escape"],
        "protocol_failure_count": classes["protocol_failure"],
        "infrastructure_failure_count": classes["infrastructure_failure"],
        "classification_counts": dict(sorted(classes.items())),
        "usage": {
            "requests": sum(
                int(cast(Mapping[str, Any], row["usage"]).get("requests", 0))
                for row in rows
            ),
            "total_tokens": sum(
                int(cast(Mapping[str, Any], row["usage"]).get("total_tokens", 0))
                for row in rows
            ),
        },
    }


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _saved_credential(
    *, provider_name: str, actor_id: int, database_url: str | None
) -> tuple[str, str]:
    """Read the user's configured provider without printing credential material."""

    from sqlalchemy import select

    from casefile.agent_runtime.credentials import decrypt_api_key
    from casefile.data_postgres.models import UserProviderSetting
    from casefile.data_postgres.session import create_database_engine, create_session_factory

    engine = create_database_engine(database_url)
    try:
        with create_session_factory(engine)() as session:
            setting = session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor_id,
                    UserProviderSetting.provider == provider_name,
                    UserProviderSetting.credential_status != "deleted",
                )
            )
            if setting is None or setting.secret_ciphertext is None or setting.secret_nonce is None:
                raise GeneralMutationCapabilityError(
                    "general_mutation_saved_credential_missing"
                )
            return (
                decrypt_api_key(
                    setting.secret_ciphertext,
                    setting.secret_nonce,
                    user_id=actor_id,
                    provider=provider_name,
                    key_version=cast(int, setting.key_version),
                ),
                setting.model_id,
            )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run General Mutation capability dev suite")
    parser.add_argument("--provider", choices=("deepseek", "openai"), default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--api-key")
    parser.add_argument("--saved-credential", action="store_true")
    parser.add_argument("--actor-id", type=int, default=1)
    parser.add_argument("--database-url")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--suite-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    env_names = (
        ("CASEFILE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY")
        if args.provider == "deepseek"
        else ("CASEFILE_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    api_key = args.api_key or next(
        (os.environ[name].strip() for name in env_names if os.environ.get(name)), None
    )
    model_id = args.model
    if args.saved_credential:
        api_key, saved_model = _saved_credential(
            provider_name=args.provider,
            actor_id=args.actor_id,
            database_url=args.database_url,
        )
        if args.model == "deepseek-v4-pro":
            model_id = saved_model
    if not api_key:
        raise SystemExit("general_mutation_capability_credential_missing")
    report = run_capability_benchmark(
        model_id=model_id,
        api_key=api_key,
        provider_name=args.provider,
        trials=args.trials,
        suite_path=args.suite_path,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()


__all__ = [
    "GeneralMutationCapabilityError",
    "load_capability_suite",
    "run_capability_benchmark",
    "validate_references",
]
