"""Frozen N4.5 B3 public Quality development suite and zero-network runner."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import rfc8785

from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    FakeProseQualityCriticProvider,
    ProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    canonical_json_sha256,
    validate_quality_pair_inputs,
    validate_semantic_acceptance,
)

ROOT: Final = Path(__file__).resolve().parents[4]
PUBLIC_ROOT: Final = ROOT / "fixtures/prose_quality_benchmark/v1"
DEFAULT_SUITE: Final = PUBLIC_ROOT / "suite.json"
DEFAULT_ATTESTATION: Final = PUBLIC_ROOT / "review-attestation.json"
PREFERENCES: Final = ("a", "b", "tie")
GATE_THRESHOLDS: Final = {
    "overall_accuracy_min": 8,
    "mirrored_consistency_min": 8,
    "dimension_accuracy_min": 40,
    "semantic_invalid_max": 0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}


class ProseQualitySuiteError(RuntimeError):
    """The public Quality development package is incomplete or drifted."""


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def load_prose_quality_dev_suite(
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Load all eight public pairs and prove semantic, lineage, Gold, and hashes."""

    suite = _load_json(suite_path)
    attestation = _load_json(attestation_path)
    expected = {
        "schema_id": "casefile.prose-quality-dev-suite.v1",
        "suite_id": "n4.5-b3-quality-public-development-v1",
        "suite_role": "development",
        "task_count": 8,
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "preference_distribution": {"a": 2, "b": 4, "tie": 2},
        "quality_model_id": PROSE_QUALITY_MODEL_ID,
        "findings_prompt_version": PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
        "pairwise_prompt_version": PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
        "quality_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "gate_thresholds": GATE_THRESHOLDS,
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "development_baseline": True,
        },
    }
    for key, value in expected.items():
        if suite.get(key) != value:
            raise ProseQualitySuiteError(f"prose_quality_suite_{key}_invalid")
    _validate_self_hash(suite, "suite_hash", "prose_quality_suite_hash_invalid")
    _validate_self_hash(
        attestation, "attestation_hash", "prose_quality_attestation_hash_invalid"
    )
    if attestation != {
        "schema_id": "casefile.prose-quality-dev-attestation.v1",
        "suite_hash": suite["suite_hash"],
        "reviewer": "Codex",
        "reviewer_independence": False,
        "reviewed_task_count": 8,
        "passes": [
            "semantic_acceptance",
            "pair_quality_gold",
            "position_symmetry",
            "development_only",
        ],
        "allowed_use": "public_quality_development_only",
        "qualification": False,
        "unresolved_findings": [],
        "attestation_hash": attestation["attestation_hash"],
    }:
        raise ProseQualitySuiteError("prose_quality_attestation_invalid")
    descriptors = suite.get("tasks")
    if not isinstance(descriptors, list) or len(descriptors) != 8:
        raise ProseQualitySuiteError("prose_quality_suite_cardinality_invalid")
    tasks = [_load_task(descriptor) for descriptor in descriptors]
    ids = [task["descriptor"]["task_id"] for task in tasks]
    fingerprints = [task["descriptor"]["pair_fingerprint"] for task in tasks]
    text_pairs = [
        canonical_hash(
            {
                "a": [block["text"] for block in task["asset"]["render_a"]["blocks"]],
                "b": [block["text"] for block in task["asset"]["render_b"]["blocks"]],
            }
        )
        for task in tasks
    ]
    distribution = Counter(
        task["asset"]["gold"]["overall_preference"] for task in tasks
    )
    if (
        len(set(ids)) != 8
        or len(set(fingerprints)) != 8
        or len(set(text_pairs)) != 8
        or distribution != Counter(suite["preference_distribution"])
    ):
        raise ProseQualitySuiteError("prose_quality_suite_distribution_invalid")
    return {"suite": suite, "attestation": attestation, "tasks": tasks}


def run_prose_quality_development_baseline(
    *,
    provider_factory: Callable[[dict[str, Any]], ProseQualityCriticProvider]
    | None = None,
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen eight-pair denominator twice by position; never qualify."""

    loaded = load_prose_quality_dev_suite(suite_path, attestation_path)
    factory = provider_factory or _fake_provider
    rows: list[dict[str, Any]] = []
    failures = Counter[str]()
    overall_correct = 0
    mirrored_consistent = 0
    dimension_correct = 0
    model_call_count = 0
    usage = _empty_usage()
    for task in loaded["tasks"]:
        asset = task["asset"]
        provider = factory(task)
        execution = execute_mirrored_pairwise_quality(
            provider,
            checklist=asset["checklist"],
            original_render=asset["render_a"],
            polished_render=asset["render_b"],
            profile=asset["profile"],
            preservation_consensus=asset["semantic_consensus_b"],
            model_id=PROSE_QUALITY_MODEL_ID,
            api_key="fake",
        )
        model_call_count += len(execution.calls)
        for call in execution.calls:
            _merge_usage(usage, call.usage)
        row = {
            "task_id": asset["task_id"],
            "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
            "render_hashes": [
                canonical_json_sha256(asset["render_a"]),
                canonical_json_sha256(asset["render_b"]),
            ],
            "status": execution.status,
            "gold_overall": asset["gold"]["overall_preference"],
            "predicted_overall": None,
            "mirrored_consistent": False,
            "dimension_correct": 0,
            "error_code": execution.error_code,
        }
        if execution.status != "completed":
            failures[
                "infrastructure" if execution.status == "inconclusive" else "protocol"
            ] += 1
            rows.append(row)
            continue
        first, second = execution.reports
        gold = asset["gold"]
        swapped_overall = _swap(gold["overall_preference"])
        if (
            first["overall_preference"] == gold["overall_preference"]
            and second["overall_preference"] == swapped_overall
        ):
            overall_correct += 1
        row["predicted_overall"] = first["overall_preference"]
        position_consistent = _swap(second["overall_preference"]) == first[
            "overall_preference"
        ]
        if position_consistent:
            mirrored_consistent += 1
            row["mirrored_consistent"] = True
        first_dimensions = first["dimension_preferences"]
        second_dimensions = second["dimension_preferences"]
        task_dimension_correct = sum(
            first_item == gold_item
            and second_item
            == {
                "dimension": gold_item["dimension"],
                "preference": _swap(gold_item["preference"]),
            }
            for first_item, second_item, gold_item in zip(
                first_dimensions,
                second_dimensions,
                gold["dimension_preferences"],
                strict=True,
            )
        )
        dimension_correct += task_dimension_correct
        row["dimension_correct"] = task_dimension_correct
        rows.append(row)
    gate_passed = (
        overall_correct >= GATE_THRESHOLDS["overall_accuracy_min"]
        and mirrored_consistent >= GATE_THRESHOLDS["mirrored_consistency_min"]
        and dimension_correct >= GATE_THRESHOLDS["dimension_accuracy_min"]
        and not failures
    )
    report = {
        "schema_id": "casefile.prose-quality-dev-report.v1",
        "suite_hash": loaded["suite"]["suite_hash"],
        "status": "completed" if not failures else "inconclusive",
        "task_count": 8,
        "completed_task_count": len(rows),
        "overall_accuracy": {"passed": overall_correct, "total": 8},
        "mirrored_consistency": {"passed": mirrored_consistent, "total": 8},
        "dimension_accuracy": {"passed": dimension_correct, "total": 40},
        "semantic_invalid_count": 0,
        "failure_counts": {
            "protocol": failures["protocol"],
            "infrastructure": failures["infrastructure"],
        },
        "model_call_count": model_call_count,
        "usage": usage,
        "development_gate_passed": gate_passed,
        "development_baseline": True,
        "qualification_eligible": False,
        "qualified": False,
        "rows": rows,
    }
    report["report_hash"] = canonical_hash(report)
    if output_dir is not None:
        _write_json(output_dir / "report.json", report)
    return report


def _load_task(descriptor: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseQualitySuiteError("prose_quality_descriptor_invalid")
    _validate_self_hash(
        descriptor, "content_hash", "prose_quality_descriptor_hash_invalid"
    )
    binding = descriptor.get("task_asset")
    if not isinstance(binding, dict) or set(binding) != {"path", "hash"}:
        raise ProseQualitySuiteError("prose_quality_task_asset_binding_invalid")
    asset_path = (ROOT / str(binding["path"])).resolve()
    if not asset_path.is_relative_to(PUBLIC_ROOT.resolve()) or not asset_path.is_file():
        raise ProseQualitySuiteError("prose_quality_task_asset_path_invalid")
    asset = _load_json(asset_path)
    if binding["hash"] != canonical_hash(asset):
        raise ProseQualitySuiteError("prose_quality_task_asset_hash_invalid")
    _validate_self_hash(asset, "content_hash", "prose_quality_task_hash_invalid")
    if (
        asset.get("schema_id") != "casefile.prose-quality-dev-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
    ):
        raise ProseQualitySuiteError("prose_quality_task_identity_invalid")
    gold = asset.get("gold")
    if (
        not isinstance(gold, dict)
        or gold.get("overall_preference") not in PREFERENCES
        or [item.get("dimension") for item in gold.get("dimension_preferences", [])]
        != list(QUALITY_DIMENSIONS)
        or any(
            item.get("preference") not in PREFERENCES
            for item in gold.get("dimension_preferences", [])
        )
    ):
        raise ProseQualitySuiteError("prose_quality_task_gold_invalid")
    try:
        validate_semantic_acceptance(
            asset["semantic_consensus_a"],
            checklist=asset["checklist"],
            render=asset["render_a"],
            profile=asset["profile"],
        )
        validate_quality_pair_inputs(
            checklist=asset["checklist"],
            original_render=asset["render_a"],
            polished_render=asset["render_b"],
            profile=asset["profile"],
            preservation_consensus=asset["semantic_consensus_b"],
        )
    except (KeyError, CompilerContractError) as error:
        raise ProseQualitySuiteError("prose_quality_task_semantic_invalid") from error
    expected_fingerprint = canonical_hash(
        {
            "render_a_hash": canonical_hash(asset["render_a"]),
            "render_b_hash": canonical_hash(asset["render_b"]),
            "gold": gold,
        }
    )
    if descriptor.get("pair_fingerprint") != expected_fingerprint:
        raise ProseQualitySuiteError("prose_quality_pair_fingerprint_invalid")
    return {"descriptor": descriptor, "asset": asset}


def _fake_provider(task: dict[str, Any]) -> FakeProseQualityCriticProvider:
    gold = task["asset"]["gold"]
    return FakeProseQualityCriticProvider(
        pairwise_candidates=(_candidate(gold), _swapped_candidate(gold))
    )


def _candidate(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        **gold,
    }


def _swapped_candidate(gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        "overall_preference": _swap(gold["overall_preference"]),
        "dimension_preferences": [
            {
                "dimension": item["dimension"],
                "preference": _swap(item["preference"]),
            }
            for item in gold["dimension_preferences"]
        ],
    }


def _swap(preference: str) -> str:
    return {"a": "b", "b": "a", "tie": "tie"}[preference]


def _validate_self_hash(value: dict[str, Any], key: str, error_code: str) -> None:
    if value.get(key) != canonical_hash(
        {name: item for name, item in value.items() if name != key}
    ):
        raise ProseQualitySuiteError(error_code)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProseQualitySuiteError(f"prose_quality_json_invalid:{path.name}") from error
    if not isinstance(value, dict):
        raise ProseQualitySuiteError(f"prose_quality_json_object_required:{path.name}")
    return value


def _empty_usage() -> dict[str, int]:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }


def _merge_usage(total: dict[str, int], value: dict[str, int]) -> None:
    for key in total:
        total[key] += int(value.get(key, 0))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    args = parser.parse_args()
    report = run_prose_quality_development_baseline(
        suite_path=args.suite,
        attestation_path=args.attestation,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "qualified": report["qualified"],
                "overall_accuracy": report["overall_accuracy"],
                "mirrored_consistency": report["mirrored_consistency"],
                "report_hash": report["report_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ATTESTATION",
    "DEFAULT_SUITE",
    "GATE_THRESHOLDS",
    "ProseQualitySuiteError",
    "canonical_hash",
    "load_prose_quality_dev_suite",
    "run_prose_quality_development_baseline",
]
