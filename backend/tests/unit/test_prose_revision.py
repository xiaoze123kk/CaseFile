"""Editorial authority, strict evaluation, and bounded no-progress regressions."""

from copy import deepcopy

import pytest
from test_prose_rewriter import _judge_candidate, _request
from test_prose_rewriter import rewrite_case as source_rewrite_case

from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider
from casefile.agent_runtime.prose_revision import execute_revision_decision
from casefile.agent_runtime.prose_rewrite_supervisor import execute_bounded_prose_rewrite
from casefile.agent_runtime.prose_rewriter import FakeProseRewriterProvider


@pytest.fixture
def rewrite_case():
    return source_rewrite_case.__wrapped__()


def _decision(case, action="retain", severity="nonfatal"):
    return {
        "action": action,
        "findings": [
            {
                "check_id": case["checklist"]["checks"][0]["check_id"],
                "assessment": "literary_interpretation",
                "severity": severity,
                "reason": "上下文允许这个文学解释。",
            }
        ],
        "revision_plan": "通过动作表达变化，保留既有事实。",
        "rationale": "局部表达不妨碍故事继续。",
    }


@pytest.mark.parametrize(
    "mode,status", [("product", "product_accepted"), ("strict", "semantic_rejected")]
)
def test_editorial_retention_does_not_turn_strict_failure_into_pass(rewrite_case, mode, status):
    case = rewrite_case
    judge = FakeProseJudgeProvider(
        judge_reports=(
            _judge_candidate(
                case, case["render"], fail_check_id=case["checklist"]["checks"][0]["check_id"]
            ),
        )
    )
    provider = FakeProseRewriterProvider(candidates=(_decision(case),))
    result = _run(case, provider, judge, mode)
    assert result.status == status
    assert result.model_call_count == 2
    assert result.remaining_scene_call_budget == 10
    assert result.rounds[0].council.consensus["scene_verdict"] == "fail"
    assert result.final_render == case["render"]


def _run(case, provider, judge, mode="product", observe=lambda *_: None):
    return execute_bounded_prose_rewrite(
        provider,
        judge,
        scene_plan=case["plan"],
        narrative_ir=case["narrative"],
        profile=case["profile"],
        checklist=case["checklist"],
        previous_scene_render=None,
        initial_render=case["render"],
        model_id="deepseek-v4-pro",
        api_key="fake",
        remaining_scene_call_budget=12,
        llm_revision=True,
        delivery_mode=mode,
        observe=observe,
    )


@pytest.mark.parametrize("mutation", ["fatal_retention", "missing_check", "exhausted_rewrite"])
def test_invalid_editorial_decisions_do_not_authorize_delivery(rewrite_case, mutation):
    decision = _decision(rewrite_case)
    if mutation == "fatal_retention":
        decision["findings"][0]["severity"] = "fatal"
    elif mutation == "missing_check":
        decision["findings"][0]["check_id"] = "unknown"
    else:
        decision["action"] = "full_rewrite"
    result = execute_revision_decision(
        FakeProseRewriterProvider(candidates=(decision,)),
        _request(rewrite_case),
        exhausted=True,
    )
    assert result.status == "protocol_failed"
    assert result.report is None


def test_no_progress_stops_rewrite_and_asks_for_final_literary_assessment(rewrite_case):
    case = rewrite_case
    judge = FakeProseJudgeProvider(
        judge_reports=(
            _judge_candidate(
                case, case["render"], fail_check_id=case["checklist"]["checks"][0]["check_id"]
            ),
        )
    )
    provider = FakeProseRewriterProvider(
        candidates=(
            _decision(case, "local_revision"),
            deepcopy(case["candidate"]),
            _decision(case),
        )
    )
    provider.allow_generation_repair = True
    failures = []
    provider.record_generation_failure = lambda fp, issue: failures.append(issue)
    observed = []
    result = _run(case, provider, judge, observe=lambda name, ex: observed.append((name, ex)))
    assert result.status == "product_accepted"
    assert result.model_call_count == 4
    assert result.remaining_scene_call_budget == 8
    assert provider.call_count == 3  # decision, rewrite, final decision; no identical retry
    assert failures[0]["code"] == "prose_generation_no_progress"
    final = observed[-1][1]
    assert final.report["repair_budget_exhausted"] is True
    assert final.report["render_hash"] == observed[1][1].report["render_hash"]


def test_final_fatal_assessment_is_semantic_rejection(rewrite_case):
    case = rewrite_case
    judge = FakeProseJudgeProvider(
        judge_reports=(
            _judge_candidate(
                case, case["render"], fail_check_id=case["checklist"]["checks"][0]["check_id"]
            ),
        )
    )
    provider = FakeProseRewriterProvider(candidates=(_decision(case, "stop", "fatal"),))
    result = _run(case, provider, judge)
    assert result.status == "semantic_rejected"
    assert result.error_code == "prose_semantic_repair_exhausted"
