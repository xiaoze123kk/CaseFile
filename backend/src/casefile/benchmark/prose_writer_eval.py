"""N4.5-04 public Writer development suite and zero-network baseline."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import rfc8785

from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    ProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_writer import (
    PROSE_WRITER_COMPONENT_HASH,
    PROSE_WRITER_MODEL_ID,
    PROSE_WRITER_PROMPT_VERSION,
    FakeProseWriterProvider,
    ProseWriterProvider,
    execute_prose_writer,
)
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    normalize_scene_render_candidate,
    validate_prose_judge_report,
)

ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_SUITE: Final = ROOT / "fixtures/prose_writer_benchmark/v1/suite.json"
DEFAULT_ATTESTATION: Final = (
    ROOT / "fixtures/prose_writer_benchmark/v1/review-attestation.json"
)
ABILITIES: Final = (
    "beat_realization",
    "canon_grounding_major_hallucination",
    "pov_knowledge",
    "reveal_control",
    "location_time_continuity",
    "causality_ordering",
    "setup_payoff_scene_outcome",
    "profile_bounded_surface_detail",
)
VARIANTS: Final = ("basic", "implicit_friendly", "constraint_dense")
CHECK_KINDS: Final = (
    "beat_realization",
    "event_modality",
    "reveal_control",
    "pov_knowledge",
    "location_time",
    "causality_ordering",
    "major_hallucination",
    "scene_outcome",
)


class ProseWriterSuiteError(RuntimeError):
    """The public Writer development suite is incomplete, unsafe, or drifted."""


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def load_prose_writer_dev_suite(
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Load and prove the 8x3 distribution, lineage, contracts, and review binding."""

    suite = _load_json(suite_path)
    attestation = _load_json(attestation_path)
    if suite.get("schema_id") != "casefile.prose-writer-dev-suite.v1":
        raise ProseWriterSuiteError("prose_writer_suite_schema_invalid")
    if suite.get("suite_hash") != canonical_hash(
        {key: value for key, value in suite.items() if key != "suite_hash"}
    ):
        raise ProseWriterSuiteError("prose_writer_suite_hash_invalid")
    if attestation.get("attestation_hash") != canonical_hash(
        {key: value for key, value in attestation.items() if key != "attestation_hash"}
    ):
        raise ProseWriterSuiteError("prose_writer_attestation_hash_invalid")
    if (
        attestation.get("suite_hash") != suite["suite_hash"]
        or attestation.get("reviewer_independence") is not False
        or attestation.get("holdout_qualification") is not False
        or attestation.get("unresolved_findings") != []
        or attestation.get("passes")
        != ["input_lineage", "fake_candidate", "evidence_binding"]
    ):
        raise ProseWriterSuiteError("prose_writer_attestation_invalid")
    if tuple(suite.get("abilities", ())) != ABILITIES or tuple(
        suite.get("variants", ())
    ) != VARIANTS:
        raise ProseWriterSuiteError("prose_writer_suite_matrix_invalid")
    qualification = suite.get("qualification")
    if qualification != {
        "qualified": False,
        "qualification_eligible": False,
        "stage": "development_baseline_only",
    }:
        raise ProseWriterSuiteError("prose_writer_suite_qualification_invalid")
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 24:
        raise ProseWriterSuiteError("prose_writer_suite_cardinality_invalid")

    distribution = Counter[tuple[str, str]]()
    task_ids: set[str] = set()
    fingerprints: set[str] = set()
    loaded_tasks = []
    for descriptor in tasks:
        loaded = _load_task(descriptor)
        task_id = loaded["descriptor"]["task_id"]
        fingerprint = loaded["descriptor"]["input_fingerprint"]
        if task_id in task_ids or fingerprint in fingerprints:
            raise ProseWriterSuiteError("prose_writer_task_identity_duplicate")
        task_ids.add(task_id)
        fingerprints.add(fingerprint)
        distribution[(descriptor["ability"], descriptor["variant"])] += 1
        loaded_tasks.append(loaded)
    expected = Counter({(ability, variant): 1 for ability in ABILITIES for variant in VARIANTS})
    if distribution != expected:
        raise ProseWriterSuiteError("prose_writer_suite_distribution_invalid")
    return {"suite": suite, "attestation": attestation, "tasks": loaded_tasks}


def run_writer_development_baseline(
    *,
    writer_provider_factory: Callable[[dict[str, Any]], ProseWriterProvider] | None = None,
    judge_provider_factory: Callable[
        [dict[str, Any], dict[str, Any]], ProseJudgeProvider
    ]
    | None = None,
    output_dir: Path | None = None,
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Run every public task once; never repair, retry, qualify, or touch the network."""

    loaded = load_prose_writer_dev_suite(suite_path, attestation_path)
    writer_factory = writer_provider_factory or (
        lambda task: FakeProseWriterProvider(candidates=(task["asset"]["fake_candidate"],))
    )
    judge_factory = judge_provider_factory or (
        lambda task, render: FakeProseJudgeProvider(
            judge_reports=(_gold_candidate(task["asset"]["gold"], render),)
        )
    )
    rows = []
    writer_call_count = 0
    judge_call_count = 0
    writer_usage = _empty_usage()
    judge_usage = _empty_usage()
    failures = Counter[str]()
    check_metrics = {
        kind: {"pass": 0, "fail": 0, "uncertain": 0, "not_evaluated": 0}
        for kind in CHECK_KINDS
    }
    character_counts: list[int] = []
    for task in loaded["tasks"]:
        descriptor = task["descriptor"]
        writer = execute_prose_writer(
            writer_factory(task),
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=task["asset"]["profile"],
            checklist=task["checklist"],
            previous_scene_render=task["asset"]["previous_scene_render"],
            model_id=PROSE_WRITER_MODEL_ID,
            api_key="fake",
            remaining_scene_call_budget=23,
        )
        if writer.call is not None:
            writer_call_count += 1
            _merge_usage(writer_usage, writer.call.usage)
        elif writer.failed_call is not None:
            writer_call_count += 1
        row = {
            "task_id": descriptor["task_id"],
            "ability": descriptor["ability"],
            "variant": descriptor["variant"],
            "input_fingerprint": descriptor["input_fingerprint"],
            "writer_status": writer.status,
            "writer_error_code": writer.error_code,
            "writer_request_fingerprint": (
                writer.call.request_fingerprint
                if writer.call is not None
                else writer.failed_call.request_fingerprint
                if writer.failed_call is not None
                else None
            ),
            "render_hash": None,
            "character_count": None,
            "council_status": "not_run",
            "council_error_code": None,
            "scene_verdict": None,
        }
        if writer.status != "completed" or writer.render is None:
            category = (
                "writer_infrastructure"
                if writer.status == "inconclusive"
                else "writer_protocol"
            )
            failures[category] += 1
            _mark_not_evaluated(check_metrics, task["checklist"])
            rows.append(row)
            continue
        render = writer.render
        character_counts.append(render["character_count"])
        row["render_hash"] = canonical_json_sha256(render)
        row["character_count"] = render["character_count"]
        council = execute_semantic_council(
            judge_factory(task, render),
            checklist=task["checklist"],
            render=render,
            profile=task["asset"]["profile"],
            policy=FIDELITY_ONLY_POLICY,
            model_id=PROSE_COUNCIL_MODEL_ID,
            api_key="fake",
        )
        judge_call_count += len(council.calls) + (1 if council.failed_call else 0)
        for call in council.calls:
            _merge_usage(judge_usage, call.usage)
        row["council_status"] = council.status
        row["council_error_code"] = council.error_code
        if council.status != "completed" or council.consensus is None:
            category = (
                "council_infrastructure"
                if council.status == "inconclusive"
                else "council_protocol"
            )
            failures[category] += 1
            _mark_not_evaluated(check_metrics, task["checklist"])
            rows.append(row)
            continue
        row["scene_verdict"] = council.consensus["scene_verdict"]
        if council.consensus["scene_verdict"] != "pass":
            failures["semantic"] += 1
        kinds = {
            item["check_id"]: item["kind"] for item in task["checklist"]["checks"]
        }
        for item in council.consensus["checks"]:
            check_metrics[kinds[item["check_id"]]][item["final_verdict"]] += 1
        rows.append(row)

    passed = sum(row["scene_verdict"] == "pass" for row in rows)
    if failures["writer_infrastructure"] or failures["council_infrastructure"]:
        status = "inconclusive"
    elif sum(failures.values()):
        status = "failed"
    else:
        status = "completed"
    report = {
        "schema_id": "casefile.prose-writer-development-baseline.v1",
        "status": status,
        "development_baseline": True,
        "qualified": False,
        "qualification_eligible": False,
        "task_count": 24,
        "completed_task_count": len(rows),
        "initial_semantic_pass": {"passed": passed, "total": 24},
        "council_policy_id": FIDELITY_ONLY_POLICY.policy_id,
        "council_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "writer_model_id": PROSE_WRITER_MODEL_ID,
        "writer_prompt_version": PROSE_WRITER_PROMPT_VERSION,
        "writer_component_hash": PROSE_WRITER_COMPONENT_HASH,
        "suite_hash": loaded["suite"]["suite_hash"],
        "attestation_hash": loaded["attestation"]["attestation_hash"],
        "call_count": writer_call_count + judge_call_count,
        "writer_call_count": writer_call_count,
        "judge_call_count": judge_call_count,
        "usage": {"writer": writer_usage, "judge": judge_usage},
        "failures": {
            "semantic": failures["semantic"],
            "writer_protocol": failures["writer_protocol"],
            "writer_infrastructure": failures["writer_infrastructure"],
            "council_protocol": failures["council_protocol"],
            "council_infrastructure": failures["council_infrastructure"],
        },
        "check_kind_metrics": check_metrics,
        "character_count": {
            "observed": len(character_counts),
            "minimum": min(character_counts) if character_counts else None,
            "maximum": max(character_counts) if character_counts else None,
            "total": sum(character_counts),
        },
        "rows": rows,
    }
    report["report_hash"] = canonical_hash(report)
    if output_dir is not None:
        _write_json(output_dir / "report.json", report)
    return report


def _load_task(descriptor: Any) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseWriterSuiteError("prose_writer_task_descriptor_invalid")
    if descriptor.get("content_hash") != canonical_hash(
        {key: value for key, value in descriptor.items() if key != "content_hash"}
    ):
        raise ProseWriterSuiteError("prose_writer_task_descriptor_hash_invalid")
    if descriptor.get("ability") not in ABILITIES or descriptor.get("variant") not in VARIANTS:
        raise ProseWriterSuiteError("prose_writer_task_distribution_invalid")
    source_input = _load_bound_json(descriptor.get("source_input"), "source_input")
    scene_plan = _load_bound_json(descriptor.get("scene_plan"), "scene_plan")
    asset = _load_bound_json(descriptor.get("task_asset"), "task_asset")
    if asset.get("schema_id") != "casefile.prose-writer-dev-task.v1":
        raise ProseWriterSuiteError("prose_writer_task_asset_schema_invalid")
    if asset.get("task_id") != descriptor.get("task_id"):
        raise ProseWriterSuiteError("prose_writer_task_asset_identity_invalid")
    if asset.get("content_hash") != canonical_hash(
        {key: value for key, value in asset.items() if key != "content_hash"}
    ):
        raise ProseWriterSuiteError("prose_writer_task_asset_hash_invalid")
    narrative_ir = source_input.get("narrative_ir")
    if not isinstance(narrative_ir, dict):
        raise ProseWriterSuiteError("prose_writer_narrative_ir_invalid")
    previous = asset.get("previous_scene_render")
    profile = asset.get("profile")
    if not isinstance(profile, dict) or (previous is not None and not isinstance(previous, dict)):
        raise ProseWriterSuiteError("prose_writer_task_input_invalid")
    checklist = build_prose_judge_checklist(
        scene_plan=scene_plan,
        narrative_ir=narrative_ir,
        profile=profile,
        scene_id=str(descriptor.get("scene_id")),
        previous_scene_render=previous,
    )
    if descriptor.get("checklist_hash") != canonical_hash(checklist):
        raise ProseWriterSuiteError("prose_writer_task_checklist_hash_invalid")
    previous_hash = None if previous is None else canonical_hash(previous)
    if descriptor.get("previous_scene_render_hash") != previous_hash:
        raise ProseWriterSuiteError("prose_writer_task_previous_hash_invalid")
    expected_fingerprint = canonical_hash(
        {
            "scene_plan_hash": canonical_hash(scene_plan),
            "narrative_ir_hash": canonical_hash(narrative_ir),
            "profile_hash": canonical_hash(profile),
            "previous_scene_render_hash": previous_hash,
            "checklist_hash": canonical_hash(checklist),
            "scene_id": descriptor["scene_id"],
        }
    )
    if descriptor.get("input_fingerprint") != expected_fingerprint:
        raise ProseWriterSuiteError("prose_writer_task_input_fingerprint_invalid")
    for pointer in descriptor.get("input_evidence_paths", ()):
        _resolve_pointer(scene_plan, pointer)
    candidate = asset.get("fake_candidate")
    gold = asset.get("gold")
    if not isinstance(candidate, dict) or not isinstance(gold, dict):
        raise ProseWriterSuiteError("prose_writer_task_fake_data_invalid")
    render = normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_hash({"fixture_validation": descriptor["task_id"]}),
    ).model_dump(mode="json")
    report = {
        "schema_id": "compiler.prose-judge-report.v1",
        "role": "fidelity",
        "scene_id": checklist["scene_id"],
        "checklist_hash": canonical_json_sha256(checklist),
        "render_hash": canonical_json_sha256(render),
        "assessments": gold.get("assessments"),
    }
    validate_prose_judge_report(report, checklist=checklist, render=render, profile=profile)
    if gold.get("scene_verdict") != "pass" or any(
        item.get("verdict") != "pass" for item in gold.get("assessments", ())
    ):
        raise ProseWriterSuiteError("prose_writer_task_gold_invalid")
    return {
        "descriptor": descriptor,
        "source_input": source_input,
        "scene_plan": scene_plan,
        "narrative_ir": narrative_ir,
        "asset": asset,
        "checklist": checklist,
    }


def _load_bound_json(binding: Any, label: str) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != {"path", "hash"}:
        raise ProseWriterSuiteError(f"prose_writer_{label}_binding_invalid")
    path_value = binding.get("path")
    if not isinstance(path_value, str):
        raise ProseWriterSuiteError(f"prose_writer_{label}_path_invalid")
    relative = Path(path_value)
    if relative.is_absolute() or "var" in relative.parts or "private" in relative.parts:
        raise ProseWriterSuiteError(f"prose_writer_{label}_path_invalid")
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ProseWriterSuiteError(f"prose_writer_{label}_path_invalid")
    value = _load_json(path)
    if binding.get("hash") != canonical_hash(value):
        raise ProseWriterSuiteError(f"prose_writer_{label}_hash_invalid")
    return value


def _resolve_pointer(value: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ProseWriterSuiteError("prose_writer_evidence_pointer_invalid")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ProseWriterSuiteError("prose_writer_evidence_pointer_invalid")
    return current


def _gold_candidate(gold: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    catalog = build_server_evidence_catalog(render)
    by_hash = {
        canonical_hash({key: value for key, value in item.items() if key != "evidence_id"}): item[
            "evidence_id"
        ]
        for item in catalog
    }
    assessments = []
    for item in gold["assessments"]:
        try:
            evidence_ids = [by_hash[canonical_hash(evidence)] for evidence in item["evidence"]]
        except KeyError as error:
            raise ProseWriterSuiteError("prose_writer_gold_evidence_catalog_mismatch") from error
        assessments.append(
            {
                "check_id": item["check_id"],
                "verdict": item["verdict"],
                "evidence_ids": evidence_ids,
                "rationale": item["rationale"],
            }
        )
    return {"schema_id": "compiler.prose-judge-candidate.v1", "assessments": assessments}


def _mark_not_evaluated(
    metrics: dict[str, dict[str, int]], checklist: dict[str, Any]
) -> None:
    for item in checklist["checks"]:
        metrics[item["kind"]]["not_evaluated"] += 1


def _empty_usage() -> dict[str, int]:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }


def _merge_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key in total:
        total[key] += int(usage.get(key, 0))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProseWriterSuiteError(f"prose_writer_json_invalid:{path.name}") from error
    if not isinstance(value, dict):
        raise ProseWriterSuiteError(f"prose_writer_json_object_required:{path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake",), default="fake")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    args = parser.parse_args()
    report = run_writer_development_baseline(
        output_dir=args.output_dir,
        suite_path=args.suite,
        attestation_path=args.attestation,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "qualified": report["qualified"],
                "initial_semantic_pass": report["initial_semantic_pass"],
                "report_hash": report["report_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ABILITIES",
    "DEFAULT_ATTESTATION",
    "DEFAULT_SUITE",
    "ProseWriterSuiteError",
    "VARIANTS",
    "canonical_hash",
    "load_prose_writer_dev_suite",
    "run_writer_development_baseline",
]
