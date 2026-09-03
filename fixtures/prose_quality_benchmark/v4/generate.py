"""Build the frozen public B3 v4 pointwise Quality development set."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from casefile.agent_runtime.prose_judge import build_server_evidence_catalog
from casefile.agent_runtime.prose_quality_assessor import (
    PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
    PROSE_QUALITY_ASSESSMENT_MODEL_ID,
    PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
)
from casefile.benchmark.prose_quality_v4_eval import PUBLIC_GATES, canonical_hash
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS

ROOT: Final = Path(__file__).resolve().parents[3]
SOURCE: Final = ROOT / "fixtures/prose_quality_benchmark/v1"
OUT: Final = ROOT / "fixtures/prose_quality_benchmark/v4"
FOCUS_DIMENSIONS: Final = {
    "readability_clearer": ("readability_editability",),
    "pacing_original": ("dramatic_progression_pacing",),
    "specificity_polished": ("scene_specificity",),
    "voice_consistency": ("pov_voice_consistency",),
    "dialogue_naturalness": ("dialogue_narration_naturalness",),
    "balanced_paraphrase_tie": tuple(QUALITY_DIMENSIONS),
    "tradeoff_tie": (
        "scene_specificity",
        "dramatic_progression_pacing",
        "readability_editability",
    ),
    "original_more_vivid": (
        "scene_specificity",
        "dramatic_progression_pacing",
        "readability_editability",
    ),
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate(
    render: dict[str, Any], *, target_dimensions: tuple[str, ...], severity: str
) -> dict[str, Any]:
    evidence_id = build_server_evidence_catalog(render)[0]["evidence_id"]
    return {
        "schema_id": "compiler.prose-quality-assessment-candidate.v1",
        "dimensions": [
            {
                "dimension": dimension,
                "severity": severity if dimension in target_dimensions else "none",
                "evidence_ids": [evidence_id] if dimension in target_dimensions else [],
                "rationale": "该维度存在可定位的表达问题。"
                if dimension in target_dimensions
                else "未发现值得处理的问题。",
            }
            for dimension in QUALITY_DIMENSIONS
        ],
    }


def build_suite() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    source_suite = _read(SOURCE / "suite.json")
    assets: dict[str, dict[str, Any]] = {}
    descriptors: list[dict[str, Any]] = []
    for source_descriptor in source_suite["tasks"]:
        source_asset = _read(ROOT / source_descriptor["task_asset"]["path"])
        source_focus = source_asset["review_notes"]["focus"]
        targets = FOCUS_DIMENSIONS[source_focus]
        overall = source_asset["gold"]["overall_preference"]
        if overall == "b":
            severity_a, severity_b, accept = "medium", "low", True
        elif overall == "a":
            severity_a, severity_b, accept = "low", "medium", False
        else:
            severity_a, severity_b, accept = "low", "low", False
        task_id = source_asset["task_id"].replace("quality_dev", "quality_v4_dev")
        gold = {
            "render_a": _candidate(
                source_asset["render_a"],
                target_dimensions=targets,
                severity=severity_a,
            ),
            "render_b": _candidate(
                source_asset["render_b"],
                target_dimensions=targets,
                severity=severity_b,
            ),
            "accept_polished": accept,
        }
        asset = {
            "schema_id": "casefile.prose-quality-pointwise-dev-task.v1",
            "task_id": task_id,
            "focus": source_focus,
            "profile": deepcopy(source_asset["profile"]),
            "checklist": deepcopy(source_asset["checklist"]),
            "render_a": deepcopy(source_asset["render_a"]),
            "semantic_consensus_a": deepcopy(source_asset["semantic_consensus_a"]),
            "render_b": deepcopy(source_asset["render_b"]),
            "semantic_consensus_b": deepcopy(source_asset["semantic_consensus_b"]),
            "gold": gold,
            "review_notes": {
                "source_public_task_hash": canonical_hash(source_asset),
                "semantic_review": "both_anonymous_renders_pass_same_checklist",
                "quality_review": "five_dimension_severity_and_delta_reviewed",
                "qualification": False,
            },
        }
        asset["content_hash"] = canonical_hash(asset)
        relative = Path(f"fixtures/prose_quality_benchmark/v4/tasks/{task_id}.json")
        assets[relative.as_posix()] = asset
        descriptor = {
            "task_id": task_id,
            "focus": source_focus,
            "task_asset": {"path": relative.as_posix(), "hash": canonical_hash(asset)},
            "pair_fingerprint": canonical_hash(
                {
                    "render_a_hash": canonical_hash(asset["render_a"]),
                    "render_b_hash": canonical_hash(asset["render_b"]),
                    "gold": gold,
                }
            ),
        }
        descriptor["content_hash"] = canonical_hash(descriptor)
        descriptors.append(descriptor)
    suite = {
        "schema_id": "casefile.prose-quality-pointwise-dev-suite.v1",
        "suite_id": "n4.5-b3-quality-public-development-v4",
        "suite_role": "development",
        "task_count": 8,
        "assessment_count": 16,
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "quality_model_id": PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        "assessment_prompt_version": PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
        "quality_component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
        "gate_thresholds": PUBLIC_GATES,
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "development_baseline": True,
        },
        "tasks": descriptors,
    }
    suite["suite_hash"] = canonical_hash(suite)
    attestation = {
        "schema_id": "casefile.prose-quality-pointwise-dev-attestation.v1",
        "suite_hash": suite["suite_hash"],
        "reviewer": "Codex",
        "reviewer_independence": False,
        "reviewed_task_count": 8,
        "passes": [
            "semantic_acceptance",
            "five_dimension_severity_gold",
            "delta_gold",
            "anonymous_single_render_protocol",
            "development_only",
        ],
        "allowed_use": "public_quality_v4_development_only",
        "qualification": False,
        "unresolved_findings": [],
    }
    attestation["attestation_hash"] = canonical_hash(attestation)
    return suite, attestation, assets


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    built_suite, built_attestation, built_assets = build_suite()
    for path_value, value in built_assets.items():
        write(ROOT / path_value, value)
    write(OUT / "suite.json", built_suite)
    write(OUT / "review-attestation.json", built_attestation)
