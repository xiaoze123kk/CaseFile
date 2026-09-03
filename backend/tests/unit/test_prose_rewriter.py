"""N4.5-05 bounded full-Scene Rewrite protocol tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_rewrite_supervisor import (
    execute_bounded_prose_rewrite,
)
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_MODEL_ID,
    DeepSeekProseRewriterProvider,
    FakeProseRewriterProvider,
    ProseRewriterInfrastructureError,
    build_prose_rewriter_request,
    execute_prose_rewriter,
)
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    normalize_scene_render_candidate,
)

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "fixtures/scene_plan_benchmark/v2/runtime_references/dependency_transfer__basic.json"
INPUT = ROOT / "fixtures/scene_plan_benchmark/v1/inputs/dependency_transfer__basic.json"
PROFILE = ROOT / "fixtures/compiler/prose_rendering/v1/profile_v2.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def rewrite_case() -> dict[str, Any]:
    plan = _load(PLAN)
    narrative = _load(INPUT)["narrative_ir"]
    profile = _load(PROFILE)
    checklist = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id="scene_1",
    )
    text = (
        "晚上八点，备用控制系统完成第七次重启，研究员守在封闭实验室的终端前。"
        "重启稳定后，他看见控制台留下的人工触发痕迹，把两种解释并列记下。"
        "冷白灯落在计数器边缘，他依次核对操作痕迹和记录页上的时间。"
        "他没有泄露下一场才允许公开的日志，也没有把尚未证实的猜测写成结论。"
        "机器恢复平稳，实验室仍保持封闭，他只依据自己能够观察到的信息行动。"
        "通风设备重新发出均匀低鸣，他收起记录，把可复查的人工痕迹留给后续调查。"
        "他再次确认计数器与终端状态，确保已经发生的变化没有被写成未来计划。"
        "现场之外的消息没有闯入这场检查，两个行动节点依照既定顺序落实。"
    )
    text += text
    candidate = {
        "schema_id": "compiler.scene-render-candidate.v1",
        "blocks": [{"text": text}],
    }
    render = normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash="a" * 64,
    ).model_dump(mode="json")
    return {
        "plan": plan,
        "narrative": narrative,
        "profile": profile,
        "checklist": checklist,
        "candidate": candidate,
        "render": render,
    }


def _judge_candidate(
    case: dict[str, Any],
    render: dict[str, Any],
    *,
    fail_check_id: str | None,
) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(render)[0]["evidence_id"]
    assessments = []
    for check in case["checklist"]["checks"]:
        failed = check["check_id"] == fail_check_id
        verdict = "fail" if failed else "pass"
        evidence_required = (check["polarity"] == "required" and verdict == "pass") or (
            check["polarity"] == "forbidden" and verdict == "fail"
        )
        assessments.append(
            {
                "check_id": check["check_id"],
                "verdict": verdict,
                "evidence_ids": [evidence_id] if evidence_required else [],
                "rationale": "正文证据支持该判断。" if not failed else "当前正文未满足该项。",
            }
        )
    return {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": assessments,
    }


def _failed_review(case: dict[str, Any]) -> Any:
    report = _judge_candidate(
        case,
        case["render"],
        fail_check_id=case["checklist"]["checks"][0]["check_id"],
    )
    return execute_semantic_council(
        FakeProseJudgeProvider(judge_reports=(report,)),
        checklist=case["checklist"],
        render=case["render"],
        profile=case["profile"],
        policy=FIDELITY_ONLY_POLICY,
        model_id=PROSE_COUNCIL_MODEL_ID,
        api_key="fake",
    )


def _request(case: dict[str, Any]) -> Any:
    review = _failed_review(case)
    return build_prose_rewriter_request(
        scene_plan=case["plan"],
        narrative_ir=case["narrative"],
        profile=case["profile"],
        checklist=case["checklist"],
        previous_scene_render=None,
        current_render=case["render"],
        consensus=review.consensus,
        judge_reports=review.judge_reports,
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="credential-canary",
        remaining_scene_call_budget=22,
    )


def test_request_contains_failed_and_preserved_semantics_without_credentials(
    rewrite_case: dict[str, Any],
) -> None:
    request = _request(rewrite_case)
    payload = request.input_payload
    untrusted = payload["untrusted_data"]
    failed_id = rewrite_case["checklist"]["checks"][0]["check_id"]

    assert request.rewrite_round == 1
    assert [item["check_id"] for item in untrusted["repair_findings"]] == [failed_id]
    assert failed_id not in {item["check_id"] for item in untrusted["preserve_checks"]}
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "credential-canary",
        "Authorization",
        "reference_prose",
        "gold",
        "private_task_id",
    ):
        assert forbidden not in serialized
    assert payload["server_bindings"]["max_rewrites_per_scene"] == 2
    length = payload["server_bindings"]["length_contract"]
    expected = rewrite_case["profile"]["prose"]["target_scene_chars"]
    assert length == {
        "policy_version": "prose-rewriter-length-contract-v2",
        "unit": "unicode_code_points_in_block_text_only",
        "min_chars": expected["min"],
        "max_chars": expected["max"],
        "target_chars": (expected["min"] + expected["max"]) // 2,
        "generation_plan": {
            "block_count": 6,
            "generation_floor_chars": 600,
            "min_chars_per_block": 100,
            "target_chars_per_block": 125,
        },
        "hard_gate": True,
    }
    assert "generation_floor_chars" in request.system_prompt
    assert "min_chars_per_block" in request.system_prompt


def test_full_candidate_becomes_rewrite_1_with_direct_hash_lineage(
    rewrite_case: dict[str, Any],
) -> None:
    review = _failed_review(rewrite_case)
    execution = execute_prose_rewriter(
        FakeProseRewriterProvider(candidates=(deepcopy(rewrite_case["candidate"]),)),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        current_render=rewrite_case["render"],
        consensus=review.consensus,
        judge_reports=review.judge_reports,
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=22,
    )
    assert execution.status == "completed"
    assert execution.render is not None
    assert (execution.render["stage"], execution.render["round"]) == ("rewrite_1", 1)
    assert execution.render["previous_render_hash"] == canonical_json_sha256(
        rewrite_case["render"]
    )


@pytest.mark.parametrize("mutation", ("passed", "wrong_render", "wrong_policy", "wrong_reports"))
def test_review_binding_drift_fails_before_provider(
    rewrite_case: dict[str, Any], mutation: str
) -> None:
    review = _failed_review(rewrite_case)
    consensus = deepcopy(review.consensus)
    reports = tuple(deepcopy(item) for item in review.judge_reports)
    if mutation == "passed":
        consensus["scene_verdict"] = "pass"
    elif mutation == "wrong_render":
        consensus["render_hash"] = "f" * 64
    elif mutation == "wrong_policy":
        consensus["council_policy_hash"] = "f" * 64
    else:
        reports = ()
    provider = FakeProseRewriterProvider(candidates=(rewrite_case["candidate"],))
    execution = execute_prose_rewriter(
        provider,
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        current_render=rewrite_case["render"],
        consensus=consensus,
        judge_reports=reports,
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=22,
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 0


def test_exact_recovery_and_fingerprint_drift(rewrite_case: dict[str, Any]) -> None:
    request = _request(rewrite_case)
    original = FakeProseRewriterProvider(
        candidates=(rewrite_case["candidate"],)
    ).rewrite_scene(request)
    review = _failed_review(rewrite_case)
    provider = FakeProseRewriterProvider()

    def execute(saved: Any) -> Any:
        return execute_prose_rewriter(
            provider,
            scene_plan=rewrite_case["plan"],
            narrative_ir=rewrite_case["narrative"],
            profile=rewrite_case["profile"],
            checklist=rewrite_case["checklist"],
            previous_scene_render=None,
            current_render=rewrite_case["render"],
            consensus=review.consensus,
            judge_reports=review.judge_reports,
            model_id=PROSE_REWRITER_MODEL_ID,
            api_key="fake",
            remaining_scene_call_budget=22,
            recover_call=lambda _fingerprint: saved,
        )

    recovered = execute(original)
    assert recovered.status == "completed"
    assert recovered.call is not None and recovered.call.recovered is True
    assert provider.call_count == 0
    rejected = execute(replace(original, input_hash="f" * 64))
    assert rejected.error_code == "prose_rewriter_recovery_fingerprint_mismatch"


def test_supervisor_stops_after_round_one_rescue(rewrite_case: dict[str, Any]) -> None:
    failed = _judge_candidate(
        rewrite_case,
        rewrite_case["render"],
        fail_check_id=rewrite_case["checklist"]["checks"][0]["check_id"],
    )
    passed = _judge_candidate(rewrite_case, rewrite_case["render"], fail_check_id=None)
    execution = execute_bounded_prose_rewrite(
        FakeProseRewriterProvider(candidates=(rewrite_case["candidate"],)),
        FakeProseJudgeProvider(judge_reports=(failed, passed)),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        initial_render=rewrite_case["render"],
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=5,
    )
    assert execution.status == "semantic_accepted"
    assert execution.rewrite_count == 1
    assert execution.model_call_count == 3
    assert [item.render["round"] for item in execution.rounds] == [0, 1]
    assert execution.final_render is not None
    assert execution.final_render["stage"] == "rewrite_1"


def test_supervisor_does_not_rewrite_an_initial_pass(rewrite_case: dict[str, Any]) -> None:
    passed = _judge_candidate(rewrite_case, rewrite_case["render"], fail_check_id=None)
    rewriter = FakeProseRewriterProvider(candidates=(rewrite_case["candidate"],))
    judge = FakeProseJudgeProvider(judge_reports=(passed,))
    execution = execute_bounded_prose_rewrite(
        rewriter,
        judge,
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        initial_render=rewrite_case["render"],
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=5,
    )
    assert execution.status == "semantic_accepted"
    assert execution.rewrite_count == 0
    assert execution.model_call_count == 1
    assert rewriter.call_count == 0
    assert judge.call_count == 1


def test_supervisor_allows_round_two_but_never_third(rewrite_case: dict[str, Any]) -> None:
    failed = _judge_candidate(
        rewrite_case,
        rewrite_case["render"],
        fail_check_id=rewrite_case["checklist"]["checks"][0]["check_id"],
    )
    rewriter = FakeProseRewriterProvider(
        candidates=(rewrite_case["candidate"], rewrite_case["candidate"])
    )
    judge = FakeProseJudgeProvider(judge_reports=(failed, failed, failed))
    execution = execute_bounded_prose_rewrite(
        rewriter,
        judge,
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        initial_render=rewrite_case["render"],
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=5,
    )
    assert execution.status == "semantic_rejected"
    assert execution.rewrite_count == 2
    assert execution.model_call_count == 5
    assert [item.render["stage"] for item in execution.rounds] == [
        "writer",
        "rewrite_1",
        "rewrite_2",
    ]
    assert rewriter.call_count == 2
    assert judge.call_count == 3
    assert execution.rounds[1].render["previous_render_hash"] == canonical_json_sha256(
        execution.rounds[0].render
    )
    assert execution.rounds[2].render["previous_render_hash"] == canonical_json_sha256(
        execution.rounds[1].render
    )


def test_supervisor_budget_and_failed_rewrite_are_audited(
    rewrite_case: dict[str, Any],
) -> None:
    failed = _judge_candidate(
        rewrite_case,
        rewrite_case["render"],
        fail_check_id=rewrite_case["checklist"]["checks"][0]["check_id"],
    )
    budget_limited = execute_bounded_prose_rewrite(
        FakeProseRewriterProvider(candidates=(rewrite_case["candidate"],)),
        FakeProseJudgeProvider(judge_reports=(failed,)),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        initial_render=rewrite_case["render"],
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=2,
    )
    assert budget_limited.status == "protocol_failed"
    assert budget_limited.error_code == "prose_rewrite_supervisor_call_budget_exhausted"
    assert budget_limited.model_call_count == 1

    failed_call = execute_bounded_prose_rewrite(
        FakeProseRewriterProvider(failure_at_call=1),
        FakeProseJudgeProvider(judge_reports=(failed,)),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        initial_render=rewrite_case["render"],
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=5,
    )
    assert failed_call.status == "inconclusive"
    assert failed_call.rewrite_count == 0
    assert failed_call.model_call_count == 2


def test_protocol_and_infrastructure_failures_are_distinct(
    rewrite_case: dict[str, Any],
) -> None:
    review = _failed_review(rewrite_case)
    invalid = execute_prose_rewriter(
        FakeProseRewriterProvider(candidates=({"schema_id": "invalid", "blocks": []},)),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        current_render=rewrite_case["render"],
        consensus=review.consensus,
        judge_reports=review.judge_reports,
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=22,
    )
    inconclusive = execute_prose_rewriter(
        FakeProseRewriterProvider(failure_at_call=1),
        scene_plan=rewrite_case["plan"],
        narrative_ir=rewrite_case["narrative"],
        profile=rewrite_case["profile"],
        checklist=rewrite_case["checklist"],
        previous_scene_render=None,
        current_render=rewrite_case["render"],
        consensus=review.consensus,
        judge_reports=review.judge_reports,
        model_id=PROSE_REWRITER_MODEL_ID,
        api_key="secret-not-audited",
        remaining_scene_call_budget=22,
    )
    assert invalid.status == "protocol_failed"
    assert inconclusive.status == "inconclusive"
    assert inconclusive.failed_call is not None
    assert "secret-not-audited" not in repr(inconclusive.failed_call)


def test_deepseek_adapter_is_single_attempt_and_sanitized(
    rewrite_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(rewrite_case)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            prompt_cache_hit_tokens=4,
        ),
        choices=(
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(rewrite_case["candidate"], ensure_ascii=False)
                )
            ),
        ),
    )
    provider = DeepSeekProseRewriterProvider()
    monkeypatch.setattr(provider, "_create_completion", lambda _request: response)
    result = provider.rewrite_scene(request)
    assert result.candidate == rewrite_case["candidate"]
    assert result.transport_attempts[0].attempt_index == 1
    assert request.network_retries == 0

    def fail(_request: Any) -> Any:
        raise RuntimeError("credential-canary and remote response")

    monkeypatch.setattr(provider, "_create_completion", fail)
    with pytest.raises(ProseRewriterInfrastructureError) as raised:
        provider.rewrite_scene(request)
    assert raised.value.failed_call is not None
    assert "credential-canary" not in repr(raised.value.failed_call)
