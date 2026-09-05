"""Paired Pro rescore refuses changed prose, policy and prompt, and never qualifies."""

from copy import deepcopy
from dataclasses import asdict

import pytest
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider, build_server_evidence_catalog
from casefile.agent_runtime.prose_polish_supervisor import execute_prose_polish_supervisor
from casefile.agent_runtime.prose_polisher import FakeProsePolisherProvider
from casefile.agent_runtime.prose_quality_critic import FakeProseQualityCriticProvider
from casefile.benchmark.prose_quality_diagnostic import write_new
from casefile.benchmark.prose_quality_diagnostic_suite import load_diagnostic_suite
from casefile.benchmark.prose_quality_rescore import (
    decision_stats,
    read_verified,
    summarize,
    validate_case,
)
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS


@pytest.fixture(scope="module")
def source_case():
    task = load_diagnostic_suite()["polisher_tasks"][0]
    evidence = build_server_evidence_catalog(task["original_render"])[0]["evidence_id"]
    judge = {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": [
            {
                "check_id": c["check_id"],
                "verdict": "pass",
                "rationale": "测试已知合法正文。",
                "evidence_ids": [evidence] if c["polarity"] == "required" else [],
            }
            for c in task["checklist"]["checks"]
        ],
    }

    def preference(value):
        return {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            "overall_preference": value,
            "dimension_preferences": [
                {"dimension": d, "preference": value} for d in QUALITY_DIMENSIONS
            ],
        }

    result = execute_prose_polish_supervisor(
        FakeProseQualityCriticProvider(
            findings_candidates=(
                {"schema_id": "compiler.prose-quality-findings-candidate.v1", "findings": []},
            ),
            pairwise_candidates=(preference("b"), preference("a")),
        ),
        FakeProsePolisherProvider(
            candidates=(
                {
                    "schema_id": "compiler.scene-render-candidate.v1",
                    "blocks": [{"text": b["text"]} for b in task["original_render"]["blocks"]],
                },
            )
        ),
        FakeProseJudgeProvider(judge_reports=(judge, judge, judge)),
        checklist=task["checklist"],
        profile=task["profile"],
        original_render=task["original_render"],
        semantic_consensus=task["semantic_consensus"],
        quality_model_id="deepseek-v4-flash",
        generation_model_id="deepseek-v4-pro",
        api_key="fake",
    )
    assert result.status == "finalized_polished"
    return task, {
        "row": {"task_id": task["task_id"], "repeat": 0},
        "execution": asdict(result),
        "content_hash": "a" * 64,
    }


def test_preflight_only_changes_model_and_keeps_source_immutable(source_case):
    task, saved = source_case
    before = deepcopy(saved)
    case = validate_case(task, saved, 0)
    assert saved == before
    assert case["original"] == task["original_render"]
    assert case["flash_decision"]["accept_polished"]


@pytest.mark.parametrize("mutation", ["identity", "original", "policy", "roles", "prompt"])
def test_invalid_frozen_source_is_rejected(source_case, mutation):
    task, saved = source_case
    saved = deepcopy(saved)
    if mutation == "identity":
        saved["row"]["repeat"] = 1
    elif mutation == "original":
        saved["execution"]["original_render"]["blocks"][0]["text"] += "额外事实"
    elif mutation == "policy":
        saved["execution"]["preservation"]["policy_hash"] = "0" * 64
    elif mutation == "roles":
        saved["execution"]["preservation"]["judge_reports"] = saved["execution"]["preservation"][
            "judge_reports"
        ][:-1]
    else:
        saved["execution"]["pairwise"]["calls"][0]["prompt_hash"] = "0" * 64
    with pytest.raises(ValueError):
        validate_case(task, saved, 0)


def test_source_hash_tampering_is_rejected(tmp_path):
    path = tmp_path / "record.json"
    write_new(path, {"value": 1})
    assert read_verified(path)["value"] == 1
    path.write_text(
        path.read_text(encoding="utf-8").replace('"value": 1', '"value": 2'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="source_hash_invalid"):
        read_verified(path)


def test_summary_separates_mirrored_tie_from_adoption(source_case):
    _, saved = source_case
    reports = deepcopy(saved["execution"]["pairwise"]["reports"])
    for report in reports:
        report["overall_preference"] = "tie"
    stats = decision_stats(reports)
    assert stats["mirrored"]
    assert stats["overall"] == ["tie", "tie"]
    rows = [
        {
            "task_id": f"polisher_{i:02d}",
            "repeat": r,
            "status": "completed",
            "flash_adopted": False,
            "pro_adopted": True,
            "flash_reason": "quality_unstable",
            "pro_reason": "polished_accepted",
            "flash": {"mirrored": False},
            "pro": {"mirrored": True},
        }
        for r in range(3)
        for i in range(1, 25)
    ]
    result = summarize(rows)
    assert result["qualified"] is False
    assert all(r["pro_adoption_threshold_met"] for r in result["rounds"])
    assert result["transitions"] == {"quality_unstable -> polished_accepted": 72}
    assert not summarize(rows[1:])["rounds"][0]["pro_adoption_threshold_met"]
