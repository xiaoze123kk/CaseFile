"""N4.5 B2 Rewrite development suite, qualification boundary, and Fake runner."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import rfc8785
from casefile_contracts import ProseConsensusReport
from pydantic import ValidationError

from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    FakeProseJudgeProvider,
    ProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_rewrite_supervisor import execute_bounded_prose_rewrite
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_COMPONENT_HASH,
    PROSE_REWRITER_MODEL_ID,
    PROSE_REWRITER_PROMPT_VERSION,
    FakeProseRewriterProvider,
    ProseRewriterProvider,
)
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    normalize_scene_rewrite_candidate,
    validate_prose_judge_report,
    validate_scene_render,
)

ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_SUITE: Final = ROOT / "fixtures/prose_rewrite_benchmark/v1/suite.json"
DEFAULT_ATTESTATION: Final = (
    ROOT / "fixtures/prose_rewrite_benchmark/v1/review-attestation.json"
)
DEFAULT_QUALIFICATION_DESCRIPTOR: Final = (
    Path(__file__).with_name("policies")
    / "prose-rewrite-qualification-v1-descriptor.json"
)
DEFAULT_PRIVATE_QUALIFICATION_SUITE: Final = (
    ROOT / "backend/var/benchmark/private/prose-rewrite/qualification-v1/suite.json"
)
PRIVATE_ROOT: Final = (ROOT / "backend/var/benchmark/private").resolve()
FAMILIES: Final = (
    "missing_required_beat",
    "modality_weakening",
    "premature_reveal",
    "pov_knowledge_violation",
    "location_time_drift",
    "causality_ordering_inversion",
    "major_hallucination",
    "multi_error_combination",
)
VARIANTS: Final = ("basic", "preservation_dense", "second_round")
GATE_THRESHOLDS: Final = {
    "final_rescue_min": 21,
    "preservation_task_min": 23,
    "new_critical_issue_max": 0,
    "extra_rewrite_call_max": 0,
    "protocol_failure_max": 0,
    "infrastructure_failure_max": 0,
}


class ProseRewriteSuiteError(RuntimeError):
    """A Rewrite suite, Gold review, or lineage binding is invalid."""


class ProseRewriteQualificationBlocked(ProseRewriteSuiteError):
    """The private qualification package is not independently ready."""


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def load_prose_rewrite_dev_suite(
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Load and fully rebuild the public 8x3 bad-prose development suite."""

    suite = _load_json(suite_path)
    attestation = _load_json(attestation_path)
    expected_identity = {
        "schema_id": "casefile.prose-rewrite-dev-suite.v1",
        "suite_id": "n4.5-b2-rewrite-public-development-v1",
        "suite_role": "development",
        "task_count": 24,
        "council_policy_id": FIDELITY_ONLY_POLICY.policy_id,
        "council_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "rewriter_prompt_version": PROSE_REWRITER_PROMPT_VERSION,
        "rewriter_component_hash": PROSE_REWRITER_COMPONENT_HASH,
        "gate_thresholds": GATE_THRESHOLDS,
        "qualification": {
            "qualified": False,
            "qualification_eligible": False,
            "development_baseline": True,
        },
    }
    for key, value in expected_identity.items():
        if suite.get(key) != value:
            raise ProseRewriteSuiteError(f"prose_rewrite_suite_{key}_invalid")
    if tuple(suite.get("defect_families", ())) != FAMILIES or tuple(
        suite.get("variants", ())
    ) != VARIANTS:
        raise ProseRewriteSuiteError("prose_rewrite_suite_matrix_invalid")
    _validate_self_hash(suite, "suite_hash", "prose_rewrite_suite_hash_invalid")
    _validate_self_hash(
        attestation, "attestation_hash", "prose_rewrite_attestation_hash_invalid"
    )
    if attestation != {
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
        "attestation_hash": attestation["attestation_hash"],
    }:
        raise ProseRewriteSuiteError("prose_rewrite_attestation_invalid")
    descriptors = suite.get("tasks")
    if not isinstance(descriptors, list) or len(descriptors) != 24:
        raise ProseRewriteSuiteError("prose_rewrite_suite_cardinality_invalid")
    tasks = [_load_dev_task(item) for item in descriptors]
    distribution = Counter(
        (task["descriptor"]["defect_family"], task["descriptor"]["variant"])
        for task in tasks
    )
    expected = Counter({(family, variant): 1 for family in FAMILIES for variant in VARIANTS})
    if distribution != expected:
        raise ProseRewriteSuiteError("prose_rewrite_suite_distribution_invalid")
    ids = [task["descriptor"]["task_id"] for task in tasks]
    fingerprints = [task["descriptor"]["input_fingerprint"] for task in tasks]
    initial_hashes = [canonical_hash(task["asset"]["initial_render"]) for task in tasks]
    if (
        len(set(ids)) != 24
        or len(set(fingerprints)) != 24
        or len(set(initial_hashes)) != 24
    ):
        raise ProseRewriteSuiteError("prose_rewrite_task_identity_duplicate")
    return {"suite": suite, "attestation": attestation, "tasks": tasks}


def load_prose_rewrite_qualification_suite(
    suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
) -> dict[str, Any]:
    """Fail closed until a private 24-task package has independent review."""

    descriptor = _load_json(descriptor_path)
    if descriptor.get("schema_id") != "casefile.prose-rewrite-qualification-descriptor.v1":
        raise ProseRewriteSuiteError("prose_rewrite_qualification_descriptor_invalid")
    _validate_self_hash(
        descriptor,
        "descriptor_hash",
        "prose_rewrite_qualification_descriptor_hash_invalid",
    )
    if (
        descriptor.get("suite_id") != "n4.5-b2-rewrite-private-qualification-v1"
        or descriptor.get("task_count") != 24
        or descriptor.get("defect_distribution") != {family: 3 for family in FAMILIES}
        or descriptor.get("variant_distribution") != {variant: 8 for variant in VARIANTS}
        or descriptor.get("gate_thresholds") != GATE_THRESHOLDS
        or descriptor.get("loader_version") != "prose-rewrite-suite-loader-v1"
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_descriptor_invalid")
    if (
        descriptor.get("review_status") != "independently_reviewed"
        or descriptor.get("qualification_eligible") is not True
        or descriptor.get("review_attestation_hash") is None
    ):
        raise ProseRewriteQualificationBlocked(
            "prose_rewrite_qualification_independent_review_pending"
        )
    resolved = suite_path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT) or not resolved.is_file():
        raise ProseRewriteSuiteError("prose_rewrite_qualification_private_path_invalid")
    suite = _load_json(resolved)
    if canonical_hash(suite) != descriptor.get("private_suite_hash"):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_suite_hash_invalid")
    if (
        suite.get("schema_id") != "casefile.prose-rewrite-qualification-suite.v1"
        or suite.get("suite_id") != descriptor["suite_id"]
        or suite.get("suite_role") != "qualification"
        or suite.get("task_count") != 24
        or tuple(suite.get("defect_families", ())) != FAMILIES
        or tuple(suite.get("variants", ())) != VARIANTS
        or suite.get("gate_thresholds") != GATE_THRESHOLDS
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_suite_invalid")
    _validate_self_hash(
        suite, "suite_hash", "prose_rewrite_qualification_suite_self_hash_invalid"
    )
    author = _load_json(resolved.parent / "author-attestation.json")
    reviewer = _load_json(resolved.parent / "reviewer-attestation.json")
    if (
        canonical_hash(author) != descriptor.get("author_attestation_hash")
        or author.get("role") != "author"
        or author.get("authored_task_count") != 24
        or author.get("independent_review_completed") is not True
        or author.get("unresolved_findings") != []
        or reviewer.get("role") != "reviewer"
        or canonical_hash(reviewer) != descriptor["review_attestation_hash"]
        or reviewer.get("reviewer_independence") is not True
        or reviewer.get("reviewed_task_count") != 24
        or reviewer.get("unresolved_findings") != []
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_review_invalid")
    descriptors = suite.get("tasks")
    if not isinstance(descriptors, list) or len(descriptors) != 24:
        raise ProseRewriteSuiteError("prose_rewrite_qualification_cardinality_invalid")
    tasks = [
        _load_qualification_task(item, package_root=resolved.parent)
        for item in descriptors
    ]
    distribution = Counter(task["descriptor"]["defect_family"] for task in tasks)
    variants = Counter(task["descriptor"]["variant"] for task in tasks)
    ids = [task["descriptor"]["task_id"] for task in tasks]
    fingerprints = [task["descriptor"]["input_fingerprint"] for task in tasks]
    if (
        distribution != Counter(descriptor["defect_distribution"])
        or variants != Counter(descriptor["variant_distribution"])
        or len(set(ids)) != 24
        or len(set(fingerprints)) != 24
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_distribution_invalid")
    return {
        "descriptor": descriptor,
        "suite": suite,
        "author": author,
        "reviewer": reviewer,
        "tasks": tasks,
    }


def run_prose_rewrite_development_baseline(
    *,
    rewriter_provider_factory: Callable[[dict[str, Any]], ProseRewriterProvider]
    | None = None,
    judge_provider_factory: Callable[[dict[str, Any]], ProseJudgeProvider] | None = None,
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run all 24 tasks once with fixed denominator and no qualification claim."""

    loaded = load_prose_rewrite_dev_suite(suite_path, attestation_path)
    rows = []
    failure_counts = Counter[str]()
    round_one_rescue = 0
    round_two_rescue = 0
    final_rescue = 0
    preservation_tasks = 0
    new_critical_issue_count = 0
    total_calls = 0
    usage = _empty_usage()
    total_latency_ms = 0
    for task in loaded["tasks"]:
        asset = task["asset"]
        rewriter = (
            rewriter_provider_factory(task)
            if rewriter_provider_factory
            else FakeProseRewriterProvider(
                candidates=tuple(asset["fake_rewrite_candidates"])
            )
        )
        judge = (
            judge_provider_factory(task)
            if judge_provider_factory
            else FakeProseJudgeProvider(judge_reports=tuple(task["judge_candidates"]))
        )
        execution = execute_bounded_prose_rewrite(
            rewriter,
            judge,
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=asset["profile"],
            checklist=task["checklist"],
            previous_scene_render=asset["previous_scene_render"],
            initial_render=asset["initial_render"],
            model_id=PROSE_REWRITER_MODEL_ID,
            api_key="fake",
            remaining_scene_call_budget=5,
        )
        total_calls += execution.model_call_count
        _collect_usage(execution, usage)
        total_latency_ms += _collect_latency(execution)
        final_consensus = (
            execution.rounds[-1].council.consensus if execution.rounds else None
        )
        final_by_id = {
            item["check_id"]: item["final_verdict"]
            for item in (final_consensus or {}).get("checks", [])
        }
        rescue_round = next(
            (
                item.round_index
                for item in execution.rounds
                if item.round_index > 0
                and item.council.consensus is not None
                and item.council.consensus["scene_verdict"] == "pass"
            ),
            None,
        )
        if rescue_round == 1:
            round_one_rescue += 1
        elif rescue_round == 2:
            round_two_rescue += 1
        if rescue_round is not None:
            final_rescue += 1
        original_removed = bool(final_by_id) and all(
            final_by_id.get(item) == "pass" for item in asset["original_issue_check_ids"]
        )
        preservation = bool(final_by_id) and all(
            final_by_id.get(item) == "pass" for item in asset["initial_passed_check_ids"]
        )
        if preservation:
            preservation_tasks += 1
        new_critical = sorted(
            {
                check_id
                for round_execution in execution.rounds[1:]
                for check_id in asset["critical_check_ids"]
                if check_id in set(asset["initial_passed_check_ids"])
                and _round_verdict(round_execution, check_id) != "pass"
            }
        )
        new_critical_issue_count += len(new_critical)
        if execution.status == "protocol_failed":
            failure_counts["protocol"] += 1
        elif execution.status == "inconclusive":
            failure_counts["infrastructure"] += 1
        elif execution.status == "semantic_rejected":
            failure_counts["semantic"] += 1
        rows.append(
            {
                "task_id": task["descriptor"]["task_id"],
                "defect_family": task["descriptor"]["defect_family"],
                "variant": task["descriptor"]["variant"],
                "input_fingerprint": task["descriptor"]["input_fingerprint"],
                "status": execution.status,
                "error_code": execution.error_code,
                "round_count": len(execution.rounds),
                "rewrite_count": execution.rewrite_count,
                "model_call_count": execution.model_call_count,
                "rescue_round": rescue_round,
                "original_issue_removed": original_removed,
                "preservation_passed": preservation,
                "new_critical_issue_check_ids": new_critical,
                "initial_render_hash": canonical_hash(asset["initial_render"]),
                "final_render_hash": (
                    canonical_hash(execution.final_render)
                    if execution.final_render is not None
                    else None
                ),
            }
        )
    extra_rewrite_calls = sum(max(0, row["rewrite_count"] - 2) for row in rows)
    protocol_failures = failure_counts["protocol"]
    infrastructure_failures = failure_counts["infrastructure"]
    development_gate = {
        "final_rescue": final_rescue >= GATE_THRESHOLDS["final_rescue_min"],
        "preservation": preservation_tasks
        >= GATE_THRESHOLDS["preservation_task_min"],
        "new_critical_issue": new_critical_issue_count
        <= GATE_THRESHOLDS["new_critical_issue_max"],
        "extra_rewrite_call": extra_rewrite_calls
        <= GATE_THRESHOLDS["extra_rewrite_call_max"],
        "protocol_failure": protocol_failures
        <= GATE_THRESHOLDS["protocol_failure_max"],
        "infrastructure_failure": infrastructure_failures
        <= GATE_THRESHOLDS["infrastructure_failure_max"],
    }
    report = {
        "schema_id": "casefile.prose-rewrite-development-report.v1",
        "suite_id": loaded["suite"]["suite_id"],
        "suite_hash": loaded["suite"]["suite_hash"],
        "attestation_hash": loaded["attestation"]["attestation_hash"],
        "status": "inconclusive" if infrastructure_failures else "completed",
        "development_baseline": True,
        "qualification_eligible": False,
        "qualified": False,
        "task_count": 24,
        "completed_task_count": 24,
        "council_policy_id": FIDELITY_ONLY_POLICY.policy_id,
        "rewriter_prompt_version": PROSE_REWRITER_PROMPT_VERSION,
        "round_one_rescue": {"passed": round_one_rescue, "total": 24},
        "round_two_incremental_rescue": {"passed": round_two_rescue, "total": 24},
        "final_rescue": {"passed": final_rescue, "total": 24},
        "preservation_tasks": {"passed": preservation_tasks, "total": 24},
        "new_critical_issue_count": new_critical_issue_count,
        "extra_rewrite_call_count": extra_rewrite_calls,
        "failure_counts": {
            "semantic": failure_counts["semantic"],
            "protocol": protocol_failures,
            "infrastructure": infrastructure_failures,
        },
        "model_call_count": total_calls,
        "usage": usage,
        "latency_ms": total_latency_ms,
        "development_gate": development_gate,
        "development_gate_passed": all(development_gate.values()),
        "rows": rows,
    }
    report["report_hash"] = canonical_hash(report)
    if output_dir is not None:
        _write_json(output_dir / "report.json", report)
    return report


def _load_dev_task(descriptor: Any) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseRewriteSuiteError("prose_rewrite_task_descriptor_invalid")
    _validate_self_hash(
        descriptor, "content_hash", "prose_rewrite_task_descriptor_hash_invalid"
    )
    if (
        descriptor.get("defect_family") not in FAMILIES
        or descriptor.get("variant") not in VARIANTS
        or descriptor.get("expected_rescue_round")
        != (2 if descriptor.get("variant") == "second_round" else 1)
    ):
        raise ProseRewriteSuiteError("prose_rewrite_task_distribution_invalid")
    source = _load_bound_json(descriptor.get("source_input"), "source_input")
    plan = _load_bound_json(descriptor.get("scene_plan"), "scene_plan")
    asset = _load_bound_json(descriptor.get("task_asset"), "task_asset")
    _validate_self_hash(asset, "content_hash", "prose_rewrite_task_asset_hash_invalid")
    if (
        asset.get("schema_id") != "casefile.prose-rewrite-dev-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
    ):
        raise ProseRewriteSuiteError("prose_rewrite_task_asset_identity_invalid")
    narrative = source.get("narrative_ir")
    profile = asset.get("profile")
    previous = asset.get("previous_scene_render")
    if not isinstance(narrative, dict) or not isinstance(profile, dict):
        raise ProseRewriteSuiteError("prose_rewrite_task_input_invalid")
    checklist = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id=str(descriptor.get("scene_id")),
        previous_scene_render=previous,
    )
    if descriptor.get("checklist_hash") != canonical_hash(checklist):
        raise ProseRewriteSuiteError("prose_rewrite_task_checklist_hash_invalid")
    initial_value = asset.get("initial_render")
    report_value = asset.get("initial_judge_report")
    if not isinstance(initial_value, dict) or not isinstance(report_value, dict):
        raise ProseRewriteSuiteError("prose_rewrite_task_review_invalid")
    initial = validate_scene_render(
        initial_value, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    if initial["stage"] != "writer" or initial["round"] != 0:
        raise ProseRewriteSuiteError("prose_rewrite_task_initial_render_invalid")
    initial_report = validate_prose_judge_report(
        report_value,
        checklist=checklist,
        render=initial,
        profile=profile,
    ).model_dump(mode="json")
    initial_consensus = _validate_consensus(
        asset.get("initial_consensus"), checklist, initial, initial_report, profile
    )
    all_ids = [item["check_id"] for item in checklist["checks"]]
    issue_ids = [
        item["check_id"]
        for item in initial_consensus["checks"]
        if item["final_verdict"] != "pass"
    ]
    passed_ids = [item for item in all_ids if item not in set(issue_ids)]
    if (
        not issue_ids
        or asset.get("original_issue_check_ids") != issue_ids
        or asset.get("initial_passed_check_ids") != passed_ids
        or not set(asset.get("critical_check_ids", ())) <= set(all_ids)
    ):
        raise ProseRewriteSuiteError("prose_rewrite_task_review_scope_invalid")
    candidates = asset.get("fake_rewrite_candidates")
    gold = asset.get("round_gold")
    expected_rounds = descriptor["expected_rescue_round"]
    if (
        not isinstance(candidates, list)
        or not isinstance(gold, list)
        or len(candidates) != expected_rounds
        or len(gold) != expected_rounds
    ):
        raise ProseRewriteSuiteError("prose_rewrite_task_rounds_invalid")
    current = initial
    round_renders = []
    judge_candidates = [_candidate_report(initial_report, initial)]
    for round_index, (candidate, round_gold) in enumerate(
        zip(candidates, gold, strict=True), start=1
    ):
        render = normalize_scene_rewrite_candidate(
            candidate,
            checklist=checklist,
            profile=profile,
            current_render=current,
            rewrite_round=round_index,
            component_input_hash=canonical_hash(
                {"fixture_validation": descriptor["task_id"], "round": round_index}
            ),
        ).model_dump(mode="json")
        report = {
            "schema_id": "compiler.prose-judge-report.v1",
            "role": "fidelity",
            "scene_id": checklist["scene_id"],
            "checklist_hash": canonical_json_sha256(checklist),
            "render_hash": canonical_json_sha256(render),
            "assessments": round_gold.get("assessments"),
        }
        validated = validate_prose_judge_report(
            report, checklist=checklist, render=render, profile=profile
        ).model_dump(mode="json")
        verdicts = [item["verdict"] for item in validated["assessments"]]
        scene_verdict = (
            "uncertain"
            if "uncertain" in verdicts
            else "fail"
            if "fail" in verdicts
            else "pass"
        )
        if (
            round_gold.get("round") != round_index
            or round_gold.get("scene_verdict") != scene_verdict
            or (round_index < expected_rounds and scene_verdict == "pass")
            or (round_index == expected_rounds and scene_verdict != "pass")
        ):
            raise ProseRewriteSuiteError("prose_rewrite_task_round_gold_invalid")
        judge_candidates.append(_candidate_report(validated, render))
        round_renders.append(render)
        current = render
    expected_fingerprint = canonical_hash(
        {
            "writer_input_fingerprint": canonical_hash(
                {
                    "scene_plan_hash": canonical_hash(plan),
                    "narrative_ir_hash": canonical_hash(narrative),
                    "profile_hash": canonical_hash(profile),
                    "previous_scene_render_hash": (
                        None if previous is None else canonical_hash(previous)
                    ),
                    "checklist_hash": canonical_hash(checklist),
                    "scene_id": descriptor["scene_id"],
                }
            ),
            "initial_render_hash": canonical_hash(initial),
            "initial_consensus_hash": canonical_hash(initial_consensus),
            "defect_family": descriptor["defect_family"],
            "variant": descriptor["variant"],
        }
    )
    if descriptor.get("input_fingerprint") != expected_fingerprint:
        raise ProseRewriteSuiteError("prose_rewrite_task_input_fingerprint_invalid")
    return {
        "descriptor": descriptor,
        "asset": asset,
        "scene_plan": plan,
        "narrative_ir": narrative,
        "checklist": checklist,
        "round_renders": round_renders,
        "judge_candidates": judge_candidates,
    }


def _load_qualification_task(
    descriptor: Any, *, package_root: Path
) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_task_invalid")
    _validate_self_hash(
        descriptor,
        "content_hash",
        "prose_rewrite_qualification_task_descriptor_hash_invalid",
    )
    if (
        descriptor.get("defect_family") not in FAMILIES
        or descriptor.get("variant") not in VARIANTS
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_task_invalid")
    source = _load_bound_json(descriptor.get("source_input"), "source_input")
    plan = _load_bound_json(descriptor.get("scene_plan"), "scene_plan")
    asset = _load_private_bound_json(
        descriptor.get("task_asset"), package_root=package_root
    )
    _validate_self_hash(
        asset,
        "content_hash",
        "prose_rewrite_qualification_task_asset_hash_invalid",
    )
    if (
        asset.get("schema_id") != "casefile.prose-rewrite-qualification-task.v1"
        or asset.get("task_id") != descriptor.get("task_id")
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_task_identity_invalid")
    narrative = source.get("narrative_ir")
    profile = asset.get("profile")
    previous = asset.get("previous_scene_render")
    if not isinstance(narrative, dict) or not isinstance(profile, dict):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_task_input_invalid")
    checklist = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id=str(descriptor.get("scene_id")),
        previous_scene_render=previous,
    )
    if descriptor.get("checklist_hash") != canonical_hash(checklist):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_checklist_hash_invalid")
    initial_value = asset.get("initial_render")
    report_value = asset.get("initial_judge_report")
    if not isinstance(initial_value, dict) or not isinstance(report_value, dict):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_review_invalid")
    initial = validate_scene_render(
        initial_value, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    if initial["stage"] != "writer" or initial["round"] != 0:
        raise ProseRewriteSuiteError("prose_rewrite_qualification_initial_render_invalid")
    report = validate_prose_judge_report(
        report_value,
        checklist=checklist,
        render=initial,
        profile=profile,
    ).model_dump(mode="json")
    consensus = _validate_consensus(
        asset.get("initial_consensus"), checklist, initial, report, profile
    )
    all_ids = [item["check_id"] for item in checklist["checks"]]
    issue_ids = [
        item["check_id"]
        for item in consensus["checks"]
        if item["final_verdict"] != "pass"
    ]
    passed_ids = [item for item in all_ids if item not in set(issue_ids)]
    if (
        not issue_ids
        or asset.get("original_issue_check_ids") != issue_ids
        or asset.get("initial_passed_check_ids") != passed_ids
        or not set(asset.get("critical_check_ids", ())) <= set(all_ids)
    ):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_review_scope_invalid")
    source_fingerprint = canonical_hash(
        {
            "scene_plan_hash": canonical_hash(plan),
            "narrative_ir_hash": canonical_hash(narrative),
            "profile_hash": canonical_hash(profile),
            "previous_scene_render_hash": (
                None if previous is None else canonical_hash(previous)
            ),
            "checklist_hash": canonical_hash(checklist),
            "scene_id": descriptor["scene_id"],
        }
    )
    expected_fingerprint = canonical_hash(
        {
            "source": source_fingerprint,
            "private_initial_render": canonical_hash(initial),
            "family": descriptor["defect_family"],
            "variant": descriptor["variant"],
        }
    )
    if descriptor.get("input_fingerprint") != expected_fingerprint:
        raise ProseRewriteSuiteError("prose_rewrite_qualification_fingerprint_invalid")
    return {
        "descriptor": descriptor,
        "asset": asset,
        "scene_plan": plan,
        "narrative_ir": narrative,
        "checklist": checklist,
    }


def _validate_consensus(
    value: Any,
    checklist: dict[str, Any],
    render: dict[str, Any],
    report: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    try:
        parsed = ProseConsensusReport.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        raise ProseRewriteSuiteError("prose_rewrite_task_consensus_invalid") from error
    rebuilt = execute_semantic_council(
        FakeProseJudgeProvider(judge_reports=(_candidate_report(report, render),)),
        checklist=checklist,
        render=render,
        profile=profile,
        policy=FIDELITY_ONLY_POLICY,
        model_id=PROSE_COUNCIL_MODEL_ID,
        api_key="fake",
    )
    if rebuilt.status != "completed" or parsed != rebuilt.consensus:
        raise ProseRewriteSuiteError("prose_rewrite_task_consensus_binding_invalid")
    return parsed


def _candidate_report(report: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    catalog = build_server_evidence_catalog(render)
    by_hash = {
        canonical_hash({key: value for key, value in item.items() if key != "evidence_id"}): item[
            "evidence_id"
        ]
        for item in catalog
    }
    try:
        assessments = [
            {
                "check_id": item["check_id"],
                "verdict": item["verdict"],
                "evidence_ids": [by_hash[canonical_hash(value)] for value in item["evidence"]],
                "rationale": item["rationale"],
            }
            for item in report["assessments"]
        ]
    except KeyError as error:
        raise ProseRewriteSuiteError("prose_rewrite_gold_evidence_catalog_mismatch") from error
    return {"schema_id": "compiler.prose-judge-candidate.v1", "assessments": assessments}


def _load_bound_json(binding: Any, label: str) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != {"path", "hash"}:
        raise ProseRewriteSuiteError(f"prose_rewrite_{label}_binding_invalid")
    raw_path = binding.get("path")
    if not isinstance(raw_path, str):
        raise ProseRewriteSuiteError(f"prose_rewrite_{label}_path_invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or "var" in relative.parts or "private" in relative.parts:
        raise ProseRewriteSuiteError(f"prose_rewrite_{label}_path_invalid")
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ProseRewriteSuiteError(f"prose_rewrite_{label}_path_invalid")
    value = _load_json(path)
    if binding.get("hash") != canonical_hash(value):
        raise ProseRewriteSuiteError(f"prose_rewrite_{label}_hash_invalid")
    return value


def _load_private_bound_json(binding: Any, *, package_root: Path) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != {"path", "hash"}:
        raise ProseRewriteSuiteError("prose_rewrite_qualification_asset_binding_invalid")
    raw_path = binding.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ProseRewriteSuiteError("prose_rewrite_qualification_asset_path_invalid")
    relative = Path(raw_path)
    path = (package_root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(package_root) or not path.is_file():
        raise ProseRewriteSuiteError("prose_rewrite_qualification_asset_path_invalid")
    value = _load_json(path)
    if binding.get("hash") != canonical_hash(value):
        raise ProseRewriteSuiteError("prose_rewrite_qualification_asset_hash_invalid")
    return value


def _validate_self_hash(value: dict[str, Any], key: str, error_code: str) -> None:
    content = {name: item for name, item in value.items() if name != key}
    if value.get(key) != canonical_hash(content):
        raise ProseRewriteSuiteError(error_code)


def _round_verdict(round_execution: Any, check_id: str) -> str | None:
    consensus = round_execution.council.consensus
    if consensus is None:
        return None
    return next(
        (
            item["final_verdict"]
            for item in consensus["checks"]
            if item["check_id"] == check_id
        ),
        None,
    )


def _collect_usage(execution: Any, total: dict[str, int]) -> None:
    for round_execution in execution.rounds:
        if round_execution.rewrite is not None and round_execution.rewrite.call is not None:
            _merge_usage(total, round_execution.rewrite.call.usage)
        for call in round_execution.council.calls:
            _merge_usage(total, call.usage)


def _collect_latency(execution: Any) -> int:
    return sum(
        call.latency_ms
        for round_execution in execution.rounds
        for call in (
            *(
                (round_execution.rewrite.call,)
                if round_execution.rewrite is not None
                and round_execution.rewrite.call is not None
                else ()
            ),
            *round_execution.council.calls,
        )
    )


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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProseRewriteSuiteError(f"prose_rewrite_json_invalid:{path.name}") from error
    if not isinstance(value, dict):
        raise ProseRewriteSuiteError(f"prose_rewrite_json_object_required:{path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
            load_prose_rewrite_qualification_suite(args.qualification_suite, args.descriptor)
            status = "ready"
            error_code = None
        except ProseRewriteQualificationBlocked as error:
            status = "blocked"
            error_code = str(error)
        print(
            json.dumps(
                {"status": status, "qualified": False, "error_code": error_code},
                ensure_ascii=False,
            )
        )
        return 0
    report = run_prose_rewrite_development_baseline(
        suite_path=args.suite,
        attestation_path=args.attestation,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "qualified": report["qualified"],
                "final_rescue": report["final_rescue"],
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
    "FAMILIES",
    "GATE_THRESHOLDS",
    "ProseRewriteQualificationBlocked",
    "ProseRewriteSuiteError",
    "VARIANTS",
    "canonical_hash",
    "load_prose_rewrite_dev_suite",
    "load_prose_rewrite_qualification_suite",
    "run_prose_rewrite_development_baseline",
]
