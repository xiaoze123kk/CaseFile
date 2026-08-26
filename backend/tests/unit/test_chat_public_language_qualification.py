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
    run_public_language_diagnostics,
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
            model_call_count=1,
            model_call_evidence_complete=True,
            model_binding_mismatch=False,
            unterminated_model_call_count=0,
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
    assert report["schema_version"].endswith("report-v2")
    assert report["m3_6_release_ready"] is True
    assert report["m_series_release_ready"] is False
    assert report["metrics"]["completed_trials"] == 48
    assert report["metrics"]["task_pass_rate"] == 1.0
    assert report["metrics"]["pass_at_3"] == 1.0
    assert report["metrics"]["model_call_evidence_missing_count"] == 0
    assert report["metrics"]["model_binding_mismatch_count"] == 0
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


def test_runtime_binding_and_evidence_integrity_are_mutually_classified() -> None:
    suite = load_public_language_suite(ROOT)
    rows = list(_passing_rows())
    rows[0] = replace(
        rows[0],
        task_passed=False,
        model_binding_mismatch=True,
        exact_model_observed=False,
    )
    binding = build_qualification_report(
        manifest=_manifest(), suite=suite, rows=rows, source_stable=True
    )
    rows = list(_passing_rows())
    rows[0] = replace(
        rows[0],
        task_passed=False,
        model_call_count=0,
        model_call_evidence_complete=False,
        exact_model_observed=False,
    )
    evidence = build_qualification_report(
        manifest=_manifest(), suite=suite, rows=rows, source_stable=True
    )

    assert binding["qualification_outcome"] == "failed_runtime_binding"
    assert binding["metrics"]["model_binding_mismatch_count"] == 1
    assert evidence["qualification_outcome"] == "inconclusive_evidence_integrity"
    assert evidence["metrics"]["model_call_evidence_missing_count"] == 1


def test_diagnostic_report_is_explicitly_ineligible_and_contains_no_model_prose() -> None:
    suite = load_public_language_suite(ROOT)

    class DiagnosticExecutor:
        def execute_trial(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return None

        def diagnostic_snapshot(self):  # type: ignore[no-untyped-def]
            return {
                "trial_status": "passed",
                "route": {
                    "route_source": "rule_capability",
                    "primary_intent": "edit_request",
                },
                "steps": [
                    {"component_id": "chat_finalizer", "execution_no": 1, "status": "succeeded"}
                ],
                "reason_codes": ["rule_capability:general_mutation_create"],
                "model_calls": [
                    {
                        "component_id": "chat_finalizer",
                        "status": "succeeded",
                        "provider": "deepseek",
                        "model_id": MODEL_ID,
                        "prompt_version": PROMPT_VERSION,
                        "schema_id": "casefile-chat-output-v1",
                    }
                ],
                "patch_set_count": 1,
                "task_error_code": None,
            }

    report = run_public_language_diagnostics(
        DiagnosticExecutor(),
        suite,
        task_id="public-create-event",
        trial_count=1,
    )

    assert report["qualification_eligible"] is False
    assert report["diagnostic_passed"] is True
    assert report["task_id"] == "public-create-event"
    assert report["results"][0]["patch_set_count"] == 1
    assert "answer" not in repr(report)
    assert "body" not in repr(report)


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
