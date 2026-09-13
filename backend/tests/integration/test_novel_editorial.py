"""Real Worker/database editorial phases with deterministic, zero-network providers."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update
from test_novel_editor import prepare, run, submit

from casefile.application.novel_editor import NovelEditorService
from casefile.data_postgres.models import AgentModelCall, TaskRun
from casefile.domain.narrative_compiler import canonical_json_sha256

pytestmark = pytest.mark.postgres


class EditorialProvider:
    def __init__(self, scenario):
        self.scenario = scenario
        self.attempts = {}

    def result(self, candidate):
        return SimpleNamespace(
            candidate=candidate,
            raw_response=json.dumps(candidate),
            usage={"requests": 1, "total_tokens": 10},
            finish_reason="stop",
        )

    def attempt(self, phase):
        self.attempts[phase] = self.attempts.get(phase, 0) + 1
        return self.attempts[phase]

    def edit(self, payload, key, model, repair=None):
        phase = "revision" if payload.get("revision_context") else "generation"
        number = self.attempt(phase)
        assert payload["requirements"]["preserve"] == "门必须打开"
        if phase == "revision" and self.scenario == "revision_unavailable":
            raise OSError("fake revision failure")
        if number == 1 and self.scenario == "repair_every_phase":
            return self.result({})
        text = "细雨敲窗，木门关着。"
        if phase == "revision" and self.scenario != "unchanged_revision":
            text = "细雨敲窗，木门推开了。"
        return self.result({"message": "生成候选", "text": text, "reason": "调整节奏"})

    def review(self, payload, key, model):
        exhausted = payload["revision_exhausted"]
        number = self.attempt("review1" if exhausted else "review0")
        if self.scenario == "review_unavailable" or (
            exhausted and self.scenario == "review_after_revision_unavailable"
        ):
            raise OSError("fake review failure")
        if self.scenario == "repair_every_phase" and number == 1:
            return self.result({})
        action = "accept" if exhausted else "revise"
        if self.scenario == "needs_author" and exhausted:
            action = "needs_author"
        return self.result(
            {
                "summary": "已检查保留项",
                "action": action,
                "revision_plan": "将关门恢复为开门" if action == "revise" else "",
                "findings": []
                if action == "accept"
                else [
                    {
                        "category": "preservation",
                        "severity": "major",
                        "message": "需要核对开门动作",
                        "source_quote": "门开了。",
                        "candidate_quote": payload["candidate"]["text"],
                        "suggestion": "保留开门动作",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "scenario,status,calls,revisions",
    [
        ("normal", "completed", 4, 1),
        ("review_unavailable", "incomplete", 2, 0),
        ("revision_unavailable", "revision_failed", 3, 0),
        ("review_after_revision_unavailable", "incomplete", 4, 1),
        ("unchanged_revision", "revision_failed", 3, 0),
        ("needs_author", "completed", 4, 1),
        ("repair_every_phase", "completed", 8, 1),
    ],
)
def test_review_revision_fallback_and_audited_budget(
    workflow_database, scenario, status, calls, revisions
):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    exchange = submit(
        db,
        factory,
        project,
        novel,
        scope="chapter_rewrite",
        requirements={"preserve": "门必须打开", "allow_changes": "措辞"},
    )
    provider = EditorialProvider(scenario)
    run(db, factory, exchange, provider)
    with factory() as session:
        view = NovelEditorService(session).get(db[1], project, novel["id"])
        reply = view["exchanges"][0]
        assert reply["status"] == "succeeded", session.get(
            TaskRun, exchange["task_id"]
        ).error_details_jsonb
        report = reply["editorial_review"]
        assert report["status"] == status and report["revision_count"] == revisions
        candidate = reply["edits"][0]["after"]
        assert report["candidate_hash"] == canonical_json_sha256(candidate)
        assert view["chapters"] == novel["chapters"] and view["revision"] == 1
        assert reply["requirements"]["preserve"] == "门必须打开"
        assert reply["usage"]["requests"] == calls
        if "unavailable" in scenario:
            assert reply["usage"]["unknown_usage_count"] == 1
        records = list(
            session.scalars(
                select(AgentModelCall)
                .where(
                    AgentModelCall.task_run_id == exchange["task_id"],
                )
                .order_by(AgentModelCall.call_no)
            )
        )
        assert len(records) == calls and [r.call_no for r in records] == list(range(1, calls + 1))
        assert all(r.finished_at and r.status != "running" for r in records)
        if status == "completed":
            assert report["reports"][-1]["candidate_hash"] == report["candidate_hash"]
        if scenario == "review_after_revision_unavailable":
            assert report["reports"][-1]["candidate_hash"] != report["candidate_hash"]
        if scenario == "needs_author":
            assert report["reports"][-1]["review"]["action"] == "needs_author"
        if scenario == "repair_every_phase":
            assert len([r for r in records if r.status == "failed"]) == 4
        saved_calls = len(records)
    run(db, factory, exchange, provider)
    assert sum(provider.attempts.values()) == saved_calls


def test_cancellation_during_review_leaves_no_adoptable_edits(workflow_database):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    exchange = submit(
        db,
        factory,
        project,
        novel,
        scope="chapter_rewrite",
        requirements={"preserve": "门必须打开", "allow_changes": ""},
    )

    class CancelReview(EditorialProvider):
        def review(self, *args):
            with factory() as session, session.begin():
                session.execute(
                    update(TaskRun)
                    .where(TaskRun.id == exchange["task_id"])
                    .values(status="cancelling")
                )
            return super().review(*args)

    run(db, factory, exchange, CancelReview("normal"))
    with factory() as session:
        view = NovelEditorService(session).get(db[1], project, novel["id"])
        assert view["exchanges"][0]["status"] == "cancelled"
        assert not view["exchanges"][0]["edits"] and view["chapters"] == novel["chapters"]


def test_persisted_call_budget_prevents_starting_a_revision(workflow_database):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    def freeze_small_budget(mapper, connection, task):
        task.budget_jsonb = {"max_calls": 2}

    # Freeze the test budget at creation; never mutate an immutable TaskRun.
    event.listen(TaskRun, "before_insert", freeze_small_budget)
    try:
        exchange = submit(db, factory, project, novel, scope="chapter_rewrite",
                          requirements={"preserve": "门必须打开", "allow_changes": ""})
    finally:
        event.remove(TaskRun, "before_insert", freeze_small_budget)
    provider = EditorialProvider("normal")
    run(db, factory, exchange, provider)
    assert provider.attempts == {"generation": 1, "review0": 1}
    with factory() as session:
        view = NovelEditorService(session).get(db[1], project, novel["id"])
        assert view["exchanges"][0]["editorial_review"]["status"] == "revision_failed"
        assert view["chapters"] == novel["chapters"]


@pytest.fixture(autouse=True)
def legacy_editorial_policy(monkeypatch):
    from casefile.application import novel_collaboration as application
    original = application.prepare_context
    def prepare_legacy(*args, **kwargs):
        context = original(*args, **kwargs)
        if context.get("editorial_policy"):
            context["editorial_policy"] = "chapter-editorial-v2"
        return context
    monkeypatch.setattr(application, "prepare_context", prepare_legacy)
