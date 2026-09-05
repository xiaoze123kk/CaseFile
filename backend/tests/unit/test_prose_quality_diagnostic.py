"""Public diagnostics isolate candidates, preserve denominators and keep credentials out."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from casefile.agent_runtime.prose_quality_config import QUALITY_PRO_DIAGNOSTIC, QUALITY_V2
from casefile.agent_runtime.prose_quality_critic import (
    FakeProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
)
from casefile.benchmark.prose_quality_diagnostic import Audit, write_new
from casefile.benchmark.prose_quality_diagnostic_report import quality_row, summarize_quality
from casefile.benchmark.prose_quality_diagnostic_suite import load_diagnostic_suite
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS


def candidate(overall):
    return {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        "overall_preference": overall,
        "dimension_preferences": [
            {"dimension": d, "preference": overall} for d in QUALITY_DIMENSIONS
        ],
    }


def execute(task, provider=None, **kwargs):
    return execute_mirrored_pairwise_quality(
        provider
        or FakeProseQualityCriticProvider(pairwise_candidates=(candidate("b"), candidate("a"))),
        checklist=task["checklist"],
        profile=task["profile"],
        original_render=task["render_a"],
        polished_render=task["render_b"],
        preservation_consensus=task["semantic_consensus_b"],
        api_key="test-secret-never-log",
        **kwargs,
    )


def test_public_suite_all_bindings_and_semantic_review():
    suite = load_diagnostic_suite()
    assert len(suite["quality_tasks"]) == len(suite["polisher_tasks"]) == 24
    assert suite["review"]["semantic_evidence_origin"] == "authored_gold_not_live_council"
    assert suite["review"]["reviewer_independence"] is False
    assert suite["qualified"] is False


def test_default_fingerprint_compatible_and_pro_is_explicit():
    task = load_diagnostic_suite()["quality_tasks"][0]
    default = execute(task, model_id=QUALITY_V2.pairwise_model)
    explicit = execute(task, model_id=QUALITY_V2.pairwise_model, config=QUALITY_V2)
    pro = execute(
        task, model_id=QUALITY_PRO_DIAGNOSTIC.pairwise_model, config=QUALITY_PRO_DIAGNOSTIC
    )
    assert default.status == explicit.status == pro.status == "completed"
    assert default.calls[0].request_fingerprint == explicit.calls[0].request_fingerprint
    assert pro.calls[0].request_fingerprint != default.calls[0].request_fingerprint
    assert pro.calls[0].prompt_hash == default.calls[0].prompt_hash
    assert (
        pro.calls[0].request_payload["untrusted_data"]
        == default.calls[0].request_payload["untrusted_data"]
    )
    assert "gold" not in json.dumps(pro.calls[0].request_payload)
    provider = FakeProseQualityCriticProvider()
    invalid = execute(task, provider, model_id="deepseek-v4-pro")
    assert invalid.status == "protocol_failed" and provider.call_count == 0
    invalid = execute(
        task,
        provider,
        model_id="deepseek-v4-pro",
        config=replace(QUALITY_PRO_DIAGNOSTIC, findings_model="deepseek-v4-pro"),
    )
    assert invalid.status == "protocol_failed" and provider.call_count == 0


def test_reverse_dispatch_preserves_canonical_reports_and_selection():
    task = load_diagnostic_suite()["quality_tasks"][0]
    normal = execute(task, model_id=QUALITY_V2.pairwise_model)
    reverse = execute(
        task,
        FakeProseQualityCriticProvider(pairwise_candidates=(candidate("a"), candidate("b"))),
        model_id=QUALITY_V2.pairwise_model,
        reverse_first=True,
    )
    assert reverse.status == "completed"
    assert reverse.reports == normal.reports
    assert reverse.decision == normal.decision
    assert reverse.calls[0].request_fingerprint == normal.calls[1].request_fingerprint


def test_recovery_cannot_cross_model_config():
    task = load_diagnostic_suite()["quality_tasks"][0]
    baseline = execute(task, model_id=QUALITY_V2.pairwise_model)
    provider = FakeProseQualityCriticProvider()
    result = execute(
        task,
        provider,
        model_id="deepseek-v4-pro",
        config=QUALITY_PRO_DIAGNOSTIC,
        recover_call=lambda _: baseline.calls[0],
    )
    assert result.status == "protocol_failed"
    assert provider.call_count == 0


def test_selection_uses_worst_repeat_and_keeps_fixed_denominator():
    suite = load_diagnostic_suite()
    rows = []
    for config in (QUALITY_V2, QUALITY_PRO_DIAGNOSTIC):
        for repeat in range(3):
            for task in suite["quality_tasks"]:
                gold = task["gold"]["overall_preference"]
                swap = {"a": "b", "b": "a", "tie": "tie"}[gold]
                result = execute(
                    task,
                    FakeProseQualityCriticProvider(
                        pairwise_candidates=(candidate(gold), candidate(swap))
                    ),
                    model_id=config.pairwise_model,
                    config=config,
                )
                rows.append(quality_row(task, result, repeat, config.config_id))
    summary = summarize_quality(rows)
    assert summary["selected"] == QUALITY_V2.config_id
    assert summary["qualified"] is False
    degraded = deepcopy(rows)
    for row in degraded:
        if (
            row["candidate"] == QUALITY_V2.config_id
            and row["repeat"] == 2
            and row["task_id"] in {"quality_01", "quality_02"}
        ):
            row["mirrored"] = False
    assert summarize_quality(degraded)["selected"] == QUALITY_PRO_DIAGNOSTIC.config_id
    missing = [r for r in rows if r["task_id"] != "quality_01"]
    assert summarize_quality(missing)["selected"] is None
    assert (
        summarize_quality(missing)["candidates"][QUALITY_V2.config_id]["repetitions"][0]["total"]
        == 24
    )


def test_tie_and_position_disagreement_do_not_count_as_stable_wins():
    task = load_diagnostic_suite()["quality_tasks"][2]
    result = execute(
        task,
        FakeProseQualityCriticProvider(pairwise_candidates=(candidate("tie"), candidate("tie"))),
        model_id=QUALITY_V2.pairwise_model,
    )
    row = quality_row(task, result, 0, QUALITY_V2.config_id)
    assert row["correct"] and row["mirrored"]
    assert not result.decision.accept_polished
    unstable = execute(
        task,
        FakeProseQualityCriticProvider(pairwise_candidates=(candidate("a"), candidate("a"))),
        model_id=QUALITY_V2.pairwise_model,
    )
    assert not quality_row(task, unstable, 0, QUALITY_V2.config_id)["mirrored"]


def test_audit_is_immutable_and_never_serializes_key(tmp_path: Path):
    audit = Audit(tmp_path / "calls")
    fake = FakeProseQualityCriticProvider(pairwise_candidates=(candidate("a"), candidate("b")))

    class Provider:
        def assess_quality(self, request):
            return audit.invoke(fake.assess_quality, request)

    result = execute(
        load_diagnostic_suite()["quality_tasks"][0], Provider(), model_id=QUALITY_V2.pairwise_model
    )
    assert result.status == "completed"
    assert audit.count == audit.physical == 2
    for path in (tmp_path / "calls").glob("*.json"):
        assert "test-secret-never-log" not in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_new(tmp_path / "calls/00001-request.json", {})


@pytest.mark.parametrize("semantic_fail", [False, True])
def test_frozen_findings_reuse_keeps_full_council_and_exact_rollback(semantic_fail):
    from casefile.agent_runtime.prose_judge import (
        FakeProseJudgeProvider,
        build_server_evidence_catalog,
    )
    from casefile.agent_runtime.prose_polish_supervisor import execute_prose_polish_supervisor
    from casefile.agent_runtime.prose_polisher import FakeProsePolisherProvider
    from casefile.agent_runtime.prose_quality_critic import execute_quality_findings

    task = load_diagnostic_suite()["polisher_tasks"][0]
    findings = execute_quality_findings(
        FakeProseQualityCriticProvider(
            findings_candidates=(
                {"schema_id": "compiler.prose-quality-findings-candidate.v1", "findings": []},
            )
        ),
        checklist=task["checklist"],
        profile=task["profile"],
        render=task["original_render"],
        semantic_consensus=task["semantic_consensus"],
        model_id="deepseek-v4-flash",
        api_key="fake",
    )

    class Judge:
        count = 0

        def judge_scene(self, request):
            self.count += 1
            evidence = build_server_evidence_catalog(task["original_render"])[0]["evidence_id"]
            assessments = []
            for check in task["checklist"]["checks"]:
                fail = semantic_fail and check["ordinal"] == 1
                assessments.append(
                    {
                        "check_id": check["check_id"],
                        "verdict": "fail" if fail else "pass",
                        "evidence_ids": [evidence]
                        if check["polarity"] == "required" and not fail
                        else [],
                        "rationale": "开发协议测试。",
                    }
                )
            return FakeProseJudgeProvider(
                judge_reports=(
                    {
                        "schema_id": "compiler.prose-judge-candidate.v1",
                        "assessments": assessments,
                    },
                )
            ).judge_scene(request)

        def arbitrate_scene(self, request):
            raise AssertionError("unanimous results require no arbiter")

    judge = Judge()
    quality = FakeProseQualityCriticProvider(pairwise_candidates=(candidate("b"), candidate("a")))
    polisher = FakeProsePolisherProvider(
        candidates=(
            {
                "schema_id": "compiler.scene-render-candidate.v1",
                "blocks": [{"text": b["text"]} for b in task["original_render"]["blocks"]],
            },
        )
    )
    result = execute_prose_polish_supervisor(
        quality,
        polisher,
        judge,
        checklist=task["checklist"],
        profile=task["profile"],
        original_render=task["original_render"],
        semantic_consensus=task["semantic_consensus"],
        quality_model_id="deepseek-v4-flash",
        generation_model_id="deepseek-v4-pro",
        api_key="fake",
        frozen_findings=findings.report,
        quality_config=QUALITY_PRO_DIAGNOSTIC,
    )
    assert judge.count == 3
    assert result.findings.call is None
    assert result.findings.report == findings.report
    assert quality.call_count == (0 if semantic_fail else 2)
    assert result.status == ("finalized_original" if semantic_fail else "finalized_polished")
    if semantic_fail:
        assert result.selection_reason == "polish_semantic_rollback"
        assert [b["text"] for b in result.accepted_render["blocks"]] == [
            b["text"] for b in task["original_render"]["blocks"]
        ]


def test_suite_tampering_fails_before_provider(tmp_path, monkeypatch):
    from casefile.benchmark import prose_quality_diagnostic_suite as loader
    from casefile.domain.narrative_compiler import canonical_json_sha256

    suite = load_diagnostic_suite()
    root = tmp_path / "fixtures/prose_quality_benchmark"
    root.mkdir(parents=True)
    path = root / "suite.json"
    monkeypatch.setattr(loader, "ROOT", tmp_path)
    suite["quality_tasks"][0]["gold"]["overall_preference"] = "b"
    path.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="freeze_invalid"):
        loader.load_diagnostic_suite(path)
    suite["suite_hash"] = canonical_json_sha256(
        {k: v for k, v in suite.items() if k != "suite_hash"}
    )
    path.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="gold_distribution_invalid"):
        loader.load_diagnostic_suite(path)
