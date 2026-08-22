from __future__ import annotations

import json

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


def test_opposed_terms_form_a_pair_without_a_repeated_token() -> None:
    bundle = build_audit_evidence_bundle(
        {
            "entities": [
                {"id": "ent_researcher", "description": "研究员认为是人工误操作。"},
                {"id": "ent_backup", "description": "备用系统会自动触发重启。"},
            ]
        }
    )

    assert bundle.payload["candidate_pairs"][0]["source_rule"] == "opposed_terms"


def test_shared_event_participant_extends_the_candidate_pair_graph() -> None:
    bundle = build_audit_evidence_bundle(
        {
            "entities": [
                {"id": "ent_researcher", "description": "研究员复查第七次重启原因。"},
                {"id": "ent_backup", "description": "备用控制系统。"},
            ],
            "events": [
                {
                    "id": "evt_restart",
                    "title": "系统第七次重启",
                    "participant_refs": [{"object_id": "ent_backup"}],
                }
            ],
        }
    )

    assert any(
        pair["source_rule"] == "shared_event_with_participant"
        and pair["left_id"] == "ent_researcher"
        and pair["right_id"] == "ent_backup"
        for pair in bundle.payload["candidate_pairs"]
    )


def test_repair_expectation_is_derived_from_pairs_and_editable_fields() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    bundle = build_audit_evidence_bundle(
        task.frozen_casefile,
        editable_fields_by_collection={"entities": ("description",)},
    )

    target = bundle.payload["repair_expectation"]["candidate_patch_targets"][0]
    assert target["object_id"] == "ent_leader"
    assert target["path"] == "/description"
    assert target["current_value_json"] == json.dumps(
        task.frozen_casefile["entities"][0]["description"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert target["value_type"] == "str"
    assert target["source_kind"] == "cross_record_conflict_candidate"
    assert target["pair_index"] == "0"


def test_event_participant_context_is_evidence_not_a_second_repair_target() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    bundle = build_audit_evidence_bundle(
        task.frozen_casefile,
        editable_fields_by_collection={
            "entities": ("description",),
            "claims": ("statement",),
        },
    )

    targets = bundle.payload["repair_expectation"]["candidate_patch_targets"]

    assert [(target["object_id"], target["path"]) for target in targets] == [
        ("ent_researcher", "/description")
    ]
