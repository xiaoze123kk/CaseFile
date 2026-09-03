"""Frozen B3 v4 public pointwise Quality and Delta development runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final

from casefile_contracts import ProseQualityAssessmentCandidate

from casefile.agent_runtime.prose_judge import build_server_evidence_catalog
from casefile.agent_runtime.prose_patch_polisher import (
    PROSE_PATCH_POLISHER_COMPONENT_HASH,
    PROSE_PATCH_POLISHER_MODEL_ID,
    PROSE_PATCH_POLISHER_PROMPT_VERSION,
)
from casefile.agent_runtime.prose_quality_assessor import (
    PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
    PROSE_QUALITY_ASSESSMENT_MODEL_ID,
    PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
    FakeProseQualityAssessmentProvider,
    ProseQualityAssessmentProvider,
    execute_quality_assessment,
)
from casefile.domain.narrative_compiler import (
    PROSE_EDIT_WINDOW_POLICY_HASH,
    PROSE_EDIT_WINDOW_POLICY_VERSION,
    PROSE_QUALITY_DELTA_POLICY_HASH,
    PROSE_QUALITY_DELTA_POLICY_VERSION,
    QUALITY_DIMENSIONS,
    canonical_json_sha256,
    resolve_quality_delta,
    validate_scene_render,
    validate_semantic_acceptance,
)

ROOT: Final = Path(__file__).resolve().parents[4]
PUBLIC_ROOT: Final = ROOT / "fixtures/prose_quality_benchmark/v4"
DEFAULT_SUITE: Final = PUBLIC_ROOT / "suite.json"
DEFAULT_ATTESTATION: Final = PUBLIC_ROOT / "review-attestation.json"
PRIVATE_ROOT: Final = ROOT / "backend/var/benchmark/private/prose-quality"
DEFAULT_PRIVATE_QUALIFICATION_SUITE: Final = PRIVATE_ROOT / "qualification-v4/suite.json"
DEFAULT_QUALIFICATION_DESCRIPTOR: Final = (
    ROOT
    / "backend/src/casefile/benchmark/policies/prose-quality-qualification-v4-descriptor.json"
)
PUBLIC_GATES: Final = {
    "severity_exact_min": 80,
    "delta_correct_min": 8,
    "five_dimension_coverage_min": 16,
    "evidence_valid_min": 16,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}
QUALITY_V4_QUALIFICATION_GATES: Final = {
    "severity_exact_min": 144,
    "delta_correct_min": 15,
    "five_dimension_coverage_min": 32,
    "evidence_valid_min": 32,
    "metamorphic_stability_min": 4,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}
POLISHER_V4_QUALIFICATION_GATES: Final = {
    "patch_contract_valid_min": 24,
    "outside_window_exact_min": 24,
    "preservation_pass_min": 24,
    "quality_non_loss_min": 24,
    "polished_accepted_min": 18,
    "critical_semantic_regression_max": 0,
    "rejected_exact_rollback_rate_min": 1.0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}


class ProseQualityV4SuiteError(RuntimeError):
    """The public v4 development package is incomplete or drifted."""


class ProseQualityV4QualificationBlocked(ProseQualityV4SuiteError):
    """The private v4 package has not completed its frozen review policy."""


def canonical_hash(value: Any) -> str:
    return canonical_json_sha256(value)


def load_prose_quality_v4_dev_suite(
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Load eight reviewed pointwise pairs and prove every frozen hash."""

    suite = _load_json(suite_path)
    attestation = _load_json(attestation_path)
    expected = {
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
    }
    for key, value in expected.items():
        if suite.get(key) != value:
            raise ProseQualityV4SuiteError(f"prose_quality_v4_suite_{key}_invalid")
    _validate_self_hash(suite, "suite_hash", "prose_quality_v4_suite_hash_invalid")
    _validate_self_hash(
        attestation,
        "attestation_hash",
        "prose_quality_v4_attestation_hash_invalid",
    )
    if attestation != {
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
        "attestation_hash": attestation["attestation_hash"],
    }:
        raise ProseQualityV4SuiteError("prose_quality_v4_attestation_invalid")
    descriptors = suite.get("tasks")
    if not isinstance(descriptors, list) or len(descriptors) != 8:
        raise ProseQualityV4SuiteError("prose_quality_v4_cardinality_invalid")
    tasks = [_load_task(item) for item in descriptors]
    if len({item["descriptor"]["task_id"] for item in tasks}) != 8:
        raise ProseQualityV4SuiteError("prose_quality_v4_task_ids_invalid")
    if len({item["descriptor"]["pair_fingerprint"] for item in tasks}) != 8:
        raise ProseQualityV4SuiteError("prose_quality_v4_fingerprints_invalid")
    return {"suite": suite, "attestation": attestation, "tasks": tasks}


def load_prose_quality_v4_qualification_suite(
    suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
) -> dict[str, Any]:
    """Load the reviewed private 16-pair and 24-patch v4 package."""

    descriptor = _load_json(descriptor_path)
    if descriptor.get("schema_id") != "casefile.prose-quality-qualification-descriptor.v2":
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_qualification_descriptor_invalid"
        )
    _validate_self_hash(
        descriptor,
        "descriptor_hash",
        "prose_quality_v4_qualification_descriptor_hash_invalid",
    )
    expected_descriptor = {
        "suite_id": "n4.5-b3-quality-polisher-private-qualification-v4",
        "quality_holdout_count": 16,
        "quality_assessment_count": 32,
        "polisher_task_count": 24,
        "metamorphic_neutral_count": 4,
        "quality_gate_thresholds": QUALITY_V4_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_V4_QUALIFICATION_GATES,
        "loader_version": "prose-quality-suite-loader-v4",
        "quality_model_id": PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        "generation_model_id": PROSE_PATCH_POLISHER_MODEL_ID,
        "quality_component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
        "polisher_component_hash": PROSE_PATCH_POLISHER_COMPONENT_HASH,
        "quality_prompt_version": PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
        "polisher_prompt_version": PROSE_PATCH_POLISHER_PROMPT_VERSION,
        "window_policy_version": PROSE_EDIT_WINDOW_POLICY_VERSION,
        "window_policy_hash": PROSE_EDIT_WINDOW_POLICY_HASH,
        "delta_policy_version": PROSE_QUALITY_DELTA_POLICY_VERSION,
        "delta_policy_hash": PROSE_QUALITY_DELTA_POLICY_HASH,
    }
    for key, value in expected_descriptor.items():
        if descriptor.get(key) != value:
            raise ProseQualityV4SuiteError(
                f"prose_quality_v4_qualification_descriptor_{key}_invalid"
            )
    public = load_prose_quality_v4_dev_suite()["suite"]
    if descriptor.get("public_development_suite_hash") != public["suite_hash"]:
        raise ProseQualityV4SuiteError("prose_quality_v4_public_suite_hash_invalid")
    if (
        descriptor.get("review_policy") != "codex-owner-accepted-review-v1"
        or descriptor.get("review_status") != "codex_reviewed"
        or descriptor.get("qualification_eligible") is not True
        or not descriptor.get("author_attestation_hash")
        or not descriptor.get("review_attestation_hash")
        or descriptor.get("prior_quality_fingerprint_overlap") != 0
        or descriptor.get("prior_polisher_fingerprint_overlap") != 0
    ):
        raise ProseQualityV4QualificationBlocked(
            "prose_quality_v4_qualification_review_pending"
        )
    resolved = suite_path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT.resolve()) or not resolved.is_file():
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_qualification_private_path_invalid"
        )
    suite = _load_json(resolved)
    if canonical_hash(suite) != descriptor.get("private_suite_hash"):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_qualification_suite_hash_invalid"
        )
    _validate_self_hash(
        suite,
        "suite_hash",
        "prose_quality_v4_qualification_suite_self_hash_invalid",
    )
    expected_suite = {
        "schema_id": "casefile.prose-quality-qualification-suite.v2",
        "suite_id": descriptor["suite_id"],
        "suite_role": "qualification",
        "quality_holdout_count": 16,
        "quality_assessment_count": 32,
        "polisher_task_count": 24,
        "metamorphic_neutral_count": 4,
        "quality_gate_thresholds": QUALITY_V4_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_V4_QUALIFICATION_GATES,
        "quality_model_id": PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        "generation_model_id": PROSE_PATCH_POLISHER_MODEL_ID,
        "quality_component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
        "polisher_component_hash": PROSE_PATCH_POLISHER_COMPONENT_HASH,
        "quality_prompt_version": PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
        "polisher_prompt_version": PROSE_PATCH_POLISHER_PROMPT_VERSION,
        "window_policy_version": PROSE_EDIT_WINDOW_POLICY_VERSION,
        "window_policy_hash": PROSE_EDIT_WINDOW_POLICY_HASH,
        "delta_policy_version": PROSE_QUALITY_DELTA_POLICY_VERSION,
        "delta_policy_hash": PROSE_QUALITY_DELTA_POLICY_HASH,
    }
    for key, value in expected_suite.items():
        if suite.get(key) != value:
            raise ProseQualityV4SuiteError(
                f"prose_quality_v4_qualification_suite_{key}_invalid"
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
        or author.get("review_completed") is not True
        or author.get("owner_accepted_codex_review") is not True
        or author.get("unresolved_findings") != []
        or canonical_hash(reviewer) != descriptor["review_attestation_hash"]
        or reviewer.get("role") != "reviewer"
        or reviewer.get("suite_id") != suite["suite_id"]
        or reviewer.get("suite_hash") != suite["suite_hash"]
        or reviewer.get("reviewer") != "Codex"
        or reviewer.get("reviewer_independence") is not False
        or reviewer.get("owner_acceptance") is not True
        or reviewer.get("reviewed_quality_count") != 16
        or reviewer.get("reviewed_polisher_count") != 24
        or reviewer.get("unresolved_findings") != []
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_qualification_review_invalid"
        )
    quality_descriptors = suite.get("quality_tasks")
    polisher_descriptors = suite.get("polisher_tasks")
    if (
        not isinstance(quality_descriptors, list)
        or len(quality_descriptors) != 16
        or not isinstance(polisher_descriptors, list)
        or len(polisher_descriptors) != 24
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_qualification_cardinality_invalid"
        )
    quality_tasks = [
        _load_private_quality_task(item, package_root) for item in quality_descriptors
    ]
    polisher_tasks = [
        _load_private_polisher_task(item, package_root)
        for item in polisher_descriptors
    ]
    if sum(task["asset"]["metamorphic_neutral"] for task in quality_tasks) != 4:
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_metamorphic_distribution_invalid"
        )
    return {
        "descriptor": descriptor,
        "suite": suite,
        "author_attestation": author,
        "review_attestation": reviewer,
        "quality_tasks": quality_tasks,
        "polisher_tasks": polisher_tasks,
    }


def run_prose_quality_v4_dev_suite(
    provider: ProseQualityAssessmentProvider | None = None,
) -> dict[str, Any]:
    """Run all 16 anonymous assessments and eight deterministic Deltas."""

    package = load_prose_quality_v4_dev_suite()
    if provider is None:
        candidates = tuple(
            task["asset"]["gold"][role]
            for task in package["tasks"]
            for role in ("render_a", "render_b")
        )
        provider = FakeProseQualityAssessmentProvider(candidates=candidates)
    rows = []
    for task in package["tasks"]:
        asset = task["asset"]
        executions = []
        for role in ("render_a", "render_b"):
            suffix = role[-1]
            executions.append(
                execute_quality_assessment(
                    provider,
                    checklist=asset["checklist"],
                    render=asset[role],
                    profile=asset["profile"],
                    semantic_consensus=asset[f"semantic_consensus_{suffix}"],
                    model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
                    api_key="fake",
                )
            )
        status = next(
            (execution.status for execution in executions if execution.status != "completed"),
            "completed",
        )
        assessments = [execution.assessment for execution in executions]
        severity_correct = 0
        delta_correct = False
        delta_hash = None
        if status == "completed" and all(value is not None for value in assessments):
            for role, assessment in zip(
                ("render_a", "render_b"), assessments, strict=True
            ):
                expected = asset["gold"][role]["dimensions"]
                actual = assessment["dimensions"]  # type: ignore[index]
                severity_correct += sum(
                    left["severity"] == right["severity"]
                    for left, right in zip(actual, expected, strict=True)
                )
            delta = resolve_quality_delta(
                original_assessment=assessments[0],  # type: ignore[arg-type]
                polished_assessment=assessments[1],  # type: ignore[arg-type]
                checklist=asset["checklist"],
                original_render=asset["render_a"],
                polished_render=asset["render_b"],
                profile=asset["profile"],
                original_semantic_consensus=asset["semantic_consensus_a"],
                preservation_consensus=asset["semantic_consensus_b"],
            ).model_dump(mode="json")
            delta_correct = (
                delta["accept_polished"] == asset["gold"]["accept_polished"]
            )
            delta_hash = canonical_hash(delta)
        rows.append(
            {
                "task_id": task["descriptor"]["task_id"],
                "status": status,
                "render_hashes": [
                    canonical_hash(asset["render_a"]),
                    canonical_hash(asset["render_b"]),
                ],
                "assessment_hashes": [
                    canonical_hash(value) if value is not None else None
                    for value in assessments
                ],
                "severity_correct": severity_correct,
                "delta_correct": delta_correct,
                "delta_hash": delta_hash,
            }
        )
    severity = sum(row["severity_correct"] for row in rows)
    delta_correct_count = sum(row["delta_correct"] for row in rows)
    completed = sum(row["status"] == "completed" for row in rows)
    protocol = sum(row["status"] == "protocol_failed" for row in rows)
    infrastructure = sum(row["status"] == "inconclusive" for row in rows)
    gates = {
        "severity_exact": severity >= PUBLIC_GATES["severity_exact_min"],
        "delta_correct": delta_correct_count >= PUBLIC_GATES["delta_correct_min"],
        "five_dimension_coverage": completed * 2
        >= PUBLIC_GATES["five_dimension_coverage_min"],
        "evidence_valid": completed * 2 >= PUBLIC_GATES["evidence_valid_min"],
        "protocol_failure": protocol <= PUBLIC_GATES["protocol_failure_max"],
        "infrastructure_failure": infrastructure
        <= PUBLIC_GATES["infrastructure_failure_max"],
    }
    report = {
        "schema_id": "casefile.prose-quality-pointwise-dev-report.v1",
        "suite_hash": package["suite"]["suite_hash"],
        "qualified": False,
        "qualification_eligible": False,
        "task_count": 8,
        "assessment_count": 16,
        "completed_task_count": completed,
        "severity_exact": {"correct": severity, "total": 80},
        "delta_accuracy": {"correct": delta_correct_count, "total": 8},
        "protocol_failures": protocol,
        "infrastructure_failures": infrastructure,
        "gates": gates,
        "development_passed": all(gates.values()),
        "rows": rows,
    }
    report["report_hash"] = canonical_hash(report)
    return report


def _load_task(descriptor: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseQualityV4SuiteError("prose_quality_v4_descriptor_invalid")
    path_value = descriptor.get("task_asset", {}).get("path")
    if not isinstance(path_value, str):
        raise ProseQualityV4SuiteError("prose_quality_v4_task_path_invalid")
    path = (ROOT / path_value).resolve()
    if not path.is_relative_to(PUBLIC_ROOT.resolve()):
        raise ProseQualityV4SuiteError("prose_quality_v4_task_path_invalid")
    asset = _load_json(path)
    if canonical_hash(asset) != descriptor.get("task_asset", {}).get("hash"):
        raise ProseQualityV4SuiteError("prose_quality_v4_task_hash_invalid")
    if asset.get("content_hash") != canonical_hash(
        {key: value for key, value in asset.items() if key != "content_hash"}
    ):
        raise ProseQualityV4SuiteError("prose_quality_v4_task_content_hash_invalid")
    if asset.get("task_id") != descriptor.get("task_id"):
        raise ProseQualityV4SuiteError("prose_quality_v4_task_id_invalid")
    for suffix in ("a", "b"):
        render = validate_scene_render(
            asset[f"render_{suffix}"],
            checklist=asset["checklist"],
            profile=asset["profile"],
        ).model_dump(mode="json")
        validate_semantic_acceptance(
            asset[f"semantic_consensus_{suffix}"],
            checklist=asset["checklist"],
            render=render,
            profile=asset["profile"],
        )
        gold = asset["gold"][f"render_{suffix}"]
        if [item["dimension"] for item in gold["dimensions"]] != list(
            QUALITY_DIMENSIONS
        ):
            raise ProseQualityV4SuiteError("prose_quality_v4_gold_dimensions_invalid")
    fingerprint = canonical_hash(
        {
            "render_a_hash": canonical_hash(asset["render_a"]),
            "render_b_hash": canonical_hash(asset["render_b"]),
            "gold": asset["gold"],
        }
    )
    if fingerprint != descriptor.get("pair_fingerprint"):
        raise ProseQualityV4SuiteError("prose_quality_v4_pair_fingerprint_invalid")
    return {"descriptor": descriptor, "asset": asset}


def _load_private_quality_task(
    descriptor: dict[str, Any], package_root: Path
) -> dict[str, Any]:
    asset = _load_private_asset(descriptor, package_root)
    if (
        asset.get("schema_id")
        != "casefile.prose-quality-pointwise-qualification-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
        or asset.get("focus") != descriptor.get("focus")
        or asset.get("metamorphic_neutral")
        != descriptor.get("metamorphic_neutral")
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_quality_identity_invalid"
        )
    renders = {}
    for suffix in ("a", "b"):
        render = validate_scene_render(
            asset[f"render_{suffix}"],
            checklist=asset["checklist"],
            profile=asset["profile"],
        ).model_dump(mode="json")
        validate_semantic_acceptance(
            asset[f"semantic_consensus_{suffix}"],
            checklist=asset["checklist"],
            render=render,
            profile=asset["profile"],
        )
        candidate = ProseQualityAssessmentCandidate.model_validate(
            asset["gold"][f"render_{suffix}"]
        ).model_dump(mode="json")
        if [item["dimension"] for item in candidate["dimensions"]] != list(
            QUALITY_DIMENSIONS
        ):
            raise ProseQualityV4SuiteError(
                "prose_quality_v4_private_quality_gold_invalid"
            )
        catalog_ids = {
            item["evidence_id"] for item in build_server_evidence_catalog(render)
        }
        if any(
            evidence_id not in catalog_ids
            for item in candidate["dimensions"]
            for evidence_id in item["evidence_ids"]
        ):
            raise ProseQualityV4SuiteError(
                "prose_quality_v4_private_quality_gold_evidence_invalid"
            )
        renders[suffix] = render
    if (
        renders["b"]["stage"] != "polished"
        or renders["b"]["previous_render_hash"] != canonical_hash(renders["a"])
        or renders["a"]["stage"] not in {"writer", "rewrite_1", "rewrite_2"}
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_quality_lineage_invalid"
        )
    fingerprint = canonical_hash(
        {
            "render_a_hash": canonical_hash(renders["a"]),
            "render_b_hash": canonical_hash(renders["b"]),
            "gold": asset["gold"],
        }
    )
    if fingerprint != descriptor.get("pair_fingerprint"):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_quality_fingerprint_invalid"
        )
    return {"descriptor": descriptor, "asset": asset}


def _load_private_polisher_task(
    descriptor: dict[str, Any], package_root: Path
) -> dict[str, Any]:
    asset = _load_private_asset(descriptor, package_root)
    if (
        asset.get("schema_id") != "casefile.prose-patch-polisher-qualification-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
        or asset.get("focus") != descriptor.get("focus")
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_polisher_identity_invalid"
        )
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
    if render["stage"] not in {"writer", "rewrite_1", "rewrite_2"}:
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_polisher_stage_invalid"
        )
    fingerprint = canonical_hash(
        {
            "render": canonical_hash(render),
            "consensus": canonical_hash(asset["semantic_consensus"]),
            "focus": asset["focus"],
            "target_dimensions": asset["target_dimensions"],
        }
    )
    if fingerprint != descriptor.get("input_fingerprint"):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_polisher_fingerprint_invalid"
        )
    return {"descriptor": descriptor, "asset": asset}


def _load_private_asset(
    descriptor: dict[str, Any], package_root: Path
) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_descriptor_invalid"
        )
    asset_ref = descriptor.get("task_asset")
    path_value = asset_ref.get("path") if isinstance(asset_ref, dict) else None
    if not isinstance(path_value, str):
        raise ProseQualityV4SuiteError("prose_quality_v4_private_path_invalid")
    assert isinstance(asset_ref, dict)
    path = (package_root / path_value).resolve()
    if not path.is_relative_to(package_root.resolve()):
        raise ProseQualityV4SuiteError("prose_quality_v4_private_path_invalid")
    asset = _load_json(path)
    if canonical_hash(asset) != asset_ref.get("hash"):
        raise ProseQualityV4SuiteError("prose_quality_v4_private_asset_hash_invalid")
    if asset.get("content_hash") != canonical_hash(
        {key: value for key, value in asset.items() if key != "content_hash"}
    ):
        raise ProseQualityV4SuiteError(
            "prose_quality_v4_private_content_hash_invalid"
        )
    return asset


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProseQualityV4SuiteError("prose_quality_v4_json_invalid") from error
    if not isinstance(value, dict):
        raise ProseQualityV4SuiteError("prose_quality_v4_json_invalid")
    return value


def _validate_self_hash(value: dict[str, Any], key: str, error_code: str) -> None:
    expected = value.get(key)
    without_hash = {name: item for name, item in value.items() if name != key}
    if expected != canonical_hash(without_hash):
        raise ProseQualityV4SuiteError(error_code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("fake", "qualification-check"), default="fake"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "qualification-check":
        package = load_prose_quality_v4_qualification_suite()
        report = {
            "schema_id": "casefile.prose-quality-qualification-check.v2",
            "qualification_eligible": True,
            "descriptor_hash": package["descriptor"]["descriptor_hash"],
            "private_suite_hash": canonical_hash(package["suite"]),
            "quality_holdout_count": len(package["quality_tasks"]),
            "polisher_task_count": len(package["polisher_tasks"]),
        }
        passed = True
    else:
        report = run_prose_quality_v4_dev_suite()
        passed = bool(report["development_passed"])
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ATTESTATION",
    "DEFAULT_PRIVATE_QUALIFICATION_SUITE",
    "DEFAULT_QUALIFICATION_DESCRIPTOR",
    "DEFAULT_SUITE",
    "POLISHER_V4_QUALIFICATION_GATES",
    "PUBLIC_GATES",
    "QUALITY_V4_QUALIFICATION_GATES",
    "ProseQualityV4QualificationBlocked",
    "ProseQualityV4SuiteError",
    "load_prose_quality_v4_dev_suite",
    "load_prose_quality_v4_qualification_suite",
    "run_prose_quality_v4_dev_suite",
]
