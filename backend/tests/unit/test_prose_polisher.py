"""N4.5 B3 Polisher and preservation rollback tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import (
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
)
from casefile.agent_runtime.prose_polish_supervisor import (
    execute_prose_polish_supervisor,
)
from casefile.agent_runtime.prose_polisher import (
    PROSE_POLISHER_MODEL_ID,
    PROSE_POLISHER_PROMPT_VERSION,
    DeepSeekProsePolisherProvider,
    FakeProsePolisherProvider,
    build_prose_polisher_request,
    execute_prose_polisher,
)
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_MODEL_ID,
    FakeProseQualityCriticProvider,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    canonical_json_sha256,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures/compiler/prose_rendering/v1"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def polish_case() -> dict[str, Any]:
    original = _load("scene_render_writer.json")
    polished = _load("scene_render_polished.json")
    return {
        "checklist": _load("checklist_scene_1.json"),
        "profile": _load("profile_v2.json"),
        "original": original,
        "consensus": _load("consensus_pass.json"),
        "findings": _load("quality_findings.json"),
        "candidate": {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [{"text": block["text"]} for block in polished["blocks"]],
        },
    }


def _findings_candidate(case: dict[str, Any]) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(case["original"])[0]["evidence_id"]
    return {
        "schema_id": "compiler.prose-quality-findings-candidate.v1",
        "findings": [
            {
                "dimension": "readability_editability",
                "severity": "low",
                "evidence_ids": [evidence_id],
                "description": "梳理句间层次并保持全部既有语义。",
            }
        ],
    }


def _pairwise(overall: str) -> dict[str, Any]:
    return {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        "overall_preference": overall,
        "dimension_preferences": [
            {"dimension": dimension, "preference": overall}
            for dimension in QUALITY_DIMENSIONS
        ],
    }


def _judge_candidate(
    case: dict[str, Any], render: dict[str, Any], *, fail: bool = False
) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(render)[0]["evidence_id"]
    fail_id = next(
        check["check_id"]
        for check in case["checklist"]["checks"]
        if check["polarity"] == "required"
    )
    assessments = []
    for check in case["checklist"]["checks"]:
        verdict = "fail" if fail and check["check_id"] == fail_id else "pass"
        evidence_required = check["polarity"] == "required" and verdict == "pass"
        assessments.append(
            {
                "check_id": check["check_id"],
                "verdict": verdict,
                "evidence_ids": [evidence_id] if evidence_required else [],
                "rationale": "正文证据支持该项。" if verdict == "pass" else "润色稿遗漏必要语义。",
            }
        )
    return {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": assessments,
    }


def _polished_render(case: dict[str, Any]) -> dict[str, Any]:
    execution = execute_prose_polisher(
        FakeProsePolisherProvider(candidates=(deepcopy(case["candidate"]),)),
        profile=case["profile"],
        checklist=case["checklist"],
        current_render=case["original"],
        semantic_consensus=case["consensus"],
        quality_findings=case["findings"],
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.render is not None
    return execution.render


def test_request_is_exact_semantic_findings_bound_and_redacted(
    polish_case: dict[str, Any],
) -> None:
    request = build_prose_polisher_request(
        profile=polish_case["profile"],
        checklist=polish_case["checklist"],
        current_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_findings=polish_case["findings"],
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="credential-canary",
    )
    assert request.prompt_version == PROSE_POLISHER_PROMPT_VERSION
    assert request.max_turns == 1
    assert request.network_retries == 0
    serialized = json.dumps(request.input_payload, ensure_ascii=False)
    assert "credential-canary" not in serialized
    assert request.input_payload["untrusted_data"]["current_render"] == polish_case[
        "original"
    ]
    assert request.input_payload["untrusted_data"]["quality_findings"] == polish_case[
        "findings"
    ]


def test_candidate_becomes_polished_with_direct_lineage(
    polish_case: dict[str, Any],
) -> None:
    render = _polished_render(polish_case)
    assert (render["stage"], render["round"]) == ("polished", 0)
    assert render["previous_render_hash"] == canonical_json_sha256(
        polish_case["original"]
    )
    assert render["selection_reason"] is None


@pytest.mark.parametrize("mutation", ("consensus", "findings", "stage"))
def test_invalid_upstream_stops_before_polisher(
    polish_case: dict[str, Any], mutation: str
) -> None:
    consensus = deepcopy(polish_case["consensus"])
    findings = deepcopy(polish_case["findings"])
    original = deepcopy(polish_case["original"])
    if mutation == "consensus":
        consensus["render_hash"] = "f" * 64
    elif mutation == "findings":
        findings["source_render_hashes"] = ["f" * 64]
    else:
        original["stage"] = "polished"
        original["previous_render_hash"] = "f" * 64
    provider = FakeProsePolisherProvider(candidates=(polish_case["candidate"],))
    execution = execute_prose_polisher(
        provider,
        profile=polish_case["profile"],
        checklist=polish_case["checklist"],
        current_render=original,
        semantic_consensus=consensus,
        quality_findings=findings,
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 0


def test_supervisor_accepts_polished_only_after_full_preservation_and_mirrored_win(
    polish_case: dict[str, Any],
) -> None:
    polished = _polished_render(polish_case)
    judge = _judge_candidate(polish_case, polished)
    quality = FakeProseQualityCriticProvider(
        findings_candidates=(_findings_candidate(polish_case),),
        pairwise_candidates=(_pairwise("b"), _pairwise("a")),
    )
    execution = execute_prose_polish_supervisor(
        quality,
        FakeProsePolisherProvider(candidates=(polish_case["candidate"],)),
        FakeProseJudgeProvider(judge_reports=(judge, judge, judge)),
        checklist=polish_case["checklist"],
        profile=polish_case["profile"],
        original_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_model_id=PROSE_QUALITY_MODEL_ID,
        generation_model_id=PROSE_COUNCIL_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_polished"
    assert execution.selection_reason == "polished_accepted"
    assert execution.accepted_render is not None
    assert execution.polish is not None and execution.polish.render is not None
    assert [block["text"] for block in execution.accepted_render["blocks"]] == [
        block["text"] for block in execution.polish.render["blocks"]
    ]
    assert execution.accepted_render["previous_render_hash"] == canonical_json_sha256(
        execution.polish.render
    )
    assert quality.call_count == 3


def test_preservation_failure_rolls_back_exact_original_without_pairwise(
    polish_case: dict[str, Any],
) -> None:
    polished = _polished_render(polish_case)
    failed = _judge_candidate(polish_case, polished, fail=True)
    quality = FakeProseQualityCriticProvider(
        findings_candidates=(_findings_candidate(polish_case),)
    )
    execution = execute_prose_polish_supervisor(
        quality,
        FakeProsePolisherProvider(candidates=(polish_case["candidate"],)),
        FakeProseJudgeProvider(judge_reports=(failed, failed, failed)),
        checklist=polish_case["checklist"],
        profile=polish_case["profile"],
        original_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_model_id=PROSE_QUALITY_MODEL_ID,
        generation_model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_original"
    assert execution.selection_reason == "polish_semantic_rollback"
    assert execution.pairwise is None
    assert quality.call_count == 1
    assert execution.accepted_render is not None
    assert [block["text"] for block in execution.accepted_render["blocks"]] == [
        block["text"] for block in polish_case["original"]["blocks"]
    ]


def test_tie_rolls_back_exact_original(polish_case: dict[str, Any]) -> None:
    polished = _polished_render(polish_case)
    passed = _judge_candidate(polish_case, polished)
    execution = execute_prose_polish_supervisor(
        FakeProseQualityCriticProvider(
            findings_candidates=(_findings_candidate(polish_case),),
            pairwise_candidates=(_pairwise("tie"), _pairwise("tie")),
        ),
        FakeProsePolisherProvider(candidates=(polish_case["candidate"],)),
        FakeProseJudgeProvider(judge_reports=(passed, passed, passed)),
        checklist=polish_case["checklist"],
        profile=polish_case["profile"],
        original_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_model_id=PROSE_QUALITY_MODEL_ID,
        generation_model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_original"
    assert execution.selection_reason == "quality_unstable"
    assert execution.accepted_render is not None
    assert [block["text"] for block in execution.accepted_render["blocks"]] == [
        block["text"] for block in polish_case["original"]["blocks"]
    ]


def test_exact_recovery_avoids_second_provider_call(polish_case: dict[str, Any]) -> None:
    request = build_prose_polisher_request(
        profile=polish_case["profile"],
        checklist=polish_case["checklist"],
        current_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_findings=polish_case["findings"],
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
    )
    saved = FakeProsePolisherProvider(
        candidates=(polish_case["candidate"],)
    ).polish_scene(request)
    provider = FakeProsePolisherProvider()
    execution = execute_prose_polisher(
        provider,
        profile=polish_case["profile"],
        checklist=polish_case["checklist"],
        current_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_findings=polish_case["findings"],
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="fake",
        recover_call=lambda fingerprint: saved
        if fingerprint == request.request_fingerprint
        else None,
    )
    assert execution.status == "completed"
    assert execution.call is not None and execution.call.recovered is True
    assert provider.call_count == 0


def test_deepseek_adapter_is_single_json_call(
    monkeypatch: pytest.MonkeyPatch, polish_case: dict[str, Any]
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(polish_case["candidate"]))
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            prompt_cache_hit_tokens=2,
        ),
    )
    provider = DeepSeekProsePolisherProvider()
    monkeypatch.setattr(provider, "_create_completion", lambda _request: response)
    execution = execute_prose_polisher(
        provider,
        profile=polish_case["profile"],
        checklist=polish_case["checklist"],
        current_render=polish_case["original"],
        semantic_consensus=polish_case["consensus"],
        quality_findings=polish_case["findings"],
        model_id=PROSE_POLISHER_MODEL_ID,
        api_key="secret-not-persisted",
    )
    assert execution.status == "completed"
    assert execution.call is not None
    assert execution.call.usage["total_tokens"] == 30
    assert len(execution.call.transport_attempts) == 1
