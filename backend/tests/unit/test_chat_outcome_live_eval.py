"""Unit tests for the M2 live outcome runner's aggregation and zero-cost path."""

from __future__ import annotations

from dataclasses import replace

import casefile.benchmark.chat_outcome_live_eval as live_eval
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    ToolMetrics,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
)
from casefile.benchmark.chat_outcome_live_eval import (
    _apply_dangerous_confusion_verdict,
    run_live_chat_outcome_eval,
    suite_fingerprint,
)


class ReferenceEchoProvider(FakeProvider):
    """Return the Task's Reference Solution for one deterministic live trial."""

    def __init__(self, candidate: CaseFileChatCandidate) -> None:
        self.candidate = candidate

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        return CaseFileChatResult(
            candidate=self.candidate,
            usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1),
        )


class FlakyOnceReferenceProvider(ReferenceEchoProvider):
    """Fail the first Trial, then return the Reference Solution."""

    def __init__(self, candidate: CaseFileChatCandidate) -> None:
        super().__init__(candidate)
        self.attempts = 0

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("transient provider failure")
        return super().chat(request)


class FourthRetryReferenceProvider(ReferenceEchoProvider):
    """Fail four independent Trials and pass only the fifth."""

    def __init__(self, candidate: CaseFileChatCandidate) -> None:
        super().__init__(candidate)
        self.calls = 0

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        self.calls += 1
        if self.calls < 5:
            raise RuntimeError("transient provider failure")
        return super().chat(request)


class ProtocolTelemetryProvider(ReferenceEchoProvider):
    """Emit structured-output diagnostics exactly as a live provider would."""

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        request.emit(
            "model.output_protocol_selected",
            "finalizing",
            {"protocol": "strict_tool", "attempt_no": 1},
        )
        request.emit(
            "model.output_protocol_fallback",
            "finalizing",
            {
                "from": "strict_tool",
                "to": "json_object",
                "reason_code": "strict_schema_violation",
            },
        )
        request.emit(
            "agent.model_call.failed",
            "finalizing",
            {
                "failure_layer": "pydantic",
                "attempt_no": 1,
                "issues": [{"path": "/audit_findings/0", "message": "invalid"}],
            },
        )
        return super().chat(request)


def test_reference_live_trials_all_pass() -> None:
    task = build_outcome_tasks()[0]
    report = run_live_chat_outcome_eval(
        lambda: ReferenceEchoProvider(task.reference_candidate),
        provider_name="fake",
        model_id="reference-echo",
        api_key="fake",
        tasks=(task,),
        trials=3,
    )
    assert report.trial_count == 3
    assert report.pass_at_1 == 1.0
    assert report.pass_at_k == 1.0
    assert report.pass_k == 3
    assert report.pass_all == 1.0
    assert report.safety_pass_at_k == 1.0
    assert report.safety_pass_all == 1.0
    assert report.unsafe_trial_rate == 0.0
    assert report.task_pass_rate == 1.0
    assert report.reference_precision == 1.0
    assert report.reference_recall == 1.0
    assert report.final_reference_precision == 1.0
    assert report.final_reference_recall == 1.0
    assert report.status == "passed"
    assert report.input_tokens == 9
    assert report.output_tokens == 12


def test_pass_at_k_gates_final_success_after_a_transient_failure() -> None:
    task = build_outcome_tasks()[0]
    provider = FlakyOnceReferenceProvider(task.reference_candidate)
    report = run_live_chat_outcome_eval(
        lambda: provider,
        provider_name="fake",
        model_id="reference-echo-flaky",
        api_key="fake",
        tasks=(task,),
        trials=3,
    )
    assert report.pass_at_1 == 0.0
    assert report.pass_at_k == 1.0
    assert report.pass_k == 3
    assert report.pass_all == 0.0
    assert report.safety_pass_at_k == 1.0
    assert report.safety_pass_all == 1.0
    assert report.final_reference_precision == 1.0
    assert report.final_reference_recall == 1.0
    assert report.status == "passed"


def test_fake_provider_live_smoke_runs_without_network() -> None:
    task = build_outcome_tasks()[0]
    report = run_live_chat_outcome_eval(
        lambda: FakeProvider(),
        provider_name="fake",
        model_id="fake-baseline",
        api_key="fake",
        tasks=(task,),
        trials=2,
    )
    assert report.trial_count == 2
    assert len(report.rows) == 2
    assert report.dangerous_confusion_recall == 1.0
    assert isinstance(report.status, str)


def test_release_pass_at_k_uses_all_five_trials() -> None:
    task = build_outcome_tasks()[0]
    provider = FourthRetryReferenceProvider(task.reference_candidate)
    report = run_live_chat_outcome_eval(
        lambda: provider,
        provider_name="fake",
        model_id="reference-echo-fifth",
        api_key="fake",
        tasks=(task,),
        trials=5,
    )

    assert report.pass_k == 5
    assert report.pass_at_1 == 0.0
    assert report.pass_at_k == 1.0
    assert report.safety_pass_at_k == 1.0
    assert report.unsafe_trial_rate == 0.0


def test_one_unsafe_trial_fails_the_all_of_five_safety_gate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    task = build_outcome_tasks()[0]
    real_grade = live_eval.grade_chat_outcome
    calls = 0

    def grade_once_unsafe(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        verdict = real_grade(*args, **kwargs)
        return replace(verdict, safety_passed=False, passed=False) if calls == 3 else verdict

    monkeypatch.setattr(live_eval, "grade_chat_outcome", grade_once_unsafe)
    report = run_live_chat_outcome_eval(
        lambda: ReferenceEchoProvider(task.reference_candidate),
        provider_name="fake",
        model_id="one-unsafe-trial",
        api_key="fake",
        tasks=(task,),
        trials=5,
    )

    assert report.pass_at_k == 1.0
    assert report.safety_pass_at_k == 0.0
    assert report.unsafe_trial_rate == 0.2
    assert report.gates["safety_pass_all_k_1.0"] is False
    assert report.gates["unsafe_trial_rate_0"] is False
    assert report.status == "failed"


def test_live_runner_can_freeze_v13_for_a_trial() -> None:
    task = build_outcome_tasks()[0]
    report = run_live_chat_outcome_eval(
        lambda: ReferenceEchoProvider(task.reference_candidate),
        provider_name="fake",
        model_id="reference-echo",
        api_key="fake",
        tasks=(task,),
        trials=1,
        prompt_version="casefile-chat-v13",
    )

    assert report.prompt_versions == ("casefile-chat-v13",)
    assert report.suite_fingerprint


def test_dangerous_confusion_is_a_trial_safety_failure_only_when_actionable() -> None:
    task = next(
        task
        for task in build_outcome_tasks()
        if task.task_id == "adversarial-danger-direct-draft"
    )
    verdict = ChatOutcomeTrialVerdict(task_id=task.task_id)

    unsafe, danger_miss = _apply_dangerous_confusion_verdict(
        task,
        verdict,
        actual_intent="edit_request",
        allow_suggestions=True,
    )
    assert danger_miss is True
    assert unsafe.safety_passed is False
    assert unsafe.passed is False
    assert "dangerous_confusion" in unsafe.failures

    fail_closed, danger_miss = _apply_dangerous_confusion_verdict(
        task,
        verdict,
        actual_intent="edit_request",
        allow_suggestions=False,
    )
    assert danger_miss is False
    assert fail_closed == verdict


def test_danger_miss_updates_trial_and_aggregate_safety(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    task = next(
        task
        for task in build_outcome_tasks()
        if task.task_id == "adversarial-danger-direct-draft"
    )

    def force_danger(task, verdict, **_kwargs):  # type: ignore[no-untyped-def]
        return (
            replace(
                verdict,
                failures=(*verdict.failures, "dangerous_confusion"),
                safety_passed=False,
                passed=False,
            ),
            True,
        )

    monkeypatch.setattr(live_eval, "_apply_dangerous_confusion_verdict", force_danger)
    report = run_live_chat_outcome_eval(
        lambda: ReferenceEchoProvider(task.reference_candidate),
        provider_name="fake",
        model_id="forced-danger",
        api_key="fake",
        tasks=(task,),
        trials=1,
    )

    assert report.rows[0]["danger_miss"] is True
    assert report.rows[0]["safety_passed"] is False
    assert report.dangerous_confusion_recall == 0.0
    assert report.safety_pass_at_k == 0.0
    assert report.unsafe_trial_rate == 1.0
    assert report.status == "failed"


def test_suite_fingerprint_covers_frozen_contract_and_grader_version(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    task = build_outcome_tasks()[0]

    def fingerprint(candidate=task):  # type: ignore[no-untyped-def]
        return suite_fingerprint(
            [candidate],
            trials=5,
            provider_name="fake",
            model_id="fake",
        )

    baseline = fingerprint()
    assert fingerprint(replace(task, message=f"{task.message} ")) != baseline
    assert fingerprint(
        replace(
            task,
            expectations=replace(task.expectations, expected_answer_markers=("Lucy",)),
        )
    ) != baseline
    assert fingerprint(
        replace(
            task,
            reference_candidate=task.reference_candidate.model_copy(
                update={"answer": "变更"}
            ),
        )
    ) != baseline
    monkeypatch.setattr(live_eval, "CHAT_OUTCOME_GRADER_VERSION", "test-grader-version")
    assert fingerprint() != baseline


def test_live_report_keeps_protocol_and_validation_history() -> None:
    task = build_outcome_tasks()[0]
    report = run_live_chat_outcome_eval(
        lambda: ProtocolTelemetryProvider(task.reference_candidate),
        provider_name="fake",
        model_id="telemetry",
        api_key="fake",
        tasks=(task,),
        trials=1,
    )

    row = report.rows[0]
    assert row["output_protocol_history"] == [
        {
            "attempt": 1,
            "from": "strict_tool",
            "to": "json_object",
            "reason_code": "strict_schema_violation",
            "stage": "finalizing",
        }
    ]
    assert row["output_validation_history"] == [
        {
            "attempt": 1,
            "stage": "finalizing",
            "issues": [{"path": "/audit_findings/0", "message": "invalid"}],
        }
    ]
