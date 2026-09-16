"""Bounded model-owned prose routing and selection tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import casefile.agent_runtime.prose_auto_edit as auto_edit_module
from casefile.agent_runtime.prose_auto_edit import execute_auto_edit
from casefile.agent_runtime.prose_rewriter import (
    ProseRewriterExecution,
    ProseRewriterInfrastructureError,
    ProseRewriterProviderResult,
    ProseRewriterTransportAttempt,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_prose_judge_checklist,
    canonical_json_sha256,
    normalize_scene_render_candidate,
    normalize_scene_rewrite_candidate,
)

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "fixtures/scene_plan_benchmark/v2/runtime_references/dependency_transfer__basic.json"
INPUT = ROOT / "fixtures/scene_plan_benchmark/v1/inputs/dependency_transfer__basic.json"
PROFILE = ROOT / "fixtures/compiler/prose_rendering/v1/profile_v2.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def auto_case() -> dict[str, Any]:
    plan = _load(PLAN)
    narrative = _load(INPUT)["narrative_ir"]
    profile = _load(PROFILE)
    checklist = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id="scene_1",
    )
    paragraph = (
        "晚上八点，封闭实验室里的备用控制系统完成重启。研究员守在终端前，"
        "依次核对操作痕迹和记录页上的时间，把两种仍待验证的解释并列写下。"
        "冷白灯落在计数器边缘，通风设备恢复低鸣，他没有泄露后续日志，"
        "也没有把尚未证实的猜测写成结论。"
    )
    text = paragraph * 3
    candidate = {"schema_id": "compiler.scene-render-candidate.v1", "blocks": [{"text": text}]}
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
        "render": render,
    }


class DecisionProvider:
    def __init__(self, review_action: str, *, fail: bool = False) -> None:
        self.review_action = review_action
        self.fail = fail
        self.calls = 0
        self.requests: list[Any] = []

    def auto_edit_decide(self, request: Any) -> ProseRewriterProviderResult:
        self.calls += 1
        self.requests.append(request)
        if self.fail:
            raise ProseRewriterInfrastructureError("judge_unavailable")
        stage = request.input_payload["auto_edit_stage"]
        checklist = request.input_payload["untrusted_data"]["checklist"]
        action = self.review_action
        if stage == "selection":
            candidates = request.input_payload["untrusted_data"]["candidates"]
            modified_key = next(
                key for key, value in candidates.items() if "修改稿" in value["blocks"][0]["text"]
            )
            action = f"select_{modified_key}"
        candidate = {
            "action": action,
            "findings": [
                {
                    "check_id": item["check_id"],
                    "assessment": "valid",
                    "severity": "none",
                    "reason": "符合当前场景要求。",
                }
                for item in checklist["checks"]
            ],
            "revision_plan": "加入明确的修改稿标记并保持事实不变。" if action != "retain" else "",
            "rationale": "模型完成本阶段判断。",
            "decision_stage": stage,
            "unresolved_issues": [],
        }
        raw = json.dumps(candidate, ensure_ascii=False)
        return ProseRewriterProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage={},
            latency_ms=1,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            component_input_hash=request.component_input_hash,
            output_hash=canonical_json_sha256(candidate),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
            transport_attempts=(ProseRewriterTransportAttempt(1, "completed", 1, None, True, {}),),
        )


def _run(case: dict[str, Any], provider: DecisionProvider) -> Any:
    return execute_auto_edit(
        provider,
        provider,  # type: ignore[arg-type]
        provider,  # type: ignore[arg-type]
        scene_plan=case["plan"],
        narrative_ir=case["narrative"],
        profile=case["profile"],
        checklist=case["checklist"],
        previous_scene_render=None,
        writer_render=case["render"],
        model_id="deepseek-flash",
        api_key="test",
    )


def test_auto_edit_retain_finishes_after_one_judge(auto_case: dict[str, Any]) -> None:
    provider = DecisionProvider("retain")
    result = _run(auto_case, provider)
    assert result.status == "completed"
    assert result.accepted_render["selection_reason"] == "auto_edit_original"
    assert provider.calls == 1


def test_auto_edit_rewrite_runs_once_then_model_selects_anonymous_candidate(
    auto_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = auto_case["render"]
    changed = {
        "schema_id": "compiler.scene-render-candidate.v1",
        "blocks": [{"text": original["blocks"][0]["text"] + "修改稿"}],
    }
    modified = normalize_scene_rewrite_candidate(
        changed,
        checklist=auto_case["checklist"],
        profile=auto_case["profile"],
        current_render=original,
        rewrite_round=1,
        component_input_hash="b" * 64,
    ).model_dump(mode="json")
    monkeypatch.setattr(
        auto_edit_module,
        "execute_prose_rewriter",
        lambda *_args, **_kwargs: ProseRewriterExecution("completed", modified, None),
    )
    provider = DecisionProvider("full_rewrite")
    result = _run(auto_case, provider)
    assert result.status == "completed"
    assert result.accepted_render["selection_reason"] == "auto_edit_modified"
    assert "修改稿" in result.accepted_render["blocks"][0]["text"]
    assert provider.calls == 2


def test_auto_edit_judge_failure_keeps_writer_and_marks_review_incomplete(
    auto_case: dict[str, Any],
) -> None:
    result = _run(auto_case, DecisionProvider("retain", fail=True))
    assert result.status == "review_incomplete"
    assert result.accepted_render["selection_reason"] == "auto_edit_unreviewed"
    assert result.error_code == "judge_unavailable"


def test_auto_edit_plan_execute_rewrite_preserves_checkin(auto_case: dict[str, Any]) -> None:
    from casefile.agent_runtime.plan_execute import NagLedger, derive_scene_execution_plan
    from casefile.agent_runtime.prose_rewriter import (
        PROSE_REWRITER_PLAN_PROMPT_VERSION,
        FakeProseRewriterProvider,
    )

    plan = derive_scene_execution_plan(auto_case["plan"])
    context = NagLedger().context(plan, branch="scene_prose", sequence_no=1)
    wrapped = {
        "schema_id": "compiler.scene-prose-plan-output.v1",
        "artifact": {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": auto_case["render"]["blocks"][0]["text"] + "修改稿"}],
        },
        "plan_checkin": {
            "schema_id": "casefile.plan-checkin-candidate.v1",
            "branch": "scene_prose",
            "items": [
                {
                    "goal_id": goal.goal_id,
                    "status": "fulfilled",
                    "evidence_paths": ["/blocks/0/text"],
                    "reason": "落实目标",
                }
                for goal in context.applicable_goals
            ],
        },
    }
    provider = DecisionProvider("full_rewrite")
    execution = execute_auto_edit(
        provider,
        FakeProseRewriterProvider(candidates=(wrapped,)),
        provider,
        scene_plan=auto_case["plan"],
        narrative_ir=auto_case["narrative"],
        profile=auto_case["profile"],
        checklist=auto_case["checklist"],
        previous_scene_render=None,
        writer_render=auto_case["render"],
        model_id="deepseek-flash",
        api_key="test",
        rewrite_prompt_version=PROSE_REWRITER_PLAN_PROMPT_VERSION,
        plan_context_provider=lambda: context.model_dump(mode="json"),
    )
    assert execution.status == "completed"
    assert execution.accepted_render["selection_reason"] == "auto_edit_modified"
    assert execution.modification.plan_checkin is not None


def test_auto_edit_protocol_retry_is_bounded(auto_case):
    class InvalidProvider(DecisionProvider):
        def auto_edit_decide(self, request):
            from dataclasses import replace

            return replace(super().auto_edit_decide(request), candidate={})

    provider = InvalidProvider("retain")
    result = _run(auto_case, provider)
    assert provider.calls == 2
    assert result.status == "review_incomplete"
    assert result.accepted_render["selection_reason"] == "auto_edit_unreviewed"


def test_auto_edit_v2_schema_requires_concrete_stage_issues_and_exact_check_ids(auto_case):
    provider = DecisionProvider("retain")
    result = _run(auto_case, provider)
    assert result.status == "completed"
    schema = provider.requests[0].input_payload["response_schema"]
    expected = [item["check_id"] for item in auto_case["checklist"]["checks"]]
    assert schema["properties"]["decision_stage"] == {"type": "string", "enum": ["review"]}
    assert schema["properties"]["unresolved_issues"]["type"] == "array"
    assert "anyOf" not in schema["properties"]["unresolved_issues"]
    assert schema["properties"]["findings"]["minItems"] == len(expected)
    assert schema["properties"]["findings"]["maxItems"] == len(expected)
    assert schema["$defs"]["ProseRevisionFinding"]["properties"]["check_id"]["enum"] == expected


def test_auto_edit_v1_keeps_legacy_nullable_schema(auto_case):
    provider = DecisionProvider("retain")
    result = execute_auto_edit(
        provider,
        provider,  # type: ignore[arg-type]
        provider,  # type: ignore[arg-type]
        scene_plan=auto_case["plan"],
        narrative_ir=auto_case["narrative"],
        profile=auto_case["profile"],
        checklist=auto_case["checklist"],
        previous_scene_render=None,
        writer_render=auto_case["render"],
        model_id="deepseek-flash",
        api_key="test",
        protocol_version=auto_edit_module.AUTO_EDIT_PROTOCOL_V1,
    )
    assert result.status == "completed"
    schema = provider.requests[0].input_payload["response_schema"]
    assert "anyOf" in schema["properties"]["unresolved_issues"]


def test_auto_edit_v1_keeps_legacy_generic_repair_payload(auto_case):
    from dataclasses import replace

    class LegacyInvalidProvider(DecisionProvider):
        def auto_edit_decide(self, request):
            result = super().auto_edit_decide(request)
            return replace(result, candidate={})

        def record_protocol_failure(self, _fingerprint, details):
            assert details == {"code": "prose_auto_edit_decision_invalid"}

    provider = LegacyInvalidProvider("retain")
    result = execute_auto_edit(
        provider,
        provider,  # type: ignore[arg-type]
        provider,  # type: ignore[arg-type]
        scene_plan=auto_case["plan"],
        narrative_ir=auto_case["narrative"],
        profile=auto_case["profile"],
        checklist=auto_case["checklist"],
        previous_scene_render=None,
        writer_render=auto_case["render"],
        model_id="deepseek-flash",
        api_key="test",
        protocol_version=auto_edit_module.AUTO_EDIT_PROTOCOL_V1,
    )
    assert result.status == "review_incomplete"
    repair = provider.requests[1].input_payload["protocol_repair"]
    assert set(repair) == {"attempt", "failed_response", "instruction"}


def test_auto_edit_protocol_is_selected_from_frozen_runtime():
    assert (
        auto_edit_module.auto_edit_protocol_for_runtime("prose-shadow-runtime-v13")
        == auto_edit_module.AUTO_EDIT_PROTOCOL_V1
    )
    assert (
        auto_edit_module.auto_edit_protocol_for_runtime("prose-shadow-runtime-v14")
        == auto_edit_module.AUTO_EDIT_PROTOCOL
    )
    assert (
        auto_edit_module.auto_edit_protocol_for_runtime("prose-shadow-runtime-v15")
        == auto_edit_module.AUTO_EDIT_PROTOCOL
    )
    with pytest.raises(CompilerContractError, match="runtime_version_unsupported"):
        auto_edit_module.auto_edit_protocol_for_runtime("prose-shadow-runtime-v999")


@pytest.mark.parametrize(
    "mutate,error_code",
    [
        (
            lambda candidate: {**candidate, "unresolved_issues": None},
            "unresolved_issues_must_be_array",
        ),
        (
            lambda candidate: {
                **candidate,
                "findings": [
                    *candidate["findings"],
                    {
                        "check_id": "invented_check_id",
                        "assessment": "valid",
                        "severity": "none",
                        "reason": "不应出现的检查项。",
                    },
                ],
            },
            "unexpected_check_ids",
        ),
        (
            lambda candidate: {
                **candidate,
                "findings": [
                    {**candidate["findings"][0], "check_id": {"bad": "shape"}},
                    *candidate["findings"][1:],
                ],
            },
            "finding_check_id_must_be_string",
        ),
    ],
)
def test_auto_edit_protocol_repair_receives_specific_validation_errors(
    auto_case, mutate, error_code
):
    from dataclasses import replace

    class RepairProvider(DecisionProvider):
        def __init__(self):
            super().__init__("retain")
            self.failures: list[dict[str, Any]] = []

        def auto_edit_decide(self, request):
            result = super().auto_edit_decide(request)
            return (
                replace(result, candidate=mutate(result.candidate)) if self.calls == 1 else result
            )

        def record_protocol_failure(self, _fingerprint, details):
            self.failures.append(details)

    provider = RepairProvider()
    result = _run(auto_case, provider)
    assert result.status == "completed"
    assert provider.calls == 2
    repair = provider.requests[1].input_payload["protocol_repair"]
    assert error_code in {item["code"] for item in repair["validation_errors"]}
    assert repair["expected_check_ids"] == [
        item["check_id"] for item in auto_case["checklist"]["checks"]
    ]
    assert provider.failures[0]["validation_errors"] == repair["validation_errors"]


def test_binding_corruption_cannot_be_a_successful_fallback(auto_case):
    class CorruptProvider(DecisionProvider):
        def auto_edit_decide(self, request):
            from dataclasses import replace

            return replace(super().auto_edit_decide(request), input_hash="0" * 64)

    with pytest.raises(CompilerContractError, match="binding_invalid"):
        _run(auto_case, CorruptProvider("retain"))


def test_soft_length_does_not_trigger_generation_repair(auto_case):
    from casefile.agent_runtime.prose_generation import generation_issue
    from casefile.agent_runtime.prose_writer import build_prose_writer_request

    request = build_prose_writer_request(
        scene_plan=auto_case["plan"],
        narrative_ir=auto_case["narrative"],
        profile=auto_case["profile"],
        checklist=auto_case["checklist"],
        previous_scene_render=None,
        model_id="deepseek-flash",
        api_key="test",
        remaining_scene_call_budget=8,
        soft_target_length=True,
    )
    candidate = {"schema_id": "compiler.scene-render-candidate.v1", "blocks": [{"text": "短稿。"}]}
    assert generation_issue(candidate, request, "prose_writer") is None
    render = normalize_scene_render_candidate(
        candidate,
        checklist=auto_case["checklist"],
        profile=auto_case["profile"],
        component_input_hash=request.component_input_hash,
        enforce_target_length=False,
    )
    assert render.character_count == 3


def test_legacy_runtime_keeps_frozen_fingerprint():
    from casefile.agent_runtime.prose_runtime import prose_runtime_binding

    assert (
        canonical_json_sha256(prose_runtime_binding(2, runtime_version="prose-shadow-runtime-v12"))
        == "dc419c343580dbb895467366a458d2131c0efbc2ef653226ad37237a625956d6"
    )
    assert (
        canonical_json_sha256(
            prose_runtime_binding(
                prose_mode="auto_edit", runtime_version="prose-shadow-runtime-v13"
            )
        )
        == "d8be093215c98f4095f6c5aaffaa89cb90479565502487fdb720b9812c244e38"
    )
    assert (
        canonical_json_sha256(
            prose_runtime_binding(
                prose_mode="full_polish", runtime_version="prose-shadow-runtime-v13"
            )
        )
        == "3e7c01ea16cf4d4078ac7c0b215d5f79b86e22c5e62f7715e710fe31814f1616"
    )
