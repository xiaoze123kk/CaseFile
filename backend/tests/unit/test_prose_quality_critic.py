"""N4.5-06 Quality Critic findings and mirrored pairwise tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import build_server_evidence_catalog
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    DeepSeekProseQualityCriticProvider,
    FakeProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
    execute_quality_findings,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    canonical_json_sha256,
    resolve_mirrored_quality,
    validate_quality_findings_report,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures/compiler/prose_rendering/v1"


@pytest.fixture(scope="module")
def quality_case() -> dict[str, Any]:
    values = {
        name: json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        for name, filename in {
            "checklist": "checklist_scene_1.json",
            "profile": "profile_v2.json",
            "original": "scene_render_writer.json",
            "polished": "scene_render_polished.json",
            "consensus": "consensus_pass.json",
        }.items()
    }
    preservation = deepcopy(values["consensus"])
    preservation["render_hash"] = canonical_json_sha256(values["polished"])
    values["preservation"] = preservation
    return values


def _findings_candidate(case: dict[str, Any]) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(case["original"])[0]["evidence_id"]
    return {
        "schema_id": "compiler.prose-quality-findings-candidate.v1",
        "findings": [
            {
                "dimension": "readability_editability",
                "severity": "low",
                "evidence_ids": [evidence_id],
                "description": "开场信息密度较高，可进一步梳理句间层次。",
            }
        ],
    }


def _pairwise_candidate(
    overall: str, *, dimension_preference: str | None = None
) -> dict[str, Any]:
    return {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        "overall_preference": overall,
        "dimension_preferences": [
            {
                "dimension": dimension,
                "preference": dimension_preference or overall,
            }
            for dimension in QUALITY_DIMENSIONS
        ],
    }


def test_findings_require_semantic_acceptance_and_exact_server_evidence(
    quality_case: dict[str, Any],
) -> None:
    execution = execute_quality_findings(
        FakeProseQualityCriticProvider(
            findings_candidates=(_findings_candidate(quality_case),)
        ),
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )

    assert execution.status == "completed"
    assert execution.report is not None
    assert execution.report["report_kind"] == "findings"
    assert execution.report["position_mapping"] is None
    assert execution.report["overall_preference"] is None
    evidence = execution.report["findings"][0]["evidence"][0]
    block = quality_case["original"]["blocks"][0]["text"]
    assert evidence["text"] == block[evidence["start_char"] : evidence["end_char"]]
    assert execution.call is not None
    assert execution.call.prompt_version == PROSE_QUALITY_FINDINGS_PROMPT_VERSION


def test_findings_fail_closed_for_nonaccepted_semantics_and_unknown_evidence(
    quality_case: dict[str, Any],
) -> None:
    failed_consensus = deepcopy(quality_case["consensus"])
    failed_consensus["scene_verdict"] = "fail"
    failed_consensus["failed_check_ids"] = [
        failed_consensus["checks"][0]["check_id"]
    ]
    failed_consensus["checks"][0]["final_verdict"] = "fail"
    provider = FakeProseQualityCriticProvider(
        findings_candidates=(_findings_candidate(quality_case),)
    )
    execution = execute_quality_findings(
        provider,
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=failed_consensus,
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 0

    candidate = _findings_candidate(quality_case)
    candidate["findings"][0]["evidence_ids"] = ["evidence_999"]
    execution = execute_quality_findings(
        FakeProseQualityCriticProvider(findings_candidates=(candidate,)),
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"
    assert execution.error_code == "prose_quality_evidence_catalog_mismatch"


def test_mirrored_pairwise_accepts_only_two_polished_wins_without_regression(
    quality_case: dict[str, Any],
) -> None:
    provider = FakeProseQualityCriticProvider(
        pairwise_candidates=(
            _pairwise_candidate("b"),
            _pairwise_candidate("a"),
        )
    )
    execution = execute_mirrored_pairwise_quality(
        provider,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )

    assert execution.status == "completed"
    assert provider.call_count == 2
    assert execution.decision is not None
    assert execution.decision.accept_polished is True
    assert execution.decision.selection_reason == "polished_accepted"
    assert [call.prompt_version for call in execution.calls] == [
        PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
        PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    ]
    first_payload = execution.calls[0].request_payload
    second_payload = execution.calls[1].request_payload
    for payload in (first_payload, second_payload):
        serialized = json.dumps(payload, ensure_ascii=False)
        assert '"original"' not in serialized
        assert '"polished"' not in serialized
        assert '"stage"' not in serialized
        assert '"round"' not in serialized
        assert '"selection_reason"' not in serialized
    assert first_payload["untrusted_data"]["a"] == second_payload["untrusted_data"]["b"]
    assert first_payload["untrusted_data"]["b"] == second_payload["untrusted_data"]["a"]


@pytest.mark.parametrize(
    ("first", "second", "expected_reason"),
    (
        ("a", "b", "quality_rollback"),
        ("b", "b", "quality_unstable"),
        ("tie", "tie", "quality_unstable"),
    ),
)
def test_mirrored_pairwise_rolls_back_nonstable_results(
    quality_case: dict[str, Any], first: str, second: str, expected_reason: str
) -> None:
    execution = execute_mirrored_pairwise_quality(
        FakeProseQualityCriticProvider(
            pairwise_candidates=(
                _pairwise_candidate(first),
                _pairwise_candidate(second),
            )
        ),
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "completed"
    assert execution.decision is not None
    assert execution.decision.accept_polished is False
    assert execution.decision.selection_reason == expected_reason


def test_dimension_regression_rolls_back_even_when_overall_prefers_polished(
    quality_case: dict[str, Any],
) -> None:
    first = _pairwise_candidate("b")
    second = _pairwise_candidate("a")
    first["dimension_preferences"][0]["preference"] = "a"
    execution = execute_mirrored_pairwise_quality(
        FakeProseQualityCriticProvider(pairwise_candidates=(first, second)),
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "completed"
    assert execution.decision is not None
    assert execution.decision.accept_polished is False
    assert execution.decision.selection_reason == "quality_rollback"


def test_pairwise_dimension_coverage_and_second_call_failure_fail_closed(
    quality_case: dict[str, Any],
) -> None:
    malformed = _pairwise_candidate("b")
    malformed["dimension_preferences"] = list(
        reversed(malformed["dimension_preferences"])
    )
    provider = FakeProseQualityCriticProvider(pairwise_candidates=(malformed,))
    execution = execute_mirrored_pairwise_quality(
        provider,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 1

    provider = FakeProseQualityCriticProvider(
        pairwise_candidates=(_pairwise_candidate("b"),), failure_at_call=2
    )
    execution = execute_mirrored_pairwise_quality(
        provider,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "inconclusive"
    assert len(execution.reports) == len(execution.calls) == 1
    assert execution.failed_call is None


def test_pairwise_invalid_lineage_stops_before_provider(
    quality_case: dict[str, Any],
) -> None:
    polished = deepcopy(quality_case["polished"])
    polished["previous_render_hash"] = "f" * 64
    provider = FakeProseQualityCriticProvider(
        pairwise_candidates=(_pairwise_candidate("b"), _pairwise_candidate("a"))
    )
    execution = execute_mirrored_pairwise_quality(
        provider,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=polished,
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"
    assert provider.call_count == 0


def test_domain_rejects_tampered_findings_evidence(
    quality_case: dict[str, Any],
) -> None:
    report = json.loads((FIXTURES / "quality_findings.json").read_text(encoding="utf-8"))
    report["findings"][0]["evidence"][0]["text"] += "篡改"
    with pytest.raises(
        CompilerContractError, match="compiler_prose_quality_evidence_invalid"
    ):
        validate_quality_findings_report(
            report,
            checklist=quality_case["checklist"],
            render=quality_case["original"],
            profile=quality_case["profile"],
            semantic_consensus=quality_case["consensus"],
        )


def test_deepseek_adapter_uses_one_json_call_without_retry(
    monkeypatch: pytest.MonkeyPatch, quality_case: dict[str, Any]
) -> None:
    candidate = _findings_candidate(quality_case)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(candidate)))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            prompt_cache_hit_tokens=2,
        ),
    )
    provider = DeepSeekProseQualityCriticProvider()
    monkeypatch.setattr(provider, "_create_completion", lambda _request: response)
    execution = execute_quality_findings(
        provider,
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="secret-not-persisted",
    )
    assert execution.status == "completed"
    assert execution.call is not None
    assert execution.call.usage["total_tokens"] == 30
    assert execution.call.transport_attempts[0].attempt_index == 1
    assert "secret-not-persisted" not in json.dumps(
        execution.call.request_payload, ensure_ascii=False
    )


def test_mirrored_resolver_rejects_nonopposite_position_reports(
    quality_case: dict[str, Any],
) -> None:
    execution = execute_mirrored_pairwise_quality(
        FakeProseQualityCriticProvider(
            pairwise_candidates=(_pairwise_candidate("b"), _pairwise_candidate("a"))
        ),
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=quality_case["polished"],
        profile=quality_case["profile"],
        preservation_consensus=quality_case["preservation"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key="fake",
    )
    second = deepcopy(execution.reports[1])
    second["position_mapping"] = {"a": "original", "b": "polished"}
    with pytest.raises(
        CompilerContractError, match="compiler_prose_quality_mirrored_binding_invalid"
    ):
        resolve_mirrored_quality(execution.reports[0], second)
