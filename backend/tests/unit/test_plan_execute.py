from __future__ import annotations

from casefile.agent_runtime.plan_execute import (
    ExecutionPlanCandidate,
    NagLedger,
    PlanCheckinCandidate,
    PlanReconciliationReport,
    bind_checkin,
    bind_execution_plan,
    build_plan_call_record,
    plan_hash,
    planning_summary,
    validate_reconciliation,
)


def _plan():
    return bind_execution_plan(
        ExecutionPlanCandidate.model_validate(
            {
                "goals": [
                    {
                        "goal_id": "watch_clue",
                        "objective": "让怀表线索保持可见但不提前揭底",
                        "verification": "故事中存在对应物件，且未直接说明真正归属",
                        "owner_branch": "story_world",
                        "source_refs": ["watch"],
                    },
                    {
                        "goal_id": "repair_record",
                        "objective": "用维修记录区分竞争解释",
                        "verification": "证据图包含维修记录并进入相关推理路径",
                        "owner_branch": "evidence_logic",
                        "source_refs": ["record"],
                    },
                    {
                        "goal_id": "late_payoff",
                        "objective": "第六场回收怀表伏笔",
                        "verification": "第六场正文给出来源支持的回收",
                        "owner_branch": "scene_prose",
                        "source_refs": ["scene_006"],
                        "applies_from": 6,
                        "applies_until": 6,
                    },
                ]
            }
        ),
        source_schema_id="test.plan.v1",
        source_plan={"watch": {}, "record": {}, "scene_006": {}},
        allowed_source_refs={"watch", "record", "scene_006"},
    )


def test_nag_is_injected_after_two_omissions_and_cleared_by_any_valid_response() -> None:
    plan = _plan()
    ledger = NagLedger()
    artifact = {"entities": [{"local_key": "watch"}]}
    for call_key in ("story-1", "story-2"):
        ledger.record_success(
            call_key=call_key,
            plan=plan,
            branch="story_world",
            sequence_no=1,
            checkin=None,
        )
    context = ledger.context(plan, branch="story_world", sequence_no=1)
    assert context.nag_reminder is not None
    assert "怀表线索" in context.nag_reminder
    assert "story-1" in context.nag_reminder and "story-2" in context.nag_reminder
    assert context.unresolved_items[0].status == "missing_checkin"

    candidate = PlanCheckinCandidate.model_validate(
        {
            "branch": "story_world",
            "items": [
                {
                    "goal_id": "watch_clue",
                    "status": "not_fulfilled",
                    "evidence_paths": [],
                    "reason": "本轮尚未生成可承载该物件的事件",
                }
            ],
        }
    )
    checkin = bind_checkin(
        candidate,
        plan=plan,
        branch="story_world",
        sequence_no=1,
        artifact=artifact,
        call_key="story-3",
    )
    assert checkin is not None
    record = build_plan_call_record(
        plan=plan,
        branch="story_world",
        sequence_no=1,
        artifact=artifact,
        call_key="story-3",
        checkin=checkin,
    )
    assert record.checkin == checkin and record.artifact_hash == checkin.artifact_hash
    ledger.record_success(
        call_key="story-3",
        plan=plan,
        branch="story_world",
        sequence_no=1,
        checkin=checkin,
    )
    cleared = ledger.context(plan, branch="story_world", sequence_no=1)
    assert cleared.nag_reminder is None
    assert cleared.unresolved_goal_ids == ["watch_clue"]
    assert cleared.unresolved_items[0].status == "not_fulfilled"
    assert cleared.unresolved_items[0].reason == "本轮尚未生成可承载该物件的事件"


def test_parallel_branches_and_future_goals_do_not_share_or_trigger_counters() -> None:
    plan = _plan()
    ledger = NagLedger()
    for call_key in ("story-1", "story-2"):
        ledger.record_success(
            call_key=call_key,
            plan=plan,
            branch="story_world",
            sequence_no=1,
            checkin=None,
        )
    assert ledger.context(plan, branch="evidence_logic", sequence_no=1).nag_reminder is None
    assert ledger.context(plan, branch="scene_prose", sequence_no=5).applicable_goals == []
    assert ledger.context(plan, branch="scene_prose", sequence_no=5).nag_reminder is None


def test_duplicate_success_is_counted_once() -> None:
    plan = _plan()
    ledger = NagLedger()
    for _ in range(3):
        ledger.record_success(
            call_key="same-call",
            plan=plan,
            branch="story_world",
            sequence_no=1,
            checkin=None,
        )
    assert ledger.context(plan, branch="story_world", sequence_no=1).nag_reminder is None


def test_checkin_evidence_must_resolve_in_current_artifact() -> None:
    plan = _plan()
    candidate = {
        "branch": "story_world",
        "items": [
            {
                "goal_id": "watch_clue",
                "status": "fulfilled",
                "evidence_paths": ["/events/9"],
                "reason": "怀表已出现",
            }
        ],
    }
    assert (
        bind_checkin(
            candidate,
            plan=plan,
            branch="story_world",
            sequence_no=1,
            artifact={"events": []},
            call_key="story-1",
        )
        is None
    )


def test_reconciliation_requires_exact_goal_coverage_and_real_evidence() -> None:
    plan = _plan()
    evidence = {"candidate": {"events": ["watch"]}, "renders": ["payoff"]}
    report = PlanReconciliationReport.model_validate(
        {
            "plan_hash": plan_hash(plan),
            "items": [
                {
                    "goal_id": "watch_clue",
                    "status": "fulfilled",
                    "evidence_refs": ["/candidate/events/0"],
                    "reason": "怀表已出现且未直接解释",
                },
                {
                    "goal_id": "repair_record",
                    "status": "not_fulfilled",
                    "evidence_refs": [],
                    "reason": "维修记录没有进入推理路径",
                    "suggested_plan_change": "补充维修记录与假设的连接",
                },
                {
                    "goal_id": "late_payoff",
                    "status": "partial",
                    "evidence_refs": ["/renders/0"],
                    "reason": "已有回收动作，但归属说明仍不清楚",
                },
            ],
        }
    )
    validated = validate_reconciliation(report, plan=plan, evidence=evidence)
    summary = planning_summary(validated)
    assert summary.status == "completed"
    assert (summary.fulfilled, summary.partial, summary.not_fulfilled, summary.unknown) == (
        1,
        1,
        1,
        0,
    )
    assert summary.suggested_plan_changes == ["补充维修记录与假设的连接"]
