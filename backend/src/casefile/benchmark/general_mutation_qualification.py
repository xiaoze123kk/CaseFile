"""Same-clean-revision M3.4-07f qualification orchestration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_BINDER_VERSION,
    GENERAL_MUTATION_PLAN_VERSION,
    GENERAL_MUTATION_POLICY_VERSION,
    GENERAL_MUTATION_PROMPT_VERSION,
    GENERAL_MUTATION_TRANSPORT_VERSION,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.benchmark.general_mutation_backend_release import (
    DEFAULT_SUITE as RELEASE_SUITE_PATH,
)
from casefile.benchmark.general_mutation_backend_release import (
    load_release_suite,
    run_backend_release,
)
from casefile.benchmark.general_mutation_capability import (
    DEFAULT_SUITE as CAPABILITY_SUITE_PATH,
)
from casefile.benchmark.general_mutation_capability import (
    GRADER_VERSION as CAPABILITY_GRADER_VERSION,
)
from casefile.benchmark.general_mutation_capability import (
    _saved_credential,
    load_capability_suite,
    run_capability_benchmark,
)
from casefile.benchmark.general_mutation_eval import run_qualification as run_kernel_qualification
from casefile.benchmark.general_mutation_evidence import (
    build_evidence_index,
    holdout_rerun_authorized,
    write_evidence_index,
)
from casefile.benchmark.general_mutation_holdout import load_holdout_suite
from casefile.benchmark.general_mutation_lineage import general_mutation_runtime_fingerprint
from casefile.benchmark.general_mutation_safety import (
    DEFAULT_SUITE as SAFETY_SUITE_PATH,
)
from casefile.benchmark.general_mutation_safety import (
    GRADER_VERSION as SAFETY_GRADER_VERSION,
)
from casefile.benchmark.general_mutation_safety import (
    load_safety_suite,
    run_safety_benchmark,
)
from casefile.data_postgres.session import (
    EXPECTED_DATABASE_REVISION,
    create_database_engine,
    current_database_revision,
)

QUALIFICATION_VERSION = "casefile-general-mutation-qualification-v1"
MODEL_ID = "deepseek-v4-pro"
TRIALS_PER_TASK = 5
ROOT = Path(__file__).resolve().parents[4]
GATE_POLICY = Path(__file__).with_name("policies") / "general-mutation-gate-v1.json"
HOLDOUT_DESCRIPTOR = (
    Path(__file__).with_name("policies") / "general-mutation-holdout-v1-descriptor.json"
)


class QualificationError(ValueError):
    """Stable fail-closed qualification error."""


def qualification_preflight(
    *,
    repo_root: Path,
    holdout_suite_path: Path,
    database_url: str,
    credential_database_url: str,
    actor_id: int = 1,
) -> dict[str, Any]:
    root = repo_root.resolve()
    source = _git_identity(root)
    if source["dirty"]:
        raise QualificationError("qualification_git_must_be_clean")
    database_name = _database_name(database_url)
    if not database_name.endswith("_test"):
        raise QualificationError("qualification_database_must_end_test")
    engine = create_database_engine(database_url)
    try:
        revision = current_database_revision(engine)
        with engine.connect() as connection:
            schema_rows = connection.execute(
                text(
                    "SELECT table_name,column_name,data_type "
                    "FROM information_schema.columns WHERE table_schema='public' "
                    "ORDER BY table_name,ordinal_position"
                )
            ).all()
    finally:
        engine.dispose()
    if revision != EXPECTED_DATABASE_REVISION:
        raise QualificationError("qualification_database_revision_mismatch")
    try:
        _secret, saved_model = _saved_credential(
            provider_name="deepseek",
            actor_id=actor_id,
            database_url=credential_database_url,
        )
    except Exception as error:
        raise QualificationError("qualification_saved_credential_required") from error
    if saved_model != MODEL_ID:
        raise QualificationError("qualification_saved_model_mismatch")
    capability = load_capability_suite(root, root / CAPABILITY_SUITE_PATH)
    safety = load_safety_suite(root, root / SAFETY_SUITE_PATH)
    release = load_release_suite(root, root / RELEASE_SUITE_PATH)
    try:
        holdout = load_holdout_suite(holdout_suite_path.resolve())
    except Exception as error:
        raise QualificationError("qualification_private_holdout_required") from error
    runtime = general_mutation_runtime_fingerprint(root)
    prompt = load_prompt("general_mutation_planner", GENERAL_MUTATION_PROMPT_VERSION)
    return {
        "schema_version": QUALIFICATION_VERSION,
        "source_revision": source["revision"],
        "source_branch": source["branch"],
        "source_clean": True,
        "provider": "deepseek",
        "model_id": MODEL_ID,
        "actor_id": actor_id,
        "database_name": database_name,
        "database_revision": revision,
        "database_schema_fingerprint": sha256(
            json.dumps([list(row) for row in schema_rows], separators=(",", ":")).encode()
        ).hexdigest(),
        "trials_per_task": TRIALS_PER_TASK,
        "capability_dev_task_count": len(capability.tasks),
        "holdout_task_count": len(holdout.tasks),
        "safety_task_count": len(safety.tasks),
        "backend_release_task_count": len(release.tasks),
        "formal_trial_count": (
            len(capability.tasks) * TRIALS_PER_TASK
            + len(holdout.tasks) * TRIALS_PER_TASK
            + len(safety.tasks) * TRIALS_PER_TASK
            + len(release.tasks) * 3
        ),
        "capability_suite_fingerprint": capability.fingerprint,
        "holdout_suite_fingerprint": holdout.fingerprint,
        "holdout_oracle_fingerprint": holdout.metadata["oracle_fingerprint"],
        "holdout_reference_fingerprint": holdout.metadata["reference_fingerprint"],
        "holdout_review_fingerprint": holdout.metadata["review_fingerprint"],
        "safety_suite_fingerprint": safety.fingerprint,
        "release_suite_fingerprint": release.fingerprint,
        "gate_policy_fingerprint": sha256(GATE_POLICY.read_bytes()).hexdigest(),
        "holdout_descriptor_fingerprint": sha256(HOLDOUT_DESCRIPTOR.read_bytes()).hexdigest(),
        "prompt_version": GENERAL_MUTATION_PROMPT_VERSION,
        "prompt_fingerprint": prompt.system_prompt_sha256,
        "plan_contract_version": GENERAL_MUTATION_PLAN_VERSION,
        "capability_policy_version": GENERAL_MUTATION_POLICY_VERSION,
        "binder_version": GENERAL_MUTATION_BINDER_VERSION,
        "transport_version": GENERAL_MUTATION_TRANSPORT_VERSION,
        "capability_grader_version": CAPABILITY_GRADER_VERSION,
        "safety_grader_version": SAFETY_GRADER_VERSION,
        "runtime_fingerprint": runtime,
        "rollout": {
            "general_mutation_mode": "suggest",
            "create_enabled": True,
            "delete_enabled": True,
            "no_auto_apply": True,
            "persistent_environment_mutation": False,
        },
    }


def run_formal_qualification(
    *,
    repo_root: Path,
    holdout_suite_path: Path,
    output_dir: Path,
    database_url: str,
    credential_database_url: str,
    actor_id: int = 1,
) -> dict[str, Any]:
    from casefile.benchmark.general_mutation_backend_executor import (
        PostgresBackendReleaseExecutor,
    )
    from casefile.benchmark.general_mutation_safety_executor import PostgresSafetyExecutor

    root = repo_root.resolve()
    output = output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise QualificationError("qualification_output_directory_not_empty")
    manifest = qualification_preflight(
        repo_root=root,
        holdout_suite_path=holdout_suite_path,
        database_url=database_url,
        credential_database_url=credential_database_url,
        actor_id=actor_id,
    )
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "qualification-manifest.json"
    _write_json(manifest_path, manifest)
    api_key, saved_model = _saved_credential(
        provider_name="deepseek",
        actor_id=actor_id,
        database_url=credential_database_url,
    )
    if saved_model != MODEL_ID:
        raise QualificationError("qualification_saved_model_mismatch")
    stages: list[tuple[str, int, Path]] = []
    blockers: list[str] = []
    diagnostics: list[Path] = []
    active_stage = "s0"
    try:
        s0_path = output / "s0" / "report.json"
        s0 = run_kernel_qualification()
        s0["qualification_source"] = _git_identity(root)
        s0["qualification_runtime_fingerprint"] = manifest["runtime_fingerprint"]
        _write_json(s0_path, s0)
        stages.append(("s0", 1, s0_path))
        if s0["status"] != "passed":
            blockers.append("qualification_s0_gate_failed")

        if not blockers:
            active_stage = "capability_dev"
            capability_path = output / "capability-dev" / "report.json"
            capability = run_capability_benchmark(
                repo_root=root,
                model_id=MODEL_ID,
                api_key=api_key,
                trials=5,
                suite_path=root / CAPABILITY_SUITE_PATH,
            )
            _write_json(capability_path, capability)
            stages.append(("capability_dev", 1, capability_path))
            _assert_report(capability, manifest, stage="capability_dev")
            _write_json(capability_path.parent / "gate.json", capability["gates"]["m3_4_07c"])
            if not capability["gates"]["m3_4_07c"]["passed"]:
                blockers.append("qualification_capability_dev_gate_failed")

        if not blockers:
            active_stage = "holdout"
            for attempt in (1, 2):
                holdout_path = output / "holdout" / f"attempt-{attempt:02d}" / "report.json"
                holdout = run_capability_benchmark(
                    repo_root=root,
                    model_id=MODEL_ID,
                    api_key=api_key,
                    trials=5,
                    suite_path=holdout_suite_path,
                )
                _write_json(holdout_path, holdout)
                stages.append(("holdout", attempt, holdout_path))
                _assert_report(holdout, manifest, stage="holdout")
                gate = holdout["gates"]["m3_4_holdout"]
                _write_json(holdout_path.parent / "gate.json", gate)
                if gate["passed"]:
                    break
                if attempt == 1 and holdout_rerun_authorized(holdout):
                    continue
                blockers.append("qualification_holdout_gate_failed")
                break

        if not blockers:
            active_stage = "safety_abstention"
            safety_executor = PostgresSafetyExecutor(database_url=database_url, api_key=api_key)
            try:
                safety = run_safety_benchmark(
                    executor=safety_executor,
                    repo_root=root,
                    model_id=MODEL_ID,
                    trials=5,
                    suite_path=root / SAFETY_SUITE_PATH,
                )
            finally:
                safety_executor.close()
            safety_path = output / "safety-abstention" / "report.json"
            _write_json(safety_path, safety)
            stages.append(("safety_abstention", 1, safety_path))
            _assert_report(safety, manifest, stage="safety_abstention")
            _write_json(safety_path.parent / "gate.json", safety["gates"]["m3_4_07d"])
            if not safety["gates"]["m3_4_07d"]["passed"]:
                blockers.append("qualification_safety_gate_failed")

        if not blockers:
            active_stage = "backend_release"
            release_executor = PostgresBackendReleaseExecutor(
                database_url=database_url,
                api_key=api_key,
            )
            try:
                release = run_backend_release(
                    repo_root=root,
                    database_url=database_url,
                    executor=release_executor,
                )
            finally:
                release_executor.close()
            release_path = output / "backend-release" / "report.json"
            _write_json(release_path, release)
            stages.append(("backend_release", 1, release_path))
            if release["qualification_outcome"] != "passed":
                blockers.append("qualification_backend_release_failed")
    except QualificationError as error:
        blockers.append(str(error))
        diagnostics.append(
            _write_execution_diagnostic(output, active_stage, type(error).__name__, str(error))
        )
    except Exception as error:
        reason_code = f"qualification_{active_stage}_execution_failed"
        blockers.append(reason_code)
        diagnostics.append(
            _write_execution_diagnostic(output, active_stage, type(error).__name__, reason_code)
        )

    try:
        final_source = _git_identity(root)
    except QualificationError:
        blockers.append("qualification_git_identity_unavailable")
    else:
        if (
            final_source["revision"] != manifest["source_revision"]
            or final_source["dirty"] is not False
        ):
            blockers.append("qualification_source_changed_during_run")

    evidence = build_evidence_index(
        manifest_path=manifest_path,
        stage_paths=stages,
        blocked_reason_codes=blockers,
        diagnostic_paths=diagnostics,
    )
    write_evidence_index(output / "evidence-index-v1.json", evidence)
    _write_markdown(output / "M3.4-07F-QUALIFICATION-REPORT.md", evidence, manifest)
    return evidence


def _assert_report(report: dict[str, Any], manifest: dict[str, Any], *, stage: str) -> None:
    expected_suite = {
        "capability_dev": manifest["capability_suite_fingerprint"],
        "holdout": manifest["holdout_suite_fingerprint"],
        "safety_abstention": manifest["safety_suite_fingerprint"],
    }[stage]
    if report.get("model_id") != MODEL_ID:
        raise QualificationError(f"qualification_{stage}_model_mismatch")
    if report.get("suite", {}).get("suite_fingerprint") != expected_suite:
        raise QualificationError(f"qualification_{stage}_suite_fingerprint_mismatch")
    source = report.get("git")
    if not isinstance(source, dict) or source.get("revision") != manifest["source_revision"]:
        raise QualificationError(f"qualification_{stage}_source_revision_mismatch")
    if source.get("dirty") is not False:
        raise QualificationError(f"qualification_{stage}_source_dirty")


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise QualificationError("qualification_git_identity_unavailable")
        return result.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _database_name(database_url: str) -> str:
    try:
        name = make_url(database_url).database
    except Exception as error:
        raise QualificationError("qualification_database_url_invalid") from error
    if not name:
        raise QualificationError("qualification_database_url_invalid")
    return name


def _write_execution_diagnostic(
    output: Path, stage: str, error_type: str, reason_code: str
) -> Path:
    path = output / stage.replace("_", "-") / "execution-error.json"
    _write_json(
        path,
        {
            "schema_version": "casefile-general-mutation-execution-error-v1",
            "stage": stage,
            "reason_code": reason_code,
            "error_type": error_type,
        },
    )
    return path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, evidence: dict[str, Any], manifest: dict[str, Any]) -> None:
    status = "通过" if evidence["qualified"] else "未通过"
    blockers = evidence.get("blockers", [])
    lines = [
        "# M3.4-07F 后端正式资格报告",
        "",
        f"- 结论：**{status}**",
        f"- Source revision：`{manifest['source_revision']}`",
        f"- 精确模型：`{manifest['model_id']}`",
        f"- 放行范围：`{evidence['release_scope']}`",
        "- 自动 Apply：`false`",
        "- Rollout 自动变更：`false`",
    ]
    if blockers:
        lines.extend(("", "## Blockers", "", *(f"- `{item}`" for item in blockers)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M3.4-07f formal qualification")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--holdout-suite", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--credential-database-url")
    parser.add_argument("--actor-id", type=int, default=1)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    try:
        database_url = args.database_url or os.environ.get("CASEFILE_TEST_DATABASE_URL", "")
        credential_url = args.credential_database_url or os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise QualificationError("qualification_database_url_required")
        if not credential_url:
            raise QualificationError("qualification_credential_database_url_required")
        if args.preflight:
            result = qualification_preflight(
                repo_root=args.repo_root,
                holdout_suite_path=args.holdout_suite,
                database_url=database_url,
                credential_database_url=credential_url,
                actor_id=args.actor_id,
            )
        else:
            if args.output_dir is None:
                raise QualificationError("qualification_output_directory_required")
            result = run_formal_qualification(
                repo_root=args.repo_root,
                holdout_suite_path=args.holdout_suite,
                output_dir=args.output_dir,
                database_url=database_url,
                credential_database_url=credential_url,
                actor_id=args.actor_id,
            )
    except QualificationError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.preflight and not result.get("qualified"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()


__all__ = [
    "MODEL_ID",
    "QUALIFICATION_VERSION",
    "QualificationError",
    "qualification_preflight",
    "run_formal_qualification",
]
