"""Deterministically rebuild the public N4.5-04 Writer development suite."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785
from casefile.agent_runtime.prose_judge import build_server_evidence_catalog
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    normalize_scene_render_candidate,
    validate_scene_render,
)

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "fixtures/compiler/prose_rendering/v1/profile_v2.json"

ABILITY_SOURCES = {
    "beat_realization": "scene_decomposition",
    "canon_grounding_major_hallucination": "scene_grounding",
    "pov_knowledge": "event_grounding",
    "reveal_control": "reveal_control",
    "location_time_continuity": "temporal_grounding",
    "causality_ordering": "dependency_transfer",
    "setup_payoff_scene_outcome": "resolution_execution",
    "profile_bounded_surface_detail": "provenance_coverage",
}
VARIANT_SOURCES = {
    "basic": "basic",
    "implicit_friendly": "decoy",
    "constraint_dense": "dense",
}
ABILITY_EVIDENCE_PATHS = {
    "beat_realization": ["/scenes", "/beats"],
    "canon_grounding_major_hallucination": ["/scenes", "/beats", "/source"],
    "pov_knowledge": ["/initial_state/character_knowledge", "/beats"],
    "reveal_control": ["/scenes", "/initial_state/audience_exposure"],
    "location_time_continuity": ["/initial_state/locations", "/scenes"],
    "causality_ordering": ["/edges", "/beats"],
    "setup_payoff_scene_outcome": ["/scenes", "/beats", "/final_state/open_setups"],
    "profile_bounded_surface_detail": ["/scenes", "/beats"],
}


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _profile(variant: str) -> dict[str, Any]:
    profile = _load(PROFILE_PATH)
    if variant == "implicit_friendly":
        profile["prose"]["style_brief"] = (
            "克制、清晰；允许用动作、潜台词和省略表达已经成立的语义，不作总结式解释。"
        )
    elif variant == "constraint_dense":
        profile["prose"]["style_brief"] = (
            "克制、清晰；在多项语义约束并存时仍保持动作顺序、有限视角和可编辑段落。"
        )
        profile["prose"]["pacing"] = "fast"
    return profile


def _label(checklist: dict[str, Any], ref: dict[str, Any] | None) -> str:
    if ref is None:
        return "当前场景"
    for item in checklist["scene_context"]["object_catalog"]:
        if item["object_ref"] == ref:
            return str(item["label"])
    return "当前场景对象"


def _fake_candidate(checklist: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    context = checklist["scene_context"]
    pov = _label(checklist, context["pov_ref"])
    location = _label(checklist, context["location_ref"])
    blocks = [
        {
            "text": (
                f"在{location}，{pov}面对眼前的阻力：{context['conflict']}"
                f"他只依据当前可见的信息推进目标，不把猜测当成事实。"
            )
        }
    ]
    for beat in context["beats"]:
        directive = str(beat["directive"]).strip().rstrip("。")
        blocks.append({"text": f"{directive}。这一行动在当前场景中实际发生并留下结果。"})
    blocks.append(
        {
            "text": (
                f"场景最终形成既定结果：{context['outcome']}"
                "人物没有提前揭露后续信息，也没有引入未经授权的重要人物、事件或结论。"
            )
        }
    )
    minimum = int(profile["prose"]["target_scene_chars"]["min"])
    padding = (
        "环境中的细微声响保持连续，视角人物核对已经发生的动作、地点和时间，"
        "只记录能够由当前场景支持的变化，并为下一场保留清楚而有限的衔接。"
    )
    while sum(len(item["text"]) for item in blocks) < minimum:
        blocks[-1]["text"] += padding
    return {"schema_id": "compiler.scene-render-candidate.v1", "blocks": blocks}


def _accepted_render(
    *,
    candidate: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    writer = normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_hash({"fixture_previous": token}),
    ).model_dump(mode="json")
    accepted = {
        **writer,
        "stage": "accepted",
        "previous_render_hash": canonical_json_sha256(writer),
        "selection_reason": "semantic_accepted",
    }
    return validate_scene_render(
        accepted, checklist=checklist, profile=profile
    ).model_dump(mode="json")


def _gold(
    *,
    checklist: dict[str, Any],
    candidate: dict[str, Any],
    profile: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    render = normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_hash({"fixture_candidate": task_id}),
    ).model_dump(mode="json")
    catalog = build_server_evidence_catalog(render)
    if not catalog:
        raise RuntimeError("Writer fixture Evidence catalog is empty")
    evidence = {key: value for key, value in catalog[0].items() if key != "evidence_id"}
    assessments = []
    for check in checklist["checks"]:
        assessments.append(
            {
                "check_id": check["check_id"],
                "verdict": "pass",
                "evidence": [deepcopy(evidence)] if check["polarity"] == "required" else [],
                "rationale": "公开 Fake 基线审定：候选满足该项或未触发禁止项。",
            }
        )
    return {"scene_verdict": "pass", "assessments": assessments}


def _scene_chain(
    *,
    plan: dict[str, Any],
    narrative: dict[str, Any],
    profile: dict[str, Any],
    token: str,
) -> list[dict[str, Any]]:
    chain = []
    previous = None
    for scene in sorted(plan["scenes"], key=lambda item: item["discourse_order"]):
        checklist = build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            scene_id=scene["scene_id"],
            previous_scene_render=previous,
        )
        candidate = _fake_candidate(checklist, profile)
        accepted = _accepted_render(
            candidate=candidate,
            checklist=checklist,
            profile=profile,
            token=f"{token}:{scene['scene_id']}",
        )
        chain.append(
            {
                "scene_id": scene["scene_id"],
                "checklist": checklist,
                "previous_scene_render": previous,
                "candidate": candidate,
                "accepted": accepted,
            }
        )
        previous = accepted
    return chain


def _select_scene(chain: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    if variant == "basic":
        return chain[0]
    if variant == "implicit_friendly":
        return chain[1]
    return min(
        chain,
        key=lambda item: (-len(item["checklist"]["checks"]), item["checklist"]["scene_ordinal"]),
    )


def build_suite() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    tasks = []
    assets: dict[str, dict[str, Any]] = {}
    counter = 0
    for ability, source in ABILITY_SOURCES.items():
        for variant, upstream_variant in VARIANT_SOURCES.items():
            counter += 1
            task_id = f"b1_{counter:02d}_{ability}_{variant}"
            input_rel = Path(
                f"fixtures/scene_plan_benchmark/v1/inputs/{source}__{upstream_variant}.json"
            )
            plan_rel = Path(
                "fixtures/scene_plan_benchmark/v2/runtime_references/"
                f"{source}__{upstream_variant}.json"
            )
            source_input = _load(ROOT / input_rel)
            plan = _load(ROOT / plan_rel)
            narrative = source_input["narrative_ir"]
            profile = _profile(variant)
            selected = _select_scene(
                _scene_chain(
                    plan=plan,
                    narrative=narrative,
                    profile=profile,
                    token=task_id,
                ),
                variant,
            )
            candidate = selected["candidate"]
            checklist = selected["checklist"]
            asset = {
                "schema_id": "casefile.prose-writer-dev-task.v1",
                "task_id": task_id,
                "profile": profile,
                "previous_scene_render": selected["previous_scene_render"],
                "fake_candidate": candidate,
                "gold": _gold(
                    checklist=checklist,
                    candidate=candidate,
                    profile=profile,
                    task_id=task_id,
                ),
            }
            asset["content_hash"] = canonical_hash(asset)
            asset_rel = Path(f"fixtures/prose_writer_benchmark/v1/tasks/{task_id}.json")
            descriptor = {
                "task_id": task_id,
                "ability": ability,
                "variant": variant,
                "scene_id": selected["scene_id"],
                "source_input": {
                    "path": input_rel.as_posix(),
                    "hash": canonical_hash(source_input),
                },
                "scene_plan": {
                    "path": plan_rel.as_posix(),
                    "hash": canonical_hash(plan),
                },
                "task_asset": {
                    "path": asset_rel.as_posix(),
                    "hash": canonical_hash(asset),
                },
                "checklist_hash": canonical_hash(checklist),
                "previous_scene_render_hash": (
                    None
                    if selected["previous_scene_render"] is None
                    else canonical_hash(selected["previous_scene_render"])
                ),
                "input_evidence_paths": ABILITY_EVIDENCE_PATHS[ability],
                "input_fingerprint": canonical_hash(
                    {
                        "scene_plan_hash": canonical_hash(plan),
                        "narrative_ir_hash": canonical_hash(narrative),
                        "profile_hash": canonical_hash(profile),
                        "previous_scene_render_hash": (
                            None
                            if selected["previous_scene_render"] is None
                            else canonical_hash(selected["previous_scene_render"])
                        ),
                        "checklist_hash": canonical_hash(checklist),
                        "scene_id": selected["scene_id"],
                    }
                ),
            }
            descriptor["content_hash"] = canonical_hash(descriptor)
            tasks.append(descriptor)
            assets[task_id] = asset
    suite = {
        "schema_id": "casefile.prose-writer-dev-suite.v1",
        "suite_id": "n4.5-b1-writer-public-development-v1",
        "abilities": list(ABILITY_SOURCES),
        "variants": list(VARIANT_SOURCES),
        "tasks": tasks,
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "stage": "development_baseline_only",
        },
    }
    suite["suite_hash"] = canonical_hash(suite)
    attestation = {
        "schema_id": "casefile.prose-writer-dev-attestation.v1",
        "suite_hash": suite["suite_hash"],
        "reviewer": "Codex",
        "reviewer_independence": False,
        "passes": ["input_lineage", "fake_candidate", "evidence_binding"],
        "allowed_use": "public_writer_development_baseline_only",
        "holdout_qualification": False,
        "unresolved_findings": [],
        "statement": (
            "公开 Fake 资产只验证当前 Writer 协议与评测闭环，不代表 B0、B1 或 B4 资格。"
        ),
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
