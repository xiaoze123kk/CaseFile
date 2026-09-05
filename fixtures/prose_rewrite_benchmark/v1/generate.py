"""Deterministically rebuild the public N4.5 B2 Rewrite development suite."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_COMPONENT_HASH,
    PROSE_REWRITER_PROMPT_VERSION,
)
from casefile.benchmark.prose_writer_eval import load_prose_writer_dev_suite
from casefile.domain.narrative_compiler import (
    canonical_json_sha256,
    normalize_scene_render_candidate,
    validate_prose_judge_report,
)

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
FAMILIES = (
    "missing_required_beat",
    "modality_weakening",
    "premature_reveal",
    "pov_knowledge_violation",
    "location_time_drift",
    "causality_ordering_inversion",
    "major_hallucination",
    "multi_error_combination",
)
VARIANTS = ("basic", "preservation_dense", "second_round")
TARGET_KIND = {
    "missing_required_beat": ("beat_realization", "required"),
    "modality_weakening": ("event_modality", "required"),
    "premature_reveal": ("reveal_control", "forbidden"),
    "pov_knowledge_violation": ("pov_knowledge", "forbidden"),
    "location_time_drift": ("location_time", "required"),
    "causality_ordering_inversion": ("causality_ordering", "required"),
    "major_hallucination": ("major_hallucination", "forbidden"),
}
DEFECT_TEXT = {
    "missing_required_beat": "他在关键行动发生前停下，最终没有执行这一场要求完成的核心步骤。",
    "modality_weakening": "他只把这件事当作以后也许会考虑的计划，当下既未执行，也未作出决定。",
    "premature_reveal": "他忽然说出了本应留到后续场景才能揭露的真相，使隐藏信息提前公开。",
    "pov_knowledge_violation": "没有任何在场证据，他却直接知道了远处密室里刚刚发生的一切。",
    "location_time_drift": "叙述毫无过渡地把人物移到另一个地点和错误时刻，当前场景的时空被替换。",
    "causality_ordering_inversion": "结果先于原因完成，后置行动反而被写成了前置行动的触发条件。",
    "major_hallucination": "一支从未在计划中出现的武装队伍闯入，并永久改变了所有人的目标。",
    "multi_error_combination": (
        "核心行动没有发生；与此同时，未经授权的新人物带着提前公开的真相闯入。"
    ),
}


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_tasks() -> list[dict[str, Any]]:
    loaded = load_prose_writer_dev_suite()["tasks"]
    reveal_indices = {0, 3, 6}
    remaining = [index for index in range(24) if index not in reveal_indices]
    by_family: dict[str, list[int]] = {"premature_reveal": sorted(reveal_indices)}
    cursor = 0
    for family in FAMILIES:
        if family == "premature_reveal":
            continue
        by_family[family] = remaining[cursor : cursor + 3]
        cursor += 3
    return [loaded[index] for family in FAMILIES for index in by_family[family]]


def _target_ids(checklist: dict[str, Any], family: str) -> list[str]:
    if family == "multi_error_combination":
        kinds = (("beat_realization", "required"), ("major_hallucination", "forbidden"))
    else:
        kinds = (TARGET_KIND[family],)
    result = []
    for kind, polarity in kinds:
        match = next(
            item
            for item in checklist["checks"]
            if (item["kind"], item["polarity"]) == (kind, polarity)
        )
        result.append(match["check_id"])
    return result


def _bad_candidate(
    valid: dict[str, Any], *, family: str, profile: dict[str, Any]
) -> dict[str, Any]:
    candidate = deepcopy(valid)
    blocks = candidate["blocks"]
    if family in {"missing_required_beat", "modality_weakening", "multi_error_combination"}:
        target = 1 if len(blocks) > 2 else 0
        blocks[target] = {"text": DEFECT_TEXT[family]}
    blocks.append({"text": DEFECT_TEXT[family]})
    minimum = int(profile["prose"]["target_scene_chars"]["min"])
    padding = "周围的声响仍与此前连续，人物只整理手边已经出现的物件，没有增加其他变化。"
    while sum(len(item["text"]) for item in blocks) < minimum:
        blocks[-1]["text"] += padding
    return candidate


def _render(
    candidate: dict[str, Any], checklist: dict[str, Any], profile: dict[str, Any], token: str
) -> dict[str, Any]:
    return normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_hash({"prose_rewrite_fixture": token}),
    ).model_dump(mode="json")


def _assessments(
    *, checklist: dict[str, Any], render: dict[str, Any], failed_ids: list[str]
) -> list[dict[str, Any]]:
    catalog = build_server_evidence_catalog(render)
    first = {key: value for key, value in catalog[0].items() if key != "evidence_id"}
    defect_block = render["blocks"][-1]["block_id"]
    defect = next(item for item in catalog if item["block_id"] == defect_block)
    defect_evidence = {key: value for key, value in defect.items() if key != "evidence_id"}
    failed = set(failed_ids)
    result = []
    for check in checklist["checks"]:
        verdict = "fail" if check["check_id"] in failed else "pass"
        evidence = []
        if check["polarity"] == "required" and verdict == "pass":
            evidence = [deepcopy(first)]
        elif check["polarity"] == "forbidden" and verdict == "fail":
            evidence = [deepcopy(defect_evidence)]
        result.append(
            {
                "check_id": check["check_id"],
                "verdict": verdict,
                "evidence": evidence,
                "rationale": (
                    "公开 B2 Gold：坏正文触发该缺陷。"
                    if verdict == "fail"
                    else "公开 B2 Gold：该项在当前正文中保持通过。"
                ),
            }
        )
    return result


def _candidate_report(report: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    catalog = build_server_evidence_catalog(render)
    by_hash = {
        canonical_hash({key: value for key, value in item.items() if key != "evidence_id"}): item[
            "evidence_id"
        ]
        for item in catalog
    }
    return {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": [
            {
                "check_id": item["check_id"],
                "verdict": item["verdict"],
                "evidence_ids": [by_hash[canonical_hash(value)] for value in item["evidence"]],
                "rationale": item["rationale"],
            }
            for item in report["assessments"]
        ],
    }


def _review(
    checklist: dict[str, Any], profile: dict[str, Any], render: dict[str, Any], failed: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = {
        "schema_id": "compiler.prose-judge-report.v1",
        "role": "fidelity",
        "scene_id": checklist["scene_id"],
        "checklist_hash": canonical_json_sha256(checklist),
        "render_hash": canonical_json_sha256(render),
        "assessments": _assessments(checklist=checklist, render=render, failed_ids=failed),
    }
    validate_prose_judge_report(report, checklist=checklist, render=render, profile=profile)
    council = execute_semantic_council(
        FakeProseJudgeProvider(judge_reports=(_candidate_report(report, render),)),
        checklist=checklist,
        render=render,
        profile=profile,
        policy=FIDELITY_ONLY_POLICY,
        model_id=PROSE_COUNCIL_MODEL_ID,
        api_key="fake",
    )
    if council.status != "completed" or council.consensus is None:
        raise RuntimeError("Failed to build Rewrite fixture consensus")
    return report, council.consensus


def build_suite() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    tasks = []
    assets: dict[str, dict[str, Any]] = {}
    sources = iter(_source_tasks())
    counter = 0
    for family in FAMILIES:
        for variant in VARIANTS:
            counter += 1
            source = next(sources)
            checklist = source["checklist"]
            profile = source["asset"]["profile"]
            valid = source["asset"]["fake_candidate"]
            task_id = f"b2_{counter:02d}_{family}_{variant}"
            target_ids = _target_ids(checklist, family)
            bad = _bad_candidate(valid, family=family, profile=profile)
            initial_render = _render(bad, checklist, profile, f"{task_id}:initial")
            initial_report, initial_consensus = _review(
                checklist, profile, initial_render, target_ids
            )
            rewrite_candidates = [deepcopy(valid)]
            round_gold = [
                {
                    "round": 1,
                    "scene_verdict": "pass",
                    "assessments": _assessments(
                        checklist=checklist,
                        render=_render(valid, checklist, profile, f"{task_id}:round1"),
                        failed_ids=[],
                    ),
                }
            ]
            expected_round = 1
            if variant == "second_round":
                rewrite_candidates = [deepcopy(bad), deepcopy(valid)]
                round_gold = [
                    {
                        "round": 1,
                        "scene_verdict": "fail",
                        "assessments": deepcopy(initial_report["assessments"]),
                    },
                    {
                        "round": 2,
                        "scene_verdict": "pass",
                        "assessments": _assessments(
                            checklist=checklist,
                            render=_render(valid, checklist, profile, f"{task_id}:round2"),
                            failed_ids=[],
                        ),
                    },
                ]
                expected_round = 2
            passed_ids = [
                item["check_id"]
                for item in initial_consensus["checks"]
                if item["final_verdict"] == "pass"
            ]
            critical_ids = [
                item["check_id"]
                for item in checklist["checks"]
                if item["kind"]
                in {"event_modality", "reveal_control", "pov_knowledge", "major_hallucination"}
            ]
            asset = {
                "schema_id": "casefile.prose-rewrite-dev-task.v1",
                "task_id": task_id,
                "profile": profile,
                "previous_scene_render": source["asset"]["previous_scene_render"],
                "initial_render": initial_render,
                "initial_judge_report": initial_report,
                "initial_consensus": initial_consensus,
                "initial_passed_check_ids": passed_ids,
                "original_issue_check_ids": target_ids,
                "critical_check_ids": critical_ids,
                "fake_rewrite_candidates": rewrite_candidates,
                "round_gold": round_gold,
                "review_notes": {
                    "defect_family": family,
                    "expected_rescue_round": expected_round,
                    "semantic_review": "development_fixture_reviewed",
                },
            }
            asset["content_hash"] = canonical_hash(asset)
            asset_rel = Path(f"fixtures/prose_rewrite_benchmark/v1/tasks/{task_id}.json")
            source_descriptor = source["descriptor"]
            descriptor = {
                "task_id": task_id,
                "defect_family": family,
                "variant": variant,
                "expected_rescue_round": expected_round,
                "scene_id": checklist["scene_id"],
                "source_input": deepcopy(source_descriptor["source_input"]),
                "scene_plan": deepcopy(source_descriptor["scene_plan"]),
                "task_asset": {"path": asset_rel.as_posix(), "hash": canonical_hash(asset)},
                "checklist_hash": canonical_hash(checklist),
                "input_fingerprint": canonical_hash(
                    {
                        "writer_input_fingerprint": source_descriptor["input_fingerprint"],
                        "initial_render_hash": canonical_hash(initial_render),
                        "initial_consensus_hash": canonical_hash(initial_consensus),
                        "defect_family": family,
                        "variant": variant,
                    }
                ),
            }
            descriptor["content_hash"] = canonical_hash(descriptor)
            tasks.append(descriptor)
            assets[task_id] = asset
    suite = {
        "schema_id": "casefile.prose-rewrite-dev-suite.v1",
        "suite_id": "n4.5-b2-rewrite-public-development-v1",
        "suite_role": "development",
        "defect_families": list(FAMILIES),
        "variants": list(VARIANTS),
        "task_count": 24,
        "council_policy_id": FIDELITY_ONLY_POLICY.policy_id,
        "council_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "rewriter_prompt_version": PROSE_REWRITER_PROMPT_VERSION,
        "rewriter_component_hash": PROSE_REWRITER_COMPONENT_HASH,
        "gate_thresholds": {
            "final_rescue_min": 21,
            "preservation_task_min": 23,
            "new_critical_issue_max": 0,
            "extra_rewrite_call_max": 0,
            "protocol_failure_max": 0,
            "infrastructure_failure_max": 0,
        },
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "development_baseline": True,
        },
        "tasks": tasks,
    }
    suite["suite_hash"] = canonical_hash(suite)
    attestation = {
        "schema_id": "casefile.prose-rewrite-dev-attestation.v1",
        "suite_hash": suite["suite_hash"],
        "reviewer": "Codex",
        "reviewer_independence": False,
        "reviewed_task_count": 24,
        "passes": [
            "bad_render_lineage",
            "consensus_binding",
            "original_passed_checks",
            "round_gold_evidence",
        ],
        "allowed_use": "public_rewrite_development_only",
        "qualification": False,
        "unresolved_findings": [],
    }
    attestation["attestation_hash"] = canonical_hash(attestation)
    return suite, attestation, assets


def main() -> None:
    suite, attestation, assets = build_suite()
    for task_id, asset in assets.items():
        _write(OUT / "tasks" / f"{task_id}.json", asset)
    _write(OUT / "suite.json", suite)
    _write(OUT / "review-attestation.json", attestation)


if __name__ == "__main__":
    main()
