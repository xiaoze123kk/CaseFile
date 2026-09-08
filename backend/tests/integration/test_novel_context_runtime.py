"""Rolling memory through real task persistence, with a fake compactor."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_novel_editor import Editor, prepare, run, submit

from casefile.application.novel_context import load_history
from casefile.data_postgres.models import AgentModelCall, TaskRun

pytestmark = pytest.mark.postgres


class CompactEditor(Editor):
    def __init__(self, invalid=False):
        super().__init__()
        self.batches = []
        self.invalid_memory = invalid

    def compact(self, payload, key, model):
        self.batches.append(payload)
        candidate = {
            "items": [
                {
                    "kind": "author_preference",
                    "text": "保持动作自然",
                    "source_ids": [
                        999999 if self.invalid_memory else payload["new_turns"][-1]["id"]
                    ],
                }
            ]
        }
        return SimpleNamespace(
            candidate=candidate,
            raw_response=json.dumps(candidate),
            usage={"requests": 1, "total_tokens": 30},
        )


def test_rolling_memory_reuses_checkpoint_and_journals_usage(workflow_database):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    provider = CompactEditor()
    ids = []
    for i in range(7):
        exchange = submit(db, factory, project, novel, mode="discuss", request_key=f"memory-{i}")
        ids.append(exchange["id"])
        run(db, factory, exchange, provider)
        with factory() as session:
            task = session.get(TaskRun, exchange["task_id"])
            assert task.status == "succeeded"
            assert task.result_jsonb["context_manifest"]["estimated_payload_tokens"] < 48000
    assert [[t["id"] for t in p["new_turns"]] for p in provider.batches] == [
        [ids[0]],
        [ids[1]],
        [ids[2]],
    ]
    assert provider.batches[1]["old_memory"]["through_id"] == ids[0]
    with factory() as session:
        calls = list(
            session.scalars(
                select(AgentModelCall).where(AgentModelCall.task_run_id == exchange["task_id"])
            )
        )
        assert len(calls) == 2
        assert any(c.prompt_component_id == "novel_context_compactor" for c in calls)
        assert load_history(session, novel["id"], "other-chapter", False)["recent"] == []
        assert load_history(session, novel["id"], "c1", True)["recent"] == []


def test_invalid_summary_does_not_advance_memory(workflow_database):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    provider = CompactEditor(invalid=True)
    for i in range(5):
        exchange = submit(
            db, factory, project, novel, mode="discuss", request_key=f"bad-memory-{i}"
        )
        run(db, factory, exchange, provider)
    with factory() as session:
        task = session.get(TaskRun, exchange["task_id"])
        assert task.status == "failed"
        assert not (task.result_jsonb or {}).get("context_memory")
        plan = load_history(session, novel["id"], "c1", False)
        assert len(plan["batches"][0]) == 1


def test_new_conversation_excludes_old_history_and_memory(workflow_database):
    db = workflow_database
    factory, project, novel, _ = prepare(db)
    provider = CompactEditor()
    for i in range(5):
        exchange = submit(db, factory, project, novel, mode="discuss", request_key=f"old-{i}")
        run(db, factory, exchange, provider)
    fresh = submit(db, factory, project, novel, mode="discuss", request_key="fresh", history_after_exchange_id=exchange["id"])
    with factory() as session:
        task = session.get(TaskRun, fresh["task_id"])
        assert task.input_jsonb["context"]["history"] == []
        assert task.input_jsonb["history_plan"]["memory"] == {}
        assert task.input_jsonb["history_plan"]["batches"] == []
    run(db, factory, fresh, provider)
    with factory() as session:
        plan = load_history(session, novel["id"], "c1", False, exchange["id"])
        assert [t["id"] for t in plan["recent"]] == [fresh["id"]]
