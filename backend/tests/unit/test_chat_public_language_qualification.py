from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from casefile.benchmark.chat_public_language_qualification import (
    MODEL_ID,
    PROMPT_VERSION,
    PublicLanguageTrialEvidence,
    build_qualification_report,
    inspect_public_payload,
    load_public_language_suite,
    run_public_language_trials,
)

ROOT = Path(__file__).resolve().parents[3]


def _manifest() -> dict[str, object]:
    return {
        "source": {"revision": "a" * 40, "branch": "codex/M3.6", "dirty": False},
        "provider": "deepseek",
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "runtime_fingerprint": "b" * 64,
    }


def _passing_rows() -> tuple[PublicLanguageTrialEvidence, ...]:
    suite = load_public_language_suite(ROOT)
    return tuple(
        PublicLanguageTrialEvidence(
            task_id=task.task_id,
            category=task.category,
            trial_no=trial_no,
            completed=True,
            task_passed=True,
            public_contract_valid=True,
            internal_leak=False,
            sensitive_leak=False,
            unsafe_patch=False,
            false_block=False,
            patch_present=task.patch_expectation != "none",
            no_auto_apply=True,
            exact_model_observed=True,
            exact_prompt_observed=True,
            run_status="succeeded",
            response_kind=task.response_kinds[0],
        )
        for task in suite.tasks
        for trial_no in range(1, 4)
    )


def test_frozen_suite_has_exact_coverage_and_runtime_binding() -> None:
    suite = load_public_language_suite(ROOT)

    assert len(suite.tasks) == 16
    assert suite.trials_per_task == 3
    assert suite.model_id == MODEL_ID
    assert suite.prompt_version == PROMPT_VERSION
    assert len(suite.fingerprint) == 64
    assert {task.category for task in suite.tasks} == {
        "normal_question",
        "analysis",
        "logic_audit",
        "update",
        "create",
        "delete",
        "relationship",
        "internal_inducement",
        "normal_neighbor",
    }


def test_public_payload_probe_allows_opaque_handles_but_rejects_rendered_internals() -> None:
    safe_rules, safe_sensitive = inspect_public_payload(
        {
            "run_id": 7,
            "patch_id": 8,
            "change_id": 9,
            "target": {"target_id": "ent_researcher", "name": "林研究员"},
            "body": "林研究员正在调查第七次重启。",
        },
        sensitive_values=("secret-canary",),
    )
    leaked_rules, leaked_sensitive = inspect_public_payload(
        {
            "body": "TaskRun ent_researcher 的值是 secret-canary。",
            "field_path": "/description",
        },
        sensitive_values=("secret-canary",),
    )

    assert safe_rules == ()
    assert safe_sensitive is False
    assert set(leaked_rules) == {
        "engineering_term",
        "forbidden_public_key",
        "internal_id",
    }
    assert leaked_sensitive is True
    assert "secret-canary" not in repr(leaked_rules)


def test_all_green_report_passes_m36_but_not_whole_m_series() -> None:
    suite = load_public_language_suite(ROOT)
    report = build_qualification_report(
        manifest=_manifest(),
        suite=suite,
        rows=_passing_rows(),
        source_stable=True,
    )

    assert report["qualification_outcome"] == "passed"
    assert report["m3_6_release_ready"] is True
    assert report["m_series_release_ready"] is False
    assert report["metrics"]["completed_trials"] == 48
    assert report["metrics"]["task_pass_rate"] == 1.0
    assert report["metrics"]["pass_at_3"] == 1.0
    assert all(report["gates"].values())


def test_infrastructure_and_public_boundary_failures_remain_distinct() -> None:
    suite = load_public_language_suite(ROOT)
    rows = list(_passing_rows())
    rows[0] = replace(
        rows[0],
        completed=False,
        task_passed=False,
        infrastructure_failure="provider_transport:provider_timeout",
    )
    infrastructure = build_qualification_report(
        manifest=_manifest(), suite=suite, rows=rows, source_stable=True
    )
    rows = list(_passing_rows())
    rows[0] = replace(rows[0], task_passed=False, internal_leak=True)
    boundary = build_qualification_report(
        manifest=_manifest(), suite=suite, rows=rows, source_stable=True
    )

    assert infrastructure["qualification_outcome"] == "inconclusive_infrastructure"
    assert boundary["qualification_outcome"] == "failed_public_boundary"


def test_runner_records_exception_and_continues_every_frozen_trial() -> None:
    suite = load_public_language_suite(ROOT)

    class BrokenExecutor:
        database_schema_fingerprint = "c" * 64

        def execute_trial(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("must not be retained")

    rows = run_public_language_trials(BrokenExecutor(), suite)

    assert len(rows) == 48
    assert all(row.completed is False for row in rows)
    assert {row.infrastructure_failure for row in rows} == {"executor_exception:RuntimeError"}
    assert "must not be retained" not in repr(rows)
