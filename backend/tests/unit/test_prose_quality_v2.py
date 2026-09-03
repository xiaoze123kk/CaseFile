"""B3 v4 pointwise Quality, bounded patch, and supervisor tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from casefile.agent_runtime.prose_judge import (
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
)
from casefile.agent_runtime.prose_patch_polisher import (
    PROSE_PATCH_POLISHER_MODEL_ID,
    FakeProsePatchPolisherProvider,
)
from casefile.agent_runtime.prose_polish_supervisor_v2 import (
    execute_prose_polish_supervisor_v2,
)
from casefile.agent_runtime.prose_quality_assessor import (
    PROSE_QUALITY_ASSESSMENT_MODEL_ID,
    FakeProseQualityAssessmentProvider,
    build_quality_assessment_request,
    execute_quality_assessment,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    apply_prose_polish_patch,
    build_editable_window_manifest,
    canonical_json_sha256,
    resolve_quality_delta,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures/compiler/prose_rendering/v1"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def quality_case() -> dict[str, Any]:
    return {
        "checklist": _load("checklist_scene_1.json"),
        "profile": _load("profile_v2.json"),
        "original": _load("scene_render_writer.json"),
        "consensus": _load("consensus_pass.json"),
    }


def _assessment_candidate(
    render: dict[str, Any],
    *,
    severity: str = "medium",
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    catalog = build_server_evidence_catalog(render)
    selected = (
        evidence_ids
        if evidence_ids is not None
        else ([catalog[0]["evidence_id"]] if severity != "none" else [])
    )
    return {
        "schema_id": "compiler.prose-quality-assessment-candidate.v1",
        "dimensions": [
            {
                "dimension": dimension,
                "severity": severity if dimension == "readability_editability" else "none",
                "evidence_ids": selected if dimension == "readability_editability" else [],
                "rationale": "句间层次需要调整。"
                if dimension == "readability_editability" and severity != "none"
                else "未发现值得处理的问题。",
            }
            for dimension in QUALITY_DIMENSIONS
        ],
    }


def _assessment_report(
    case: dict[str, Any], render: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    consensus = deepcopy(case["consensus"])
    consensus["render_hash"] = canonical_json_sha256(render)
    execution = execute_quality_assessment(
        FakeProseQualityAssessmentProvider(candidates=(candidate,)),
        checklist=case["checklist"],
        render=render,
        profile=case["profile"],
        semantic_consensus=consensus,
        model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "completed", execution.error_code
    assert execution.assessment is not None
    return execution.assessment


def _windows(case: dict[str, Any], assessment: dict[str, Any]):
    return build_editable_window_manifest(
        assessment=assessment,
        checklist=case["checklist"],
        render=case["original"],
        profile=case["profile"],
        semantic_consensus=case["consensus"],
        evidence_catalog=build_server_evidence_catalog(case["original"]),
    )


def _patch_candidate(
    case: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    window = manifest["windows"][0]
    replacement = window["original_text"].replace("冷白", "苍白", 1)
    if replacement == window["original_text"]:
        replacement = "此刻，" + window["original_text"]
    return {
        "schema_id": "compiler.prose-polish-patch-candidate.v1",
        "source_render_hash": canonical_json_sha256(case["original"]),
        "window_manifest_hash": canonical_json_sha256(manifest),
        "edits": [
            {
                "window_id": window["window_id"],
                "original_text_hash": window["original_text_hash"],
                "replacement_text": replacement,
            }
        ],
    }


def _patched_render(
    case: dict[str, Any], manifest: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    render = apply_prose_polish_patch(
        patch,
        manifest=manifest,
        checklist=case["checklist"],
        profile=case["profile"],
        current_render=case["original"],
        component_input_hash="a" * 64,
    )
    assert render is not None
    return render.model_dump(mode="json")


def _judge_candidate(case: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(render)[0]["evidence_id"]
    return {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": [
            {
                "check_id": check["check_id"],
                "verdict": "pass",
                "evidence_ids": [evidence_id]
                if check["polarity"] == "required"
                else [],
                "rationale": "完整正文证据支持该项。",
            }
            for check in case["checklist"]["checks"]
        ],
    }


def test_pointwise_request_is_anonymous_and_requires_complete_five_dimensions(
    quality_case: dict[str, Any],
) -> None:
    request, _, _ = build_quality_assessment_request(
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        api_key="credential-canary",
    )
    serialized = json.dumps(request.input_payload, ensure_ascii=False)
    assert "credential-canary" not in serialized
    assert all(token not in serialized for token in ('"original"', '"polished"', '"a"', '"b"'))
    assert request.input_payload["quality_dimensions"] == list(QUALITY_DIMENSIONS)

    candidate = _assessment_candidate(quality_case["original"])
    candidate["dimensions"].pop()
    provider = FakeProseQualityAssessmentProvider(candidates=(candidate,))
    execution = execute_quality_assessment(
        provider,
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"


@pytest.mark.parametrize(
    ("severity", "evidence_ids"),
    (("none", ["evidence_001"]), ("low", []), ("low", ["evidence_999"])),
)
def test_assessment_evidence_rules_fail_closed(
    quality_case: dict[str, Any], severity: str, evidence_ids: list[str]
) -> None:
    candidate = _assessment_candidate(
        quality_case["original"], severity=severity, evidence_ids=evidence_ids
    )
    execution = execute_quality_assessment(
        FakeProseQualityAssessmentProvider(candidates=(candidate,)),
        checklist=quality_case["checklist"],
        render=quality_case["original"],
        profile=quality_case["profile"],
        semantic_consensus=quality_case["consensus"],
        model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "protocol_failed"


def test_window_patch_preserves_every_byte_outside_authorized_unicode_span(
    quality_case: dict[str, Any],
) -> None:
    assessment = _assessment_report(
        quality_case,
        quality_case["original"],
        _assessment_candidate(quality_case["original"]),
    )
    windows = _windows(quality_case, assessment)
    assert windows.status == "ready"
    assert len(windows.manifest["windows"]) == 1
    assert windows.manifest["coverage_chars"] * 5 <= windows.manifest["render_chars"] * 2
    patch = _patch_candidate(quality_case, windows.manifest)
    polished = _patched_render(quality_case, windows.manifest, patch)
    window = windows.manifest["windows"][0]
    source = quality_case["original"]["blocks"][0]["text"]
    result = polished["blocks"][0]["text"]
    replacement = patch["edits"][0]["replacement_text"]
    assert result[: window["start_char"]] == source[: window["start_char"]]
    assert result[window["start_char"] + len(replacement) :] == source[window["end_char"] :]
    assert len(polished["blocks"]) == len(quality_case["original"]["blocks"])


def test_multi_block_window_limit_and_adjacent_merge_are_deterministic(
    quality_case: dict[str, Any],
) -> None:
    render = deepcopy(quality_case["original"])
    render["blocks"] = [
        {
            "block_id": f"block_scene_1_{index:03d}",
            "ordinal": index,
            "text": (
                "甲核对编号。乙记录时间。丙检查封条。丁确认顺序。戊收起表格。"
                "己查看灯号。庚比对刻痕。辛标记差异。壬封存面板。癸退出房间。"
            ),
        }
        for index in range(1, 7)
    ]
    render["character_count"] = sum(len(item["text"]) for item in render["blocks"])
    consensus = deepcopy(quality_case["consensus"])
    consensus["render_hash"] = canonical_json_sha256(render)
    case = {**quality_case, "original": render, "consensus": consensus}
    catalog = build_server_evidence_catalog(render)
    first_ids = [
        next(item["evidence_id"] for item in catalog if item["block_id"] == block_id)
        for block_id in (
            "block_scene_1_001",
            "block_scene_1_003",
            "block_scene_1_005",
            "block_scene_1_006",
        )
    ]
    three = _assessment_report(
        case,
        render,
        _assessment_candidate(render, evidence_ids=first_ids[:3]),
    )
    three_windows = _windows(case, three)
    assert three_windows.status == "ready"
    assert len(three_windows.manifest["windows"]) == 3

    four = _assessment_report(
        case,
        render,
        _assessment_candidate(render, evidence_ids=first_ids),
    )
    assert _windows(case, four).status == "scope_exceeded"

    adjacent = _assessment_report(
        case,
        render,
        _assessment_candidate(
            render,
            evidence_ids=[catalog[0]["evidence_id"], catalog[1]["evidence_id"]],
        ),
    )
    adjacent_windows = _windows(case, adjacent)
    assert len(adjacent_windows.manifest["windows"]) == 1


@pytest.mark.parametrize("mutation", ("unknown", "duplicate", "stale"))
def test_unknown_duplicate_and_stale_patch_windows_are_rejected(
    quality_case: dict[str, Any], mutation: str
) -> None:
    assessment = _assessment_report(
        quality_case,
        quality_case["original"],
        _assessment_candidate(quality_case["original"]),
    )
    windows = _windows(quality_case, assessment)
    patch = _patch_candidate(quality_case, windows.manifest)
    if mutation == "unknown":
        patch["edits"][0]["window_id"] = "window_999"
    elif mutation == "duplicate":
        patch["edits"].append(deepcopy(patch["edits"][0]))
    else:
        patch["edits"][0]["original_text_hash"] = "f" * 64
    with pytest.raises(CompilerContractError):
        apply_prose_polish_patch(
            patch,
            manifest=windows.manifest,
            checklist=quality_case["checklist"],
            profile=quality_case["profile"],
            current_render=quality_case["original"],
            component_input_hash="a" * 64,
        )


def test_delta_accepts_target_improvement_and_rejects_any_regression(
    quality_case: dict[str, Any],
) -> None:
    before = _assessment_report(
        quality_case,
        quality_case["original"],
        _assessment_candidate(quality_case["original"], severity="medium"),
    )
    windows = _windows(quality_case, before)
    polished = _patched_render(
        quality_case, windows.manifest, _patch_candidate(quality_case, windows.manifest)
    )
    preservation = deepcopy(quality_case["consensus"])
    preservation["render_hash"] = canonical_json_sha256(polished)
    after = _assessment_report(
        quality_case,
        polished,
        _assessment_candidate(polished, severity="low"),
    )
    delta = resolve_quality_delta(
        original_assessment=before,
        polished_assessment=after,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=polished,
        profile=quality_case["profile"],
        original_semantic_consensus=quality_case["consensus"],
        preservation_consensus=preservation,
    ).model_dump(mode="json")
    assert delta["accept_polished"] is True

    regressed_candidate = _assessment_candidate(polished, severity="low")
    regressed_candidate["dimensions"][0] = {
        "dimension": "pov_voice_consistency",
        "severity": "low",
        "evidence_ids": ["evidence_001"],
        "rationale": "出现新的视角问题。",
    }
    regressed = _assessment_report(quality_case, polished, regressed_candidate)
    rejected = resolve_quality_delta(
        original_assessment=before,
        polished_assessment=regressed,
        checklist=quality_case["checklist"],
        original_render=quality_case["original"],
        polished_render=polished,
        profile=quality_case["profile"],
        original_semantic_consensus=quality_case["consensus"],
        preservation_consensus=preservation,
    ).model_dump(mode="json")
    assert rejected["accept_polished"] is False
    assert rejected["selection_reason"] == "quality_rollback"


def test_supervisor_noop_calls_only_one_assessment(quality_case: dict[str, Any]) -> None:
    assessment_provider = FakeProseQualityAssessmentProvider(
        candidates=(
            _assessment_candidate(quality_case["original"], severity="none"),
        )
    )
    polisher = FakeProsePatchPolisherProvider()
    judge = FakeProseJudgeProvider()
    execution = execute_prose_polish_supervisor_v2(
        assessment_provider,
        polisher,
        judge,
        checklist=quality_case["checklist"],
        profile=quality_case["profile"],
        original_render=quality_case["original"],
        semantic_consensus=quality_case["consensus"],
        quality_model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        generation_model_id=PROSE_PATCH_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_original"
    assert execution.selection_reason == "quality_noop"
    assert execution.audit_record is not None
    assert execution.audit_record["patch_hash"] is None
    assert execution.audit_record["quality_delta_hash"] is None
    assert assessment_provider.call_count == 1
    assert polisher.call_count == 0
    assert judge.call_count == 0


def test_supervisor_scope_overflow_rolls_back_before_polisher(
    quality_case: dict[str, Any],
) -> None:
    candidate = _assessment_candidate(
        quality_case["original"], evidence_ids=["evidence_001", "evidence_009"]
    )
    assessment_provider = FakeProseQualityAssessmentProvider(candidates=(candidate,))
    polisher = FakeProsePatchPolisherProvider()
    judge = FakeProseJudgeProvider()
    execution = execute_prose_polish_supervisor_v2(
        assessment_provider,
        polisher,
        judge,
        checklist=quality_case["checklist"],
        profile=quality_case["profile"],
        original_render=quality_case["original"],
        semantic_consensus=quality_case["consensus"],
        quality_model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        generation_model_id=PROSE_COUNCIL_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_original"
    assert execution.selection_reason == "polish_scope_rollback"
    assert execution.window_manifest is not None
    assert execution.window_manifest["coverage_chars"] * 5 > execution.window_manifest[
        "render_chars"
    ] * 2
    assert polisher.call_count == judge.call_count == 0


def test_supervisor_accepts_only_after_preservation_and_target_delta(
    quality_case: dict[str, Any],
) -> None:
    before_candidate = _assessment_candidate(
        quality_case["original"], severity="medium"
    )
    before = _assessment_report(quality_case, quality_case["original"], before_candidate)
    windows = _windows(quality_case, before)
    patch = _patch_candidate(quality_case, windows.manifest)
    polished = _patched_render(quality_case, windows.manifest, patch)
    after_candidate = _assessment_candidate(polished, severity="low")
    judge_candidate = _judge_candidate(quality_case, polished)
    assessment_provider = FakeProseQualityAssessmentProvider(
        candidates=(before_candidate, after_candidate)
    )
    execution = execute_prose_polish_supervisor_v2(
        assessment_provider,
        FakeProsePatchPolisherProvider(candidates=(patch,)),
        FakeProseJudgeProvider(
            judge_reports=(judge_candidate, judge_candidate, judge_candidate)
        ),
        checklist=quality_case["checklist"],
        profile=quality_case["profile"],
        original_render=quality_case["original"],
        semantic_consensus=quality_case["consensus"],
        quality_model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        generation_model_id=PROSE_PATCH_POLISHER_MODEL_ID,
        api_key="fake",
    )
    assert execution.status == "finalized_polished"
    assert execution.selection_reason == "polished_accepted"
    assert execution.quality_delta is not None
    assert execution.quality_delta["accept_polished"] is True
    assert execution.audit_record is not None
    for field in (
        "original_assessment_hash",
        "window_manifest_hash",
        "patch_hash",
        "preservation_hash",
        "polished_assessment_hash",
        "quality_delta_hash",
    ):
        assert isinstance(execution.audit_record[field], str)
    assert assessment_provider.call_count == 2
    assert execution.accepted_render is not None
    assert execution.accepted_render["previous_render_hash"] == canonical_json_sha256(
        execution.polish.render  # type: ignore[union-attr]
    )
