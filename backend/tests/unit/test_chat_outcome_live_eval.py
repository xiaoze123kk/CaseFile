"""Unit tests for the M2 live outcome runner's aggregation and zero-cost path."""

from __future__ import annotations

from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    ToolMetrics,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.benchmark.chat_outcome_eval import build_outcome_tasks
from casefile.benchmark.chat_outcome_live_eval import run_live_chat_outcome_eval


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
    assert report.pass_all == 1.0
    assert report.safety_pass_at_k == 1.0
    assert report.safety_pass_all == 1.0
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
    assert report.pass_all == 0.0
    assert report.safety_pass_at_k == 1.0
    assert report.safety_pass_all == 0.0
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
