"""Real Worker persistence with fake heavy-chain roles; never spends model quota."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update
from test_novel_editor import prepare, run, submit

from casefile.application.novel_editor import NovelEditorService
from casefile.data_postgres.models import AgentModelCall, TaskRun
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS

pytestmark = pytest.mark.postgres


class ProseRoles:
    def __init__(self, scenario="rewrite"):
        self.scenario = scenario
        self.calls = []
        self.chapter_prose = self

    def invoke(self, phase, data, key, model):
        self.calls.append((phase, data))
        if phase == "judge" and self.scenario == "judge_unavailable":
            raise OSError("test-only unavailable")
        if phase == "judge" and self.scenario == "invalid_judge":
            value = {"schema_id": "compiler.prose-judge-candidate.v1", "assessments": []}
        elif phase == "checklist":
            assert (
                not {"history", "previous_chapter", "next_chapter", "related_settings"}
                & data.keys()
            )
            value = {
                "checks": [
                    {
                        "check_id": "intent",
                        "category": "intent",
                        "polarity": "required",
                        "requirement": data["instruction"],
                        "source_field": "instruction",
                        "source_quote": data["instruction"],
                    }
                ]
            }
            if self.scenario == "checklist_repair":
                if sum(name == "checklist" for name, _ in self.calls) == 1:
                    value["checks"][0]["source_quote"] = "不存在的引文"
                else:
                    assert data["protocol_repair"]["rejected_candidate"]["checks"]
        elif phase == "judge":
            passed = data["candidate"] == "雨停了，门开了。"
            if self.scenario == "preservation_failed":
                passed = False
            value = {
                "schema_id": "compiler.prose-judge-candidate.v1",
                "assessments": [
                    {
                        "check_id": "intent",
                        "verdict": "pass" if passed else "fail",
                        "evidence_ids": [data["server_evidence_catalog"][0]["evidence_id"]],
                        "rationale": "已完成" if passed else "仍需修订",
                    }
                ],
            }
            if self.scenario in {"judge_evidence_flip", "judge_evidence_repair"}:
                number = sum(name == "judge" for name, _ in self.calls)
                if number == 1:
                    value["assessments"][0]["evidence_ids"] = ["invented"]
                elif number == 2:
                    assert data["protocol_repair"]["allowed_change"] == "evidence_ids_only"
                    if self.scenario == "judge_evidence_flip":
                        value["assessments"][0]["verdict"] = "pass"
        elif phase == "revision":
            passed = data["judge"]["assessments"][0]["verdict"] == "pass"
            action = (
                "retain"
                if passed
                else "stop"
                if data["repair_budget_exhausted"]
                else "full_rewrite"
            )
            value = {
                "action": action,
                "rationale": "依据逐项核对作出决定",
                "revision_plan": "压缩重复" if action == "full_rewrite" else "",
                "findings": [
                    {
                        "check_id": "intent",
                        "assessment": "valid",
                        "severity": "none" if passed else "nonfatal",
                        "reason": "核对正文",
                    }
                ],
            }
        elif phase in {"rewriter", "polisher"}:
            value = {
                "schema_id": "compiler.scene-render-candidate.v1",
                "blocks": [{"text": "雨停了，门开了。"}],
            }
            if self.scenario == "no_progress":
                value["blocks"] = [{"text": data["candidate"]}]
        elif phase == "quality_critic":
            value = {
                "schema_id": "compiler.prose-quality-findings-candidate.v1",
                "findings": [
                    {
                        "dimension": QUALITY_DIMENSIONS[3],
                        "severity": "medium",
                        "description": "推进节奏可以更紧凑",
                        "evidence_ids": [data["server_evidence_catalog"][0]["evidence_id"]],
                    }
                ],
            }
        elif phase == "pairwise":
            polished = "a" if data["a"] == "雨停了，门开了。" else "b"
            chosen = "a" if self.scenario == "position_bias" else polished
            value = {
                "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
                "overall_preference": chosen,
                "dimension_preferences": [
                    {"dimension": dimension, "preference": chosen}
                    for dimension in QUALITY_DIMENSIONS
                ],
            }
        else:
            raise AssertionError(phase)
        result = SimpleNamespace(
            candidate=value,
            raw_response=json.dumps(value),
            usage={"requests": 1, "total_tokens": 10},
            finish_reason="stop",
        )
        if self.scenario == "missing_usage" and phase == "quality_critic":
            result.usage = {"requests": 1, "total_tokens": 0}
            result.transport_attempts = (SimpleNamespace(status="completed", usage=None),)
        return result


@pytest.mark.parametrize(
    "scenario,mode,expected_calls,changed",
    [
        ("rewrite", "rewrite", 6, True),
        ("polish", "polish", 7, True),
        ("position_bias", "polish", 7, False),
        ("preservation_failed", "polish", 5, False),
        ("judge_unavailable", "rewrite", 2, False),
        ("invalid_judge", "rewrite", 3, False),
        ("checklist_repair", "rewrite", 7, True),
        ("no_progress", "rewrite", 4, False),
        ("missing_usage", "polish", 7, True),
        ("judge_evidence_flip", "rewrite", 3, False),
        ("judge_evidence_repair", "rewrite", 7, True),
    ],
)
def test_heavy_roles_are_audited_and_never_auto_adopt(
    workflow_database, scenario, mode, expected_calls, changed
):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    exchange = submit(db, factory, project, novel, scope="chapter_rewrite", mode=mode)
    provider = ProseRoles(scenario)
    run(db, factory, exchange, provider)
    with factory() as session:
        view = NovelEditorService(session).get(db[1], project, novel["id"])
        reply = view["exchanges"][0]
        task = session.get(TaskRun, exchange["task_id"])
        assert task.status == "succeeded", task.error_details_jsonb
        assert task.input_jsonb["context"]["editorial_policy"] == "chapter-prose-v1"
        assert len(task.input_jsonb["prose_prompt_hashes"]) == 7
        assert task.budget_jsonb["max_calls"] == 18
        assert view["chapters"] == novel["chapters"] and view["revision"] == 1
        assert bool(reply["edits"]) == changed
        assert reply["usage"]["requests"] == expected_calls
        assert len(provider.calls) == expected_calls
        calls = list(
            session.scalars(select(AgentModelCall).where(AgentModelCall.task_run_id == task.id))
        )
        assert len(calls) == expected_calls
        assert all(call.raw_output_text or call.error_code for call in calls)
        stages = reply["editorial_review"]["stages"]
        assert stages[0]["phase"] == "checklist"
        assert task.result_jsonb["prose_trace"]
        if scenario == "rewrite":
            assert [phase for phase, _ in provider.calls] == [
                "checklist",
                "judge",
                "revision",
                "rewriter",
                "judge",
                "revision",
            ]
        if scenario == "position_bias":
            assert "未稳定支持" in reply["message"]
        if scenario in {"judge_unavailable", "invalid_judge", "judge_evidence_flip"}:
            assert reply["editorial_review"]["status"] == "incomplete"
        if scenario == "no_progress":
            assert reply["editorial_review"]["status"] == "revision_failed"
            assert reply["editorial_review"]["stages"][-1]["status"] == "incomplete"
        if scenario == "missing_usage":
            assert reply["usage"]["unknown_usage_count"] == 1
        if changed:
            assert reply["edits"][0]["after"] == "雨停了，门开了。"


@pytest.mark.parametrize("cancel", [False, True])
def test_budget_and_cancellation_preserve_actual_text(workflow_database, cancel):
    db = workflow_database
    factory, project, novel, _ = prepare(db)

    def small_budget(mapper, connection, task):
        task.budget_jsonb = {"max_calls": 2}

    if not cancel:
        event.listen(TaskRun, "before_insert", small_budget)
    try:
        exchange = submit(db, factory, project, novel, scope="chapter_rewrite")
    finally:
        if not cancel:
            event.remove(TaskRun, "before_insert", small_budget)

    class CancelRoles(ProseRoles):
        def invoke(self, phase, data, key, model):
            if phase == "judge" and cancel:
                with factory() as session, session.begin():
                    session.execute(
                        update(TaskRun)
                        .where(TaskRun.id == exchange["task_id"])
                        .values(status="cancelling")
                    )
            return super().invoke(phase, data, key, model)

    provider = CancelRoles()
    run(db, factory, exchange, provider)
    assert len(provider.calls) == 2
    with factory() as session:
        view = NovelEditorService(session).get(db[1], project, novel["id"])
        reply = view["exchanges"][0]
        assert view["chapters"] == novel["chapters"] and not reply["edits"]
        assert reply["status"] == ("cancelled" if cancel else "succeeded")
        if not cancel:
            assert reply["editorial_review"]["status"] == "incomplete"
