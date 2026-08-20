from __future__ import annotations

from casefile.agent_runtime.context.audit_evidence import build_audit_evidence_bundle
from casefile.benchmark.chat_outcome_eval import build_outcome_tasks


def test_audit_evidence_bundle_is_stable_and_reports_crop() -> None:
    casefile = {
        "entities": [{"id": "ent_a", "name": "甲", "description": "人物"}],
        "events": [{"id": "evt_a", "title": "夜间会面", "description": "发生"}],
    }
    first = build_audit_evidence_bundle(casefile, max_chars=24_000)
    second = build_audit_evidence_bundle(casefile, max_chars=24_000)
    assert first.payload == second.payload
    assert first.payload["schema_version"] == "audit-evidence-v1"
    assert first.payload["collection_counts"]["events"] == 1
    assert set(first.payload["uncovered_ids"]) == set()


def test_audit_evidence_bundle_hard_cap_marks_uncovered_records() -> None:
    casefile = {"entities": [{"id": f"ent_{index}", "name": "对象" * 200} for index in range(20)]}
    bundle = build_audit_evidence_bundle(casefile, max_chars=1_000)
    assert bundle.truncated is True
    assert bundle.payload["uncovered_record_count"] > 0
    assert bundle.payload["clean_noop_eligible"] is False


def test_clean_bundle_is_eligible_only_when_no_pairs_or_findings() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-clean-no-op"
    )
    clean = build_audit_evidence_bundle(task.frozen_casefile)
    assert clean.payload["candidate_pairs"] == []
    assert clean.payload["clean_noop_eligible"] is True


def test_event_pair_has_rule_and_both_excerpts() -> None:
    bundle = build_audit_evidence_bundle(
        {
            "events": [
                {"id": "evt_a", "title": "南侧撤离", "description": "北侧灯塔撤离。"}
            ]
        }
    )
    pair = bundle.payload["candidate_pairs"][0]
    assert pair["source_rule"] == "event_title_description_opposed_terms"
    assert pair["left_excerpt"] == "南侧撤离"
    assert pair["right_excerpt"] == "北侧灯塔撤离。"
