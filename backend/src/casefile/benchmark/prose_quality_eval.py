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

from casefile.agent_runtime.prose_polisher import (
    PROSE_POLISHER_COMPONENT_HASH,
    PROSE_POLISHER_MODEL_ID,
    PROSE_POLISHER_PROMPT_VERSION,
)
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
    validate_scene_render,
    validate_semantic_acceptance,
)

ROOT: Final = Path(__file__).resolve().parents[4]
PUBLIC_ROOT: Final = ROOT / "fixtures/prose_quality_benchmark/v1"
DEFAULT_SUITE: Final = PUBLIC_ROOT / "suite.json"
DEFAULT_ATTESTATION: Final = PUBLIC_ROOT / "review-attestation.json"
PRIVATE_ROOT: Final = ROOT / "backend/var/benchmark/private/prose-quality"
DEFAULT_PRIVATE_QUALIFICATION_SUITE: Final = (
    PRIVATE_ROOT / "qualification-v1/suite.json"
)
DEFAULT_QUALIFICATION_DESCRIPTOR: Final = (
    ROOT
    / "backend/src/casefile/benchmark/policies/prose-quality-qualification-v1-descriptor.json"
)
PREFERENCES: Final = ("a", "b", "tie")
QUALITY_FOCI: Final = (
    "pov_voice_consistency",
    "scene_specificity",
    "dialogue_narration_naturalness",
    "dramatic_progression_pacing",
    "readability_editability",
    "sentence_rhythm",
    "redundancy_control",
    "balanced_tradeoff",
)
GATE_THRESHOLDS: Final = {
    "overall_accuracy_min": 8,
    "mirrored_consistency_min": 8,
    "dimension_accuracy_min": 40,
    "semantic_invalid_max": 0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}
QUALITY_QUALIFICATION_GATES: Final = {
    "overall_accuracy_min": 14,
    "mirrored_consistency_min": 15,
    "semantic_invalid_max": 0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}
POLISHER_QUALIFICATION_GATES: Final = {
    "preservation_pass_min": 24,
    "quality_non_loss_min": 22,
    "polished_accepted_min": 18,
    "critical_semantic_regression_max": 0,
    "rejected_exact_rollback_rate_min": 1.0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}


class ProseQualitySuiteError(RuntimeError):
    """The public Quality development package is incomplete or drifted."""


class ProseQualityQualificationBlocked(ProseQualitySuiteError):
    """The private package has not completed its frozen review policy."""


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


def load_prose_quality_qualification_suite(
    suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
) -> dict[str, Any]:
    """Load the reviewed private 16-pair and 24-Scene B3 qualification package."""

    descriptor = _load_json(descriptor_path)
    if descriptor.get("schema_id") != "casefile.prose-quality-qualification-descriptor.v1":
        raise ProseQualitySuiteError("prose_quality_qualification_descriptor_invalid")
    _validate_self_hash(
        descriptor,
        "descriptor_hash",
        "prose_quality_qualification_descriptor_hash_invalid",
    )
    expected_descriptor = {
        "suite_id": "n4.5-b3-quality-polisher-private-qualification-v1",
        "quality_holdout_count": 16,
        "polisher_task_count": 24,
        "quality_focus_distribution": {focus: 2 for focus in QUALITY_FOCI},
        "polisher_focus_distribution": {focus: 3 for focus in QUALITY_FOCI},
        "quality_preference_distribution": {"a": 4, "b": 8, "tie": 4},
        "quality_gate_thresholds": QUALITY_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_QUALIFICATION_GATES,
        "loader_version": "prose-quality-suite-loader-v1",
        "quality_model_id": PROSE_QUALITY_MODEL_ID,
        "generation_model_id": PROSE_POLISHER_MODEL_ID,
        "quality_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "polisher_component_hash": PROSE_POLISHER_COMPONENT_HASH,
        "polisher_prompt_version": PROSE_POLISHER_PROMPT_VERSION,
    }
    for key, value in expected_descriptor.items():
        if descriptor.get(key) != value:
            raise ProseQualitySuiteError(
                f"prose_quality_qualification_descriptor_{key}_invalid"
            )
    public = load_prose_quality_dev_suite()["suite"]
    if descriptor.get("public_development_suite_hash") != public["suite_hash"]:
        raise ProseQualitySuiteError("prose_quality_public_suite_hash_invalid")
    if (
        descriptor.get("review_policy") != "codex-owner-accepted-review-v1"
        or descriptor.get("review_status") != "codex_reviewed"
        or descriptor.get("qualification_eligible") is not True
        or not descriptor.get("author_attestation_hash")
        or not descriptor.get("review_attestation_hash")
    ):
        raise ProseQualityQualificationBlocked("prose_quality_qualification_review_pending")
    resolved = suite_path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT.resolve()) or not resolved.is_file():
        raise ProseQualitySuiteError("prose_quality_qualification_private_path_invalid")
    suite = _load_json(resolved)
    if canonical_hash(suite) != descriptor.get("private_suite_hash"):
        raise ProseQualitySuiteError("prose_quality_qualification_suite_hash_invalid")
    _validate_self_hash(
        suite, "suite_hash", "prose_quality_qualification_suite_self_hash_invalid"
    )
    expected_suite = {
        "schema_id": "casefile.prose-quality-qualification-suite.v1",
        "suite_id": descriptor["suite_id"],
        "suite_role": "qualification",
        "quality_holdout_count": 16,
        "polisher_task_count": 24,
        "quality_foci": list(QUALITY_FOCI),
        "quality_preference_distribution": descriptor[
            "quality_preference_distribution"
        ],
        "quality_gate_thresholds": QUALITY_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_QUALIFICATION_GATES,
        "quality_model_id": PROSE_QUALITY_MODEL_ID,
        "generation_model_id": PROSE_POLISHER_MODEL_ID,
        "quality_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "polisher_component_hash": PROSE_POLISHER_COMPONENT_HASH,
        "polisher_prompt_version": PROSE_POLISHER_PROMPT_VERSION,
    }
    for key, value in expected_suite.items():
        if suite.get(key) != value:
            raise ProseQualitySuiteError(
                f"prose_quality_qualification_suite_{key}_invalid"
            )
    package_root = resolved.parent
    author = _load_json(package_root / "author-attestation.json")
    reviewer = _load_json(package_root / "reviewer-attestation.json")
    if (
        canonical_hash(author) != descriptor["author_attestation_hash"]
        or author.get("role") != "author"
        or author.get("suite_id") != suite["suite_id"]
        or author.get("suite_hash") != suite["suite_hash"]
        or author.get("authored_quality_count") != 16
        or author.get("authored_polisher_count") != 24
        or author.get("review_policy") != descriptor["review_policy"]
        or author.get("review_completed") is not True
        or author.get("owner_accepted_codex_review") is not True
        or author.get("unresolved_findings") != []
        or canonical_hash(reviewer) != descriptor["review_attestation_hash"]
        or reviewer.get("role") != "reviewer"
        or reviewer.get("suite_id") != suite["suite_id"]
        or reviewer.get("suite_hash") != suite["suite_hash"]
        or reviewer.get("reviewer") != "Codex"
        or reviewer.get("reviewer_independence") is not False
        or reviewer.get("review_policy") != descriptor["review_policy"]
        or reviewer.get("owner_acceptance") is not True
        or reviewer.get("reviewed_quality_count") != 16
        or reviewer.get("reviewed_polisher_count") != 24
        or reviewer.get("unresolved_findings") != []
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_review_invalid")
    quality_descriptors = suite.get("quality_tasks")
    polisher_descriptors = suite.get("polisher_tasks")
    if (
        not isinstance(quality_descriptors, list)
        or len(quality_descriptors) != 16
        or not isinstance(polisher_descriptors, list)
        or len(polisher_descriptors) != 24
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_cardinality_invalid")
    quality_tasks = [
        _load_private_quality_task(item, package_root) for item in quality_descriptors
    ]
    polisher_tasks = [
        _load_private_polisher_task(item, package_root) for item in polisher_descriptors
    ]
    quality_foci = Counter(task["descriptor"]["focus"] for task in quality_tasks)
    polisher_foci = Counter(task["descriptor"]["focus"] for task in polisher_tasks)
    preferences = Counter(
        task["asset"]["gold"]["overall_preference"] for task in quality_tasks
    )
    quality_ids = [task["descriptor"]["task_id"] for task in quality_tasks]
    polisher_ids = [task["descriptor"]["task_id"] for task in polisher_tasks]
    pair_fingerprints = [
        task["descriptor"]["pair_fingerprint"] for task in quality_tasks
    ]
    input_fingerprints = [
        task["descriptor"]["input_fingerprint"] for task in polisher_tasks
    ]
    if (
        quality_foci != Counter(descriptor["quality_focus_distribution"])
        or polisher_foci != Counter(descriptor["polisher_focus_distribution"])
        or preferences != Counter(descriptor["quality_preference_distribution"])
        or len(set(quality_ids)) != 16
        or len(set(polisher_ids)) != 24
        or set(quality_ids) & set(polisher_ids)
        or len(set(pair_fingerprints)) != 16
        or len(set(input_fingerprints)) != 24
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_distribution_invalid")
    return {
        "descriptor": descriptor,
        "suite": suite,
        "author": author,
        "reviewer": reviewer,
        "quality_tasks": quality_tasks,
        "polisher_tasks": polisher_tasks,
    }


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


def _load_private_quality_task(
    descriptor: dict[str, Any], package_root: Path
) -> dict[str, Any]:
    asset = _load_private_asset(descriptor, package_root, "quality-tasks")
    gold = asset.get("gold")
    if (
        asset.get("schema_id") != "casefile.prose-quality-qualification-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
        or asset.get("focus") != descriptor.get("focus")
        or descriptor.get("overall_preference")
        != (gold.get("overall_preference") if isinstance(gold, dict) else None)
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_task_identity_invalid")
    if (
        not isinstance(gold, dict)
        or gold.get("overall_preference") not in PREFERENCES
        or [item.get("dimension") for item in gold.get("dimension_preferences", [])]
        != list(QUALITY_DIMENSIONS)
        or any(
            item.get("preference") not in PREFERENCES
            for item in gold.get("dimension_preferences", [])
        )
        or asset.get("author_review", {}).get("semantic_status")
        != "codex_reviewed_owner_accepted"
        or asset.get("author_review", {}).get("quality_status")
        != "codex_reviewed_owner_accepted"
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_task_gold_invalid")
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
        raise ProseQualitySuiteError(
            "prose_quality_qualification_task_semantic_invalid"
        ) from error
    expected = canonical_hash(
        {
            "a": canonical_hash(asset["render_a"]),
            "b": canonical_hash(asset["render_b"]),
            "gold": gold,
        }
    )
    if descriptor.get("pair_fingerprint") != expected:
        raise ProseQualitySuiteError(
            "prose_quality_qualification_pair_fingerprint_invalid"
        )
    return {"descriptor": descriptor, "asset": asset}


def _load_private_polisher_task(
    descriptor: dict[str, Any], package_root: Path
) -> dict[str, Any]:
    asset = _load_private_asset(descriptor, package_root, "polisher-tasks")
    if (
        asset.get("schema_id") != "casefile.prose-polisher-qualification-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
        or asset.get("focus") != descriptor.get("focus")
        or asset.get("author_review", {}).get("semantic_status")
        != "codex_reviewed_owner_accepted"
        or asset.get("author_review", {}).get("surface_issue_status")
        != "codex_reviewed_owner_accepted"
    ):
        raise ProseQualitySuiteError(
            "prose_polisher_qualification_task_identity_invalid"
        )
    target_dimensions = asset.get("target_dimensions")
    if (
        not isinstance(target_dimensions, list)
        or not target_dimensions
        or len(target_dimensions) != len(set(target_dimensions))
        or not set(target_dimensions) <= set(QUALITY_DIMENSIONS)
    ):
        raise ProseQualitySuiteError(
            "prose_polisher_qualification_dimensions_invalid"
        )
    try:
        render = validate_scene_render(
            asset["original_render"],
            checklist=asset["checklist"],
            profile=asset["profile"],
        ).model_dump(mode="json")
        validate_semantic_acceptance(
            asset["semantic_consensus"],
            checklist=asset["checklist"],
            render=render,
            profile=asset["profile"],
        )
    except (KeyError, CompilerContractError) as error:
        raise ProseQualitySuiteError(
            "prose_polisher_qualification_task_semantic_invalid"
        ) from error
    critical_ids = [
        check["check_id"]
        for check in asset["checklist"]["checks"]
        if check["kind"]
        in {"event_modality", "reveal_control", "pov_knowledge", "major_hallucination"}
    ]
    if (
        render["stage"] not in {"writer", "rewrite_1", "rewrite_2"}
        or asset.get("critical_check_ids") != critical_ids
    ):
        raise ProseQualitySuiteError(
            "prose_polisher_qualification_task_binding_invalid"
        )
    expected = canonical_hash(
        {
            "render": canonical_hash(render),
            "consensus": canonical_hash(asset["semantic_consensus"]),
            "focus": asset["focus"],
        }
    )
    if descriptor.get("input_fingerprint") != expected:
        raise ProseQualitySuiteError(
            "prose_polisher_qualification_input_fingerprint_invalid"
        )
    return {"descriptor": descriptor, "asset": asset}


def _load_private_asset(
    descriptor: dict[str, Any], package_root: Path, expected_directory: str
) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseQualitySuiteError("prose_quality_qualification_descriptor_invalid")
    _validate_self_hash(
        descriptor,
        "content_hash",
        "prose_quality_qualification_task_descriptor_hash_invalid",
    )
    binding = descriptor.get("task_asset")
    if not isinstance(binding, dict) or set(binding) != {"path", "hash"}:
        raise ProseQualitySuiteError("prose_quality_qualification_asset_binding_invalid")
    relative = Path(str(binding["path"]))
    asset_path = (package_root / relative).resolve()
    expected_root = (package_root / expected_directory).resolve()
    if (
        relative.is_absolute()
        or not asset_path.is_relative_to(expected_root)
        or not asset_path.is_file()
    ):
        raise ProseQualitySuiteError("prose_quality_qualification_asset_path_invalid")
    asset = _load_json(asset_path)
    if canonical_hash(asset) != binding["hash"]:
        raise ProseQualitySuiteError("prose_quality_qualification_asset_hash_invalid")
    _validate_self_hash(
        asset,
        "content_hash",
        "prose_quality_qualification_task_hash_invalid",
    )
    return asset


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
    parser.add_argument("--mode", choices=("fake", "qualification-check"), default="fake")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument(
        "--qualification-suite", type=Path, default=DEFAULT_PRIVATE_QUALIFICATION_SUITE
    )
    parser.add_argument("--descriptor", type=Path, default=DEFAULT_QUALIFICATION_DESCRIPTOR)
    args = parser.parse_args()
    if args.mode == "qualification-check":
        try:
            loaded = load_prose_quality_qualification_suite(
                args.qualification_suite, args.descriptor
            )
            result = {
                "status": "ready",
                "qualified": False,
                "quality_holdout_count": len(loaded["quality_tasks"]),
                "polisher_task_count": len(loaded["polisher_tasks"]),
                "suite_hash": loaded["suite"]["suite_hash"],
                "error_code": None,
            }
        except ProseQualityQualificationBlocked as error:
            result = {
                "status": "blocked",
                "qualified": False,
                "error_code": str(error),
            }
        print(json.dumps(result, ensure_ascii=False))
        return 0
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
    "DEFAULT_PRIVATE_QUALIFICATION_SUITE",
    "DEFAULT_QUALIFICATION_DESCRIPTOR",
    "DEFAULT_SUITE",
    "GATE_THRESHOLDS",
    "POLISHER_QUALIFICATION_GATES",
    "QUALITY_FOCI",
    "QUALITY_QUALIFICATION_GATES",
    "ProseQualityQualificationBlocked",
    "ProseQualitySuiteError",
    "canonical_hash",
    "load_prose_quality_dev_suite",
    "load_prose_quality_qualification_suite",
    "run_prose_quality_development_baseline",
]
