"""Real PostgreSQL lifecycle, with zero live model calls."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from test_prose_shadow_runtime import _prepare

from casefile.agent_runtime import FakeProvider
from casefile.api.app import create_app
from casefile.application.errors import ApplicationError
from casefile.application.novel_collaboration import NovelCollaborationService
from casefile.application.novel_editor import NovelEditorService
from casefile.data_postgres.models import AgentModelCall, TaskRun
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


class Editor:
    def __init__(self, invalid=False):
        self.calls = 0
        self.invalid = invalid

    def edit(self, payload, key, model, repair=None):
        self.calls += 1
        candidate = {
            "message": "压缩重复表达。",
            "edits": [
                {"before": "雨落了。", "after": "雨落下来。", "reason": "让动作更连贯。"},
                {"before": "门开了。", "after": "木门缓缓推开。", "reason": "补充动作节奏。"},
            ],
        }
        if self.invalid:
            candidate["edits"][0]["before"] = "不存在的文本"
        return SimpleNamespace(
            candidate=candidate,
            raw_response=json.dumps(candidate),
            usage={"requests": 1, "total_tokens": 20},
        )

    def discuss(self, payload, key, model, emit):
        self.calls += 1
        emit("这是一次讨论。" * 20)
        return "这是讨论，不改正文。", {"requests": 1, "total_tokens": 20}


def prepare(db):
    factory, project, run, _ = _prepare(db)
    payload = {
        "draft_id": run["draft_id"],
        "source_key": "import-1",
        "source_label": "导入初稿",
        "title": "雨夜",
        "chapters": [{"id": "c1", "title": "第一章", "text": "雨落了。😀门开了。"}],
        "original_chapters": [{"id": "c1", "title": "第一章", "text": "雨落了。😀门开了。"}],
    }
    with factory() as s:
        novel = NovelEditorService(s).create(db[1], project, payload)
    return factory, project, novel, payload


def submit(db, factory, project, novel, mode="rewrite", **kwargs):
    payload = {
        "request_key": "request-1",
        "expected_revision": novel["revision"],
        "mode": mode,
        "scope": "chapter",
        "chapter_id": "c1",
        "instruction": "让动作更自然",
        "anchor": None,
        **kwargs,
    }
    with factory() as s:
        return NovelCollaborationService(s).submit(db[1], project, novel["id"], payload)


def run(db, factory, exchange, provider):
    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": db[2]}):
        Worker(
            factory,
            config=WorkerConfig(worker_id="novel-editor-test"),
            provider_factory=lambda _: FakeProvider(),
            novel_provider=provider,
        ).run_once(task_run_id=exchange["task_id"])


def test_import_save_history_and_ownership(workflow_database):
    db = workflow_database
    f, p, n, payload = prepare(db)
    with f() as s:
        assert NovelEditorService(s).create(db[1], p, payload)["id"] == n["id"]
        newer = NovelEditorService(s).save(
            db[1],
            p,
            n["id"],
            {
                "expected_revision": 1,
                "title": "雨夜",
                "chapters": [{"id": "c1", "title": "第一章", "text": "手工修改。"}],
            },
        )
        assert newer["revision"] == 2 and newer["original_chapters"] == payload["chapters"]
        with pytest.raises(ApplicationError):
            NovelEditorService(s).save(
                db[1],
                p,
                n["id"],
                {"expected_revision": 1, "title": "雨夜", "chapters": payload["chapters"]},
            )
        with pytest.raises(ApplicationError):
            NovelEditorService(s).get(db[1] + 999, p, n["id"])
        restored = NovelEditorService(s).restore(
            db[1], p, n["id"], {"expected_revision": 2, "revision": 1}
        )
        assert restored["revision"] == 3 and restored["chapters"] == payload["chapters"]
    with f() as s, s.begin():
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE novel_chapters SET text='bad'"))


@pytest.mark.parametrize("mode", ["discuss", "rewrite", "polish"])
def test_modes_and_partial_adoption(workflow_database, mode):
    db = workflow_database
    f, p, n, _ = prepare(db)
    e = submit(db, f, p, n, mode)
    provider = Editor()
    run(db, f, e, provider)
    with f() as s:
        view = NovelEditorService(s).get(db[1], p, n["id"])
        reply = view["exchanges"][0]
        assert reply["status"] == "succeeded", s.get(TaskRun, e["task_id"]).error_details_jsonb
        assert view["revision"] == 1 and provider.calls == 1
        if mode == "discuss":
            assert not reply["edits"]
            return
        edits = reply["edits"]
        assert edits[1]["start"] == 5
        first = {"expected_revision": 1, "edit_ids": [edits[0]["id"]], "action": "accept"}
        view = NovelEditorService(s).decide(db[1], p, n["id"], e["id"], first)
        assert view["revision"] == 2
        assert NovelEditorService(s).decide(db[1], p, n["id"], e["id"], first)["revision"] == 2
        view = NovelEditorService(s).decide(
            db[1],
            p,
            n["id"],
            e["id"],
            {"expected_revision": 2, "edit_ids": [edits[1]["id"]], "action": "accept"},
        )
        assert view["chapters"][0]["text"] == "雨落下来。😀木门缓缓推开。"


def test_stale_candidate_does_not_overwrite(workflow_database):
    db = workflow_database
    f, p, n, _ = prepare(db)
    e = submit(db, f, p, n)
    run(db, f, e, Editor())
    with f() as s:
        view = NovelEditorService(s).get(db[1], p, n["id"])
        edit = view["exchanges"][0]["edits"][0]
        NovelEditorService(s).save(
            db[1],
            p,
            n["id"],
            {
                "expected_revision": 1,
                "title": "雨夜",
                "chapters": [{"id": "c1", "title": "第一章", "text": "新的正文。"}],
            },
        )
        with pytest.raises(ApplicationError):
            NovelEditorService(s).decide(
                db[1],
                p,
                n["id"],
                e["id"],
                {"expected_revision": 2, "edit_ids": [edit["id"]], "action": "accept"},
            )


def test_protocol_repair_bounded_and_cancelled_queue(workflow_database):
    db = workflow_database
    f, p, n, _ = prepare(db)
    e = submit(db, f, p, n)
    provider = Editor(invalid=True)
    run(db, f, e, provider)
    assert provider.calls == 2
    with f() as s:
        reply = NovelEditorService(s).get(db[1], p, n["id"])["exchanges"][0]
        assert reply["status"] == "failed" and not reply["edits"]
    e2 = submit(db, f, p, n, request_key="request-2")
    with f() as s, s.begin():
        s.execute(update(TaskRun).where(TaskRun.id == e2["task_id"]).values(status="cancelling"))
    run(db, f, e2, provider)
    assert provider.calls == 2
    with f() as s:
        assert (
            len(
                list(
                    s.scalars(
                        select(AgentModelCall).where(AgentModelCall.task_run_id == e["task_id"])
                    )
                )
            )
            == 2
        )


def test_http_contract_stream_replay_and_no_duplicate_submission(workflow_database):
    db = workflow_database
    f, p, n, _ = prepare(db)
    body = {
        "request_key": "http-discuss",
        "expected_revision": 1,
        "mode": "discuss",
        "scope": "chapter",
        "chapter_id": "c1",
        "instruction": "讨论这段",
        "anchor": None,
    }
    headers = {"X-CaseFile-User-Id": str(db[1])}
    with TestClient(create_app(db[0].url.render_as_string(hide_password=False))) as client:
        path = f"/api/v1/projects/{p}/novels/{n['id']}/collaborations"
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 202, response.text
        exchange = response.json()
        assert (
            client.post(path, headers=headers, json=body).json()["task_id"] == exchange["task_id"]
        )
        assert client.post(path, headers=headers, json={**body, "scope": "book"}).status_code == 422
        provider = Editor()
        run(db, f, exchange, provider)
        stream = client.get(
            f"/api/v1/projects/{p}/tasks/{exchange['task_id']}/stream", headers=headers
        )
        assert stream.status_code == 200 and "novel.answer.delta" in stream.text
        assert "sk-" not in stream.text
        run(db, f, exchange, provider)
        assert provider.calls == 1


def test_cancellation_after_model_response_never_creates_edits(workflow_database):
    db = workflow_database
    f, p, n, _ = prepare(db)
    exchange = submit(db, f, p, n)

    class CancellingEditor(Editor):
        def edit(self, *args):
            result = super().edit(*args)
            with f() as s, s.begin():
                s.execute(
                    update(TaskRun)
                    .where(TaskRun.id == exchange["task_id"])
                    .values(status="cancelling")
                )
            return result

    provider = CancellingEditor()
    run(db, f, exchange, provider)
    with f() as s:
        view = NovelEditorService(s).get(db[1], p, n["id"])
    assert view["exchanges"][0]["status"] == "cancelled"
    assert not view["exchanges"][0]["edits"] and view["revision"] == 1
