"""N4.5-04 single-call Writer protocol and normalization tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from casefile.agent_runtime.prose_writer import (
    PROSE_WRITER_MODEL_ID,
    DeepSeekProseWriterProvider,
    FakeProseWriterProvider,
    ProseWriterInfrastructureError,
    build_prose_writer_request,
    execute_prose_writer,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
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
def writer_case() -> dict[str, Any]:
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
        "重启稳定后，他才看见控制台留下的人工触发痕迹，并把自动触发与人工触发并列记录。"
        "他没有把尚未证实的猜测写成结论，也没有泄露下一场才允许公开的重启日志。"
        "冷白灯落在计数器边缘，他逐项核对眼前已经发生的变化，只依据自己能够观察到的信息行动。"
        "这次检查落实了当前场景的两个行动节点，并把可复查的人工痕迹留作后续调查状态。"
        "机器恢复平稳，实验室仍保持封闭；他收起记录，没有引入任何未获授权的人物或事件。"
        "通风设备重新发出均匀的低鸣，他依次确认计数器、操作痕迹和记录页上的时间，"
        "没有让现场之外的消息替代眼前证据，也没有把两种仍在竞争的解释合并成确定答案。"
    )
    return {
        "plan": plan,
        "narrative": narrative,
        "profile": profile,
        "checklist": checklist,
        "candidate": {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": text}],
        },
    }


def _execute(case: dict[str, Any], provider: FakeProseWriterProvider | None = None) -> Any:
    return execute_prose_writer(
        provider
        or FakeProseWriterProvider(candidates=(deepcopy(case["candidate"]),)),
        scene_plan=case["plan"],
        narrative_ir=case["narrative"],
        profile=case["profile"],
        checklist=case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake-secret",
        remaining_scene_call_budget=23,
    )


def test_writer_candidate_is_normalized_with_server_owned_identity(
    writer_case: dict[str, Any],
) -> None:
    execution = _execute(writer_case)

    assert execution.status == "completed"
    assert execution.render is not None
    render = execution.render
    assert (render["stage"], render["round"], render["previous_render_hash"]) == (
        "writer",
        0,
        None,
    )
    assert (render["scene_id"], render["scene_ordinal"]) == ("scene_1", 1)
    assert render["blocks"][0]["block_id"] == "block_scene_1_001"
    assert render["character_count"] == len(writer_case["candidate"]["blocks"][0]["text"])
    assert render["source"]["component_input_hash"] == (
        execution.call.component_input_hash if execution.call else None
    )


def test_normalization_drops_only_whitespace_blocks(writer_case: dict[str, Any]) -> None:
    candidate = deepcopy(writer_case["candidate"])
    candidate["blocks"] = [
        {"text": " \n\t"},
        {"text": candidate["blocks"][0]["text"]},
        {"text": "   "},
    ]
    render = normalize_scene_render_candidate(
        candidate,
        checklist=writer_case["checklist"],
        profile=writer_case["profile"],
        component_input_hash="a" * 64,
    ).model_dump(mode="json")
    assert len(render["blocks"]) == 1
    assert render["blocks"][0]["text"] == writer_case["candidate"]["blocks"][0]["text"]


@pytest.mark.parametrize(
    "candidate",
    (
        {"schema_id": "compiler.scene-render-candidate.v1", "blocks": []},
        {"schema_id": "compiler.scene-render-candidate.v1", "blocks": [{"text": " "}]},
        {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": "合法"}],
            "stage": "accepted",
        },
        {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": "合法", "block_id": "block_scene_1_999"}],
        },
        {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": "超" * 4001}],
        },
        {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": "段落"} for _ in range(65)],
        },
    ),
)
def test_invalid_or_model_owned_fields_fail_closed(
    writer_case: dict[str, Any], candidate: dict[str, Any]
) -> None:
    execution = _execute(
        writer_case,
        FakeProseWriterProvider(candidates=(deepcopy(candidate),)),
    )
    assert execution.status == "protocol_failed"
    assert execution.render is None


def test_total_character_bounds_and_component_hash_fail_closed(
    writer_case: dict[str, Any],
) -> None:
    too_short = deepcopy(writer_case["candidate"])
    too_short["blocks"] = [{"text": "太短。"}]
    assert _execute(
        writer_case, FakeProseWriterProvider(candidates=(too_short,))
    ).error_code == "compiler_scene_render_length_out_of_bounds"

    with pytest.raises(
        CompilerContractError, match="compiler_scene_render_component_input_hash_invalid"
    ):
        normalize_scene_render_candidate(
            writer_case["candidate"],
            checklist=writer_case["checklist"],
            profile=writer_case["profile"],
            component_input_hash="not-a-hash",
        )


def test_request_is_minimal_untrusted_and_credential_free(writer_case: dict[str, Any]) -> None:
    request = build_prose_writer_request(
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="credential-canary",
        remaining_scene_call_budget=23,
    )
    serialized = json.dumps(request.input_payload, ensure_ascii=False)
    assert set(request.input_payload["untrusted_data"]) == {
        "checklist",
        "scene_context",
        "profile",
    }
    for forbidden in (
        "credential-canary",
        "Authorization",
        "gold",
        "reference_prose",
        "judge_assessment",
        "private_task_id",
    ):
        assert forbidden not in serialized
    assert request.input_payload["server_bindings"]["max_writer_calls"] == 1
    assert request.network_retries == 0


def test_authoritative_input_and_budget_drift_are_rejected_before_provider(
    writer_case: dict[str, Any],
) -> None:
    provider = FakeProseWriterProvider(candidates=(writer_case["candidate"],))
    drifted = deepcopy(writer_case["plan"])
    drifted["scenes"][0]["objective"] += "漂移"
    execution = execute_prose_writer(
        provider,
        scene_plan=drifted,
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=23,
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 0

    execution = execute_prose_writer(
        provider,
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=0,
    )
    assert execution.error_code == "prose_writer_call_budget_invalid"
    assert provider.call_count == 0


def test_request_fingerprint_is_stable_and_changes_with_frozen_input(
    writer_case: dict[str, Any],
) -> None:
    kwargs = {
        "scene_plan": writer_case["plan"],
        "narrative_ir": writer_case["narrative"],
        "profile": writer_case["profile"],
        "checklist": writer_case["checklist"],
        "previous_scene_render": None,
        "model_id": PROSE_WRITER_MODEL_ID,
        "api_key": "first",
        "remaining_scene_call_budget": 23,
    }
    first = build_prose_writer_request(**kwargs)
    second = build_prose_writer_request(**{**kwargs, "api_key": "second"})
    assert first.request_fingerprint == second.request_fingerprint

    changed = build_prose_writer_request(**{**kwargs, "remaining_scene_call_budget": 22})
    assert changed.request_fingerprint != first.request_fingerprint


def test_exact_recovery_is_reused_and_drifted_recovery_is_rejected(
    writer_case: dict[str, Any],
) -> None:
    request = build_prose_writer_request(
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=23,
    )
    original = FakeProseWriterProvider(candidates=(writer_case["candidate"],)).write_scene(
        request
    )
    provider = FakeProseWriterProvider()
    recovered = execute_prose_writer(
        provider,
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=23,
        recover_call=lambda _fingerprint: original,
    )
    assert recovered.status == "completed"
    assert recovered.call is not None and recovered.call.recovered is True
    assert provider.call_count == 0

    drifted = replace(original, component_input_hash="f" * 64)
    rejected = execute_prose_writer(
        provider,
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=23,
        recover_call=lambda _fingerprint: drifted,
    )
    assert rejected.error_code == "prose_writer_recovery_fingerprint_mismatch"


def test_infrastructure_failure_has_sanitized_single_attempt(writer_case: dict[str, Any]) -> None:
    execution = _execute(writer_case, FakeProseWriterProvider(failure_at_call=1))
    assert execution.status == "inconclusive"
    assert execution.failed_call is not None
    assert execution.failed_call.transport_attempts[0].status == "failed"
    assert "fake-secret" not in repr(execution.failed_call)


def test_deepseek_adapter_disables_hidden_retry_and_parses_json(
    writer_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = build_prose_writer_request(
        scene_plan=writer_case["plan"],
        narrative_ir=writer_case["narrative"],
        profile=writer_case["profile"],
        checklist=writer_case["checklist"],
        previous_scene_render=None,
        model_id=PROSE_WRITER_MODEL_ID,
        api_key="fake",
        remaining_scene_call_budget=23,
    )
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
                    content=json.dumps(writer_case["candidate"], ensure_ascii=False)
                )
            ),
        ),
    )
    provider = DeepSeekProseWriterProvider()
    monkeypatch.setattr(provider, "_create_completion", lambda _request: response)
    result = provider.write_scene(request)
    assert result.candidate == writer_case["candidate"]
    assert result.transport_attempts[0].attempt_index == 1
    assert result.usage["cached_tokens"] == 4

    def fail(_request: Any) -> Any:
        raise RuntimeError("credential-canary and remote response body")

    monkeypatch.setattr(provider, "_create_completion", fail)
    with pytest.raises(ProseWriterInfrastructureError) as raised:
        provider.write_scene(request)
    assert raised.value.failed_call is not None
    assert raised.value.failed_call.error_code == "prose_writer_provider_failed:RuntimeError"
    assert "credential-canary" not in repr(raised.value.failed_call)


def test_render_hash_is_stable_for_identical_execution(writer_case: dict[str, Any]) -> None:
    first = _execute(writer_case)
    second = _execute(writer_case)
    assert canonical_json_sha256(first.render) == canonical_json_sha256(second.render)
