"""N4.5-02 public B0 semantic Council development ablation."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import rfc8785

from casefile.agent_runtime.prompt_repository import load_prompt, validate_prompt_repository
from casefile.agent_runtime.prose_judge import (
    PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
    PROSE_COUNCIL_MODEL_ID,
    PROSE_COUNCIL_POLICIES,
    PROSE_JUDGE_REQUEST_PROTOCOL,
    PROSE_JUDGE_SCHEMA_HASH,
    DeepSeekProseJudgeProvider,
    FakeProseJudgeProvider,
    ProseCouncilExecution,
    ProseCouncilPolicy,
    ProseJudgeProvider,
    execute_semantic_council,
)
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    validate_prose_judge_report,
    validate_scene_render,
)

ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_SUITE: Final = ROOT / "fixtures/prose_judge_benchmark/v1/suite.json"
DEFAULT_ATTESTATION: Final = (
    ROOT / "fixtures/prose_judge_benchmark/v1/review-attestation.json"
)
ABILITIES: Final = (
    "beat_realization",
    "event_modality",
    "reveal_control",
    "pov_knowledge",
    "location_time",
    "causality_ordering",
    "major_hallucination",
    "implicit_semantics",
)
VARIANTS: Final = ("explicit_valid", "implicit_valid", "adversarial_invalid")
SAMPLE_KINDS: Final = ("base", "paraphrase", "mutation")


class ProseJudgeSuiteError(RuntimeError):
    """The public development suite or attestation is invalid."""


def canonical_hash(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProseJudgeSuiteError(f"prose_judge_json_object_required:{path}")
    return value


def load_prose_judge_dev_suite(
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Load and prove distribution, hashes, contracts, Gold and review closure."""

    suite = _load_json(suite_path)
    attestation = _load_json(attestation_path)
    if suite.get("schema_id") != "casefile.prose-judge-dev-suite.v1":
        raise ProseJudgeSuiteError("prose_judge_suite_schema_invalid")
    expected_suite_hash = suite.get("suite_hash")
    if expected_suite_hash != canonical_hash(
        {key: value for key, value in suite.items() if key != "suite_hash"}
    ):
        raise ProseJudgeSuiteError("prose_judge_suite_hash_invalid")
    if attestation.get("attestation_hash") != canonical_hash(
        {key: value for key, value in attestation.items() if key != "attestation_hash"}
    ):
        raise ProseJudgeSuiteError("prose_judge_attestation_hash_invalid")
    if (
        attestation.get("suite_hash") != expected_suite_hash
        or attestation.get("passes") != ["semantic", "adversarial"]
        or attestation.get("reviewer_independence") is not False
        or attestation.get("holdout_qualification") is not False
        or attestation.get("unresolved_findings") != []
    ):
        raise ProseJudgeSuiteError("prose_judge_attestation_invalid")
    inputs = suite.get("inputs")
    tasks = suite.get("tasks")
    if not isinstance(inputs, dict) or not isinstance(tasks, list) or len(tasks) != 24:
        raise ProseJudgeSuiteError("prose_judge_suite_cardinality_invalid")
    plan = _load_json(ROOT / str(inputs["scene_plan"]))
    narrative_input = _load_json(ROOT / str(inputs["narrative_input"]))
    profile = _load_json(ROOT / str(inputs["profile"]))
    checklist = _load_json(ROOT / str(inputs["checklist"]))
    rebuilt = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative_input["narrative_ir"],
        profile=profile,
        scene_id=checklist["scene_id"],
    )
    if rebuilt != checklist:
        raise ProseJudgeSuiteError("prose_judge_checklist_not_exactly_rebuilt")
    distribution = {(ability, variant): 0 for ability in ABILITIES for variant in VARIANTS}
    normalized_texts: set[str] = set()
    task_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ProseJudgeSuiteError("prose_judge_task_invalid")
        task_hash = task.get("content_hash")
        if task_hash != canonical_hash(
            {key: value for key, value in task.items() if key != "content_hash"}
        ):
            raise ProseJudgeSuiteError("prose_judge_task_hash_invalid")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or task_id in task_ids:
            raise ProseJudgeSuiteError("prose_judge_task_id_invalid")
        task_ids.add(task_id)
        key = (task.get("ability"), task.get("variant"))
        if key not in distribution:
            raise ProseJudgeSuiteError("prose_judge_task_distribution_invalid")
        distribution[key] += 1
        critical = task.get("critical")
        if critical is not (task["variant"] == "adversarial_invalid"):
            raise ProseJudgeSuiteError("prose_judge_critical_marking_invalid")
        review = task.get("review")
        if not isinstance(review, dict) or (
            review.get("semantic_pass"),
            review.get("adversarial_pass"),
            review.get("unresolved_findings"),
        ) != ("accepted", "accepted", []):
            raise ProseJudgeSuiteError("prose_judge_task_review_invalid")
        samples = task.get("samples")
        if not isinstance(samples, dict) or tuple(samples) != SAMPLE_KINDS:
            raise ProseJudgeSuiteError("prose_judge_sample_set_invalid")
        gold_vectors: dict[str, tuple[str, ...]] = {}
        for sample_kind in SAMPLE_KINDS:
            sample = samples[sample_kind]
            render = sample["render"]
            gold = sample["gold"]
            validate_scene_render(render, checklist=checklist, profile=profile)
            text = "\n".join(block["text"] for block in render["blocks"])
            normalized = " ".join(text.split()).casefold()
            if normalized in normalized_texts:
                raise ProseJudgeSuiteError("prose_judge_render_text_duplicate")
            normalized_texts.add(normalized)
            report = _gold_report(gold, checklist, render, role="fidelity")
            validate_prose_judge_report(
                report, checklist=checklist, render=render, profile=profile
            )
            vector = tuple(item["verdict"] for item in gold["assessments"])
            gold_vectors[sample_kind] = vector
            expected_scene = (
                "fail"
                if "fail" in vector
                else "uncertain" if "uncertain" in vector else "pass"
            )
            if gold["scene_verdict"] != expected_scene:
                raise ProseJudgeSuiteError("prose_judge_gold_scene_verdict_invalid")
        if gold_vectors["base"] != gold_vectors["paraphrase"]:
            raise ProseJudgeSuiteError("prose_judge_paraphrase_gold_drift")
        changed_ids = task.get("expected_changed_check_ids")
        actual_changed = [
            checklist["checks"][index]["check_id"]
            for index, (base, mutation) in enumerate(
                zip(gold_vectors["base"], gold_vectors["mutation"], strict=True)
            )
            if base != mutation
        ]
        if not actual_changed or actual_changed != changed_ids:
            raise ProseJudgeSuiteError("prose_judge_mutation_gold_invalid")
        if (
            samples["base"]["gold"]["scene_verdict"]
            == samples["mutation"]["gold"]["scene_verdict"]
        ):
            raise ProseJudgeSuiteError("prose_judge_mutation_scene_unchanged")
    if any(value != 1 for value in distribution.values()) or len(normalized_texts) != 72:
        raise ProseJudgeSuiteError("prose_judge_suite_distribution_invalid")
    return {
        "suite": suite,
        "attestation": attestation,
        "scene_plan": plan,
        "narrative_ir": narrative_input["narrative_ir"],
        "profile": profile,
        "checklist": checklist,
    }


def _gold_report(
    gold: dict[str, Any],
    checklist: dict[str, Any],
    render: dict[str, Any],
    *,
    role: str,
    assessments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_id": "compiler.prose-judge-report.v1",
        "role": role,
        "scene_id": checklist["scene_id"],
        "checklist_hash": canonical_json_sha256(checklist),
        "render_hash": canonical_json_sha256(render),
        "assessments": deepcopy(
            assessments if assessments is not None else gold["assessments"]
        ),
    }


def oracle_provider_for_sample(
    sample: dict[str, Any], policy: ProseCouncilPolicy
) -> FakeProseJudgeProvider:
    """Return explicit Gold-backed reports; this never proves model capability."""

    checklist = load_prose_judge_dev_suite()["checklist"]
    render = sample["render"]
    gold = sample["gold"]
    reports = tuple(
        _gold_report(gold, checklist, render, role=role) for role in policy.roles
    )
    return FakeProseJudgeProvider(judge_reports=reports)


def run_development_ablation(
    *,
    provider_factory: Callable[[dict[str, Any], ProseCouncilPolicy], ProseJudgeProvider],
    api_key: str,
    mode: str,
    output_dir: Path | None = None,
    suite_path: Path = DEFAULT_SUITE,
    attestation_path: Path = DEFAULT_ATTESTATION,
) -> dict[str, Any]:
    """Run all three frozen policies in order over base/paraphrase/mutation."""

    loaded = load_prose_judge_dev_suite(suite_path, attestation_path)
    validate_prompt_repository()
    suite = loaded["suite"]
    checklist = loaded["checklist"]
    profile = loaded["profile"]
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=False)
    policy_reports = []
    raw_bundle: list[dict[str, Any]] = []
    inconclusive = False
    for policy in PROSE_COUNCIL_POLICIES:
        results = []
        for task in suite["tasks"]:
            for sample_kind in SAMPLE_KINDS:
                sample = task["samples"][sample_kind]
                provider = provider_factory(sample, policy)
                execution = execute_semantic_council(
                    provider,
                    checklist=checklist,
                    render=sample["render"],
                    profile=profile,
                    policy=policy,
                    model_id=PROSE_COUNCIL_MODEL_ID,
                    api_key=api_key,
                )
                item = _execution_result(task, sample_kind, sample, execution)
                results.append(item)
                raw_bundle.extend(item["calls"])
                if output_dir is not None:
                    _write_json(
                        output_dir
                        / "calls"
                        / policy.policy_id
                        / f"{task['task_id']}__{sample_kind}.json",
                        item,
                    )
                if execution.status == "inconclusive":
                    inconclusive = True
                    break
            if inconclusive:
                break
        policy_reports.append(_policy_metrics(policy, suite["tasks"], results))
        if inconclusive:
            break
    selected = _select_policy(policy_reports) if not inconclusive else None
    lineage = _lineage(loaded, mode)
    report = {
        "schema_id": "casefile.prose-judge-dev-ablation.v1",
        "attempt_id": output_dir.name if output_dir else "fake-gate",
        "status": "inconclusive" if inconclusive else "completed",
        "mode": mode,
        "oracle_backed": mode == "fake",
        "qualification_eligible": False,
        "holdout_eligible": selected is not None and mode == "live",
        "selected_policy_id": selected,
        "lineage": lineage,
        "policies": policy_reports,
        "raw_call_bundle_hash": canonical_hash(raw_bundle),
        "call_count": len(raw_bundle),
    }
    report["report_hash"] = canonical_hash(report)
    if output_dir is not None:
        _write_json(output_dir / "raw-call-bundle.json", {"calls": raw_bundle})
        _write_json(output_dir / "report.json", report)
    return report


def _execution_result(
    task: dict[str, Any],
    sample_kind: str,
    sample: dict[str, Any],
    execution: ProseCouncilExecution,
) -> dict[str, Any]:
    actual_vector = (
        [item["final_verdict"] for item in execution.consensus["checks"]]
        if execution.consensus
        else []
    )
    gold_vector = [item["verdict"] for item in sample["gold"]["assessments"]]
    calls = []
    for call in execution.calls:
        value = asdict(call)
        value["api_key_persisted"] = False
        calls.append(value)
    return {
        "task_id": task["task_id"],
        "ability": task["ability"],
        "variant": task["variant"],
        "critical": task["critical"],
        "sample_kind": sample_kind,
        "status": execution.status,
        "error_code": execution.error_code,
        "gold_scene_verdict": sample["gold"]["scene_verdict"],
        "actual_scene_verdict": (
            execution.consensus["scene_verdict"] if execution.consensus else None
        ),
        "gold_check_verdicts": gold_vector,
        "actual_check_verdicts": actual_vector,
        "exact": actual_vector == gold_vector,
        "consensus": execution.consensus,
        "judge_reports": list(execution.judge_reports),
        "arbiter_report": execution.arbiter_report,
        "calls": calls,
        "request_count": len(calls),
    }


def _policy_metrics(
    policy: ProseCouncilPolicy,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_key = {(item["task_id"], item["sample_kind"]): item for item in results}
    bases = [item for item in results if item["sample_kind"] == "base"]
    overall = sum(item["exact"] for item in bases)
    critical_false_accept = sum(
        item["critical"]
        and item["gold_scene_verdict"] == "fail"
        and item["actual_scene_verdict"] == "pass"
        for item in bases
    )
    implicit_pass = sum(
        item["variant"] == "implicit_valid"
        and item["actual_scene_verdict"] == "pass"
        and item["exact"]
        for item in bases
    )
    mutation_detection = 0
    critical_mutation_miss = 0
    paraphrase_invariance = 0
    per_task_requests = []
    for task in tasks:
        key = task["task_id"]
        base = by_key.get((key, "base"))
        para = by_key.get((key, "paraphrase"))
        mutation = by_key.get((key, "mutation"))
        if base and para and mutation:
            if mutation["exact"] and (
                mutation["actual_check_verdicts"] != base["actual_check_verdicts"]
            ):
                mutation_detection += 1
            elif task["critical"]:
                critical_mutation_miss += 1
            if (
                base["exact"]
                and para["exact"]
                and base["actual_check_verdicts"] == para["actual_check_verdicts"]
            ):
                paraphrase_invariance += 1
            per_task_requests.append(
                base["request_count"] + para["request_count"] + mutation["request_count"]
            )
    protocol_failures = sum(item["status"] == "protocol_failed" for item in results)
    infrastructure_failures = sum(item["status"] == "inconclusive" for item in results)
    calls = [call for item in results for call in item["calls"]]
    evidence_binding = (
        1.0
        if results
        and protocol_failures == 0
        and all(item["status"] == "completed" for item in results)
        else 0.0
    )
    gates = {
        "overall": overall >= 22,
        "critical_invalid_false_accept": critical_false_accept == 0,
        "implicit_valid": implicit_pass >= 7,
        "mutation_detection": mutation_detection >= 23,
        "critical_mutation_miss": critical_mutation_miss == 0,
        "paraphrase_invariance": paraphrase_invariance >= 23,
        "evidence_binding": evidence_binding == 1.0,
        "infrastructure": infrastructure_failures == 0,
    }
    return {
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "roles": list(policy.roles),
        "metrics": {
            "base_overall": {"passed": overall, "total": 24},
            "critical_invalid_false_accept": {"count": critical_false_accept, "total": 8},
            "implicit_valid": {"passed": implicit_pass, "total": 8},
            "mutation_detection": {"passed": mutation_detection, "total": 24},
            "critical_mutation_miss": {"count": critical_mutation_miss, "total": 8},
            "paraphrase_invariance": {"passed": paraphrase_invariance, "total": 24},
            "evidence_binding_rate": evidence_binding,
            "protocol_failures": protocol_failures,
            "infrastructure_failures": infrastructure_failures,
            "median_requests_per_task": (
                statistics.median(per_task_requests) if per_task_requests else None
            ),
            "requests": len(calls),
            "usage": {
                name: sum(call["usage"].get(name, 0) for call in calls)
                for name in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cached_tokens",
                    "reasoning_tokens",
                )
            },
            "latency_ms": sum(call["latency_ms"] for call in calls),
        },
        "gates": gates,
        "eligible": all(gates.values()),
        "results": results,
    }


def _select_policy(policy_reports: list[dict[str, Any]]) -> str | None:
    eligible = [item for item in policy_reports if item["eligible"]]
    if not eligible:
        return None
    order = {policy.policy_id: index for index, policy in enumerate(PROSE_COUNCIL_POLICIES)}
    selected = min(
        eligible,
        key=lambda item: (
            item["metrics"]["median_requests_per_task"],
            order[item["policy_id"]],
        ),
    )
    return str(selected["policy_id"])


def freeze_selected_policy(
    report: dict[str, Any],
    *,
    descriptor_path: Path | None = None,
    compact_report_path: Path | None = None,
    markdown_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize only the compact, non-qualifying development evidence."""

    if report.get("report_hash") != canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    ):
        raise ProseJudgeSuiteError("prose_judge_live_report_hash_invalid")
    selected_id = report.get("selected_policy_id")
    if (
        report.get("mode") != "live"
        or report.get("status") != "completed"
        or not isinstance(selected_id, str)
    ):
        raise ProseJudgeSuiteError("prose_judge_policy_freeze_not_eligible")
    selected_report = next(
        (item for item in report["policies"] if item["policy_id"] == selected_id), None
    )
    selected_policy = next(
        (policy for policy in PROSE_COUNCIL_POLICIES if policy.policy_id == selected_id),
        None,
    )
    if selected_report is None or selected_policy is None or not selected_report["eligible"]:
        raise ProseJudgeSuiteError("prose_judge_selected_policy_invalid")
    compact = {
        "schema_id": "casefile.prose-judge-dev-result.v1",
        "qualified": False,
        "holdout_eligible": True,
        "selected_policy_id": selected_id,
        "source_report_hash": report["report_hash"],
        "raw_call_bundle_hash": report["raw_call_bundle_hash"],
        "call_count": report["call_count"],
        "lineage": report["lineage"],
        "policies": [
            {
                "policy_id": item["policy_id"],
                "policy_hash": item["policy_hash"],
                "roles": item["roles"],
                "metrics": item["metrics"],
                "gates": item["gates"],
                "eligible": item["eligible"],
            }
            for item in report["policies"]
        ],
    }
    compact["report_hash"] = canonical_hash(compact)
    descriptor = {
        "schema_id": "casefile.prose-council-policy.v1",
        **selected_policy.descriptor(),
        "policy_hash": selected_policy.policy_hash,
        "model_id": report["lineage"]["model_id"],
        "max_output_tokens": report["lineage"]["max_output_tokens"],
        "max_turns": 1,
        "network_retries": 0,
        "temperature": 0,
        "thinking_enabled": False,
        "prompt_bindings": report["lineage"]["prompt_bindings"],
        "schema_id_binding": report["lineage"]["schema_id"],
        "schema_hash": report["lineage"]["schema_hash"],
        "request_protocol": report["lineage"]["request_protocol"],
        "suite_hash": report["lineage"]["suite_hash"],
        "attestation_hash": report["lineage"]["attestation_hash"],
        "development_report_hash": compact["report_hash"],
        "raw_call_bundle_hash": report["raw_call_bundle_hash"],
        "implementation_revision": report["lineage"]["implementation_revision"],
        "qualified": False,
        "qualification_stage": "development_only",
    }
    descriptor["descriptor_hash"] = canonical_hash(descriptor)
    descriptor_path = descriptor_path or (
        ROOT / "fixtures/prose_judge_benchmark/v1/frozen-council-policy.json"
    )
    compact_report_path = compact_report_path or (
        ROOT / "fixtures/prose_judge_benchmark/v1/development-result.json"
    )
    markdown_path = markdown_path or (
        ROOT / "docs/narrative-compiler/n4.5-02-development-results.md"
    )
    _write_json(compact_report_path, compact)
    _write_json(descriptor_path, descriptor)
    _write_markdown_report(markdown_path, compact, descriptor)
    return descriptor, compact


def _write_markdown_report(
    path: Path, compact: dict[str, Any], descriptor: dict[str, Any]
) -> None:
    rows = []
    for item in compact["policies"]:
        metrics = item["metrics"]
        rows.append(
            "| {policy} | {overall}/24 | {critical} | {implicit}/8 | {mutation}/24 | "
            "{paraphrase}/24 | {median} | {eligible} |".format(
                policy=item["policy_id"],
                overall=metrics["base_overall"]["passed"],
                critical=metrics["critical_invalid_false_accept"]["count"],
                implicit=metrics["implicit_valid"]["passed"],
                mutation=metrics["mutation_detection"]["passed"],
                paraphrase=metrics["paraphrase_invariance"]["passed"],
                median=metrics["median_requests_per_task"],
                eligible="是" if item["eligible"] else "否",
            )
        )
    text = "\n".join(
        [
            "# N4.5-02 公开开发集 Council 消融结果",
            "",
            f"- 实现 revision：`{descriptor['implementation_revision']}`",
            f"- Suite hash：`{descriptor['suite_hash']}`",
            f"- 原始调用 bundle hash：`{descriptor['raw_call_bundle_hash']}`",
            f"- 选定策略：`{descriptor['policy_id']}`",
            f"- 开发报告 hash：`{compact['report_hash']}`",
            "- 资格状态：`qualified=false`；本结果只用于公开开发集策略选择。",
            "- Reviewer 独立性：`false`；N4.5-03 仍需独立私有 Holdout reviewer。",
            "",
            (
                "| Policy | Base | 关键非法误接收 | 隐含合法 | Mutation | "
                "Paraphrase | 中位请求 | 合格 |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---|",
            *rows,
            "",
            "消融按冻结顺序执行。选择规则为先满足全部开发门槛，再取每任务中位请求数最低的策略；并列时优先角色更少的既定顺序。",
            "",
            (
                "完整模型输入、原始响应、usage 与 latency 保存在本地忽略目录；"
                "受跟踪报告只保留哈希、指标和绑定关系。"
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _lineage(loaded: dict[str, Any], mode: str) -> dict[str, Any]:
    prompts = {}
    for agent_id in (
        "prose_fidelity_judge",
        "prose_adversarial_judge",
        "prose_coherence_judge",
        "prose_arbiter",
    ):
        prompt = load_prompt(agent_id)
        prompts[agent_id] = {
            "version": prompt.version,
            "hash": prompt.system_prompt_sha256,
        }
    revision = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return {
        "implementation_revision": revision,
        "dirty_state": dirty,
        "suite_hash": loaded["suite"]["suite_hash"],
        "attestation_hash": loaded["attestation"]["attestation_hash"],
        "checklist_hash": canonical_json_sha256(loaded["checklist"]),
        "profile_hash": canonical_json_sha256(loaded["profile"]),
        "model_id": PROSE_COUNCIL_MODEL_ID,
        "prompt_bindings": prompts,
        "schema_id": "compiler.prose-judge-report.v1",
        "schema_hash": PROSE_JUDGE_SCHEMA_HASH,
        "request_protocol": PROSE_JUDGE_REQUEST_PROTOCOL,
        "max_output_tokens": PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
        "runner_hash": canonical_hash(
            {
                "source": Path(__file__).read_text(encoding="utf-8"),
                "mode": mode,
            }
        ),
        "started_at": datetime.now(UTC).isoformat(),
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake", "live"), required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--freeze-repo", action="store_true")
    args = parser.parse_args()
    api_key = os.getenv("CASEFILE_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "live":
        if not api_key:
            raise SystemExit("DeepSeek API key is required")
        if _git("status", "--porcelain"):
            raise SystemExit("Live N4.5-02 requires a clean worktree")
        provider = DeepSeekProseJudgeProvider()

        def factory(
            _sample: dict[str, Any], _policy: ProseCouncilPolicy
        ) -> ProseJudgeProvider:
            return provider
    else:
        loaded = load_prose_judge_dev_suite(args.suite, args.attestation)
        checklist = loaded["checklist"]

        def factory(
            _sample: dict[str, Any], _policy: ProseCouncilPolicy
        ) -> ProseJudgeProvider:
            gold = _sample["gold"]
            render = _sample["render"]
            return FakeProseJudgeProvider(
                judge_reports=tuple(
                    _gold_report(gold, checklist, render, role=role)
                    for role in _policy.roles
                )
            )

    report = run_development_ablation(
        provider_factory=factory,
        api_key=api_key,
        mode=args.mode,
        output_dir=args.output_dir,
        suite_path=args.suite,
        attestation_path=args.attestation,
    )
    if args.freeze_repo and report["selected_policy_id"] is not None:
        freeze_selected_policy(report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "selected_policy_id": report["selected_policy_id"],
                "call_count": report["call_count"],
                "report_hash": report["report_hash"],
            },
            ensure_ascii=False,
        )
    )
    if report["status"] == "inconclusive":
        return 3
    return 0 if report["selected_policy_id"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ATTESTATION",
    "DEFAULT_SUITE",
    "ProseJudgeSuiteError",
    "canonical_hash",
    "freeze_selected_policy",
    "load_prose_judge_dev_suite",
    "oracle_provider_for_sample",
    "run_development_ablation",
]
