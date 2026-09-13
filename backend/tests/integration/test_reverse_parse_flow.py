"""Postgres integration tests for the Path C reverse parse flow."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import _test_database_url
from fastapi.testclient import TestClient
from sqlalchemy import Engine, update
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime import FakeProvider
from casefile.api.app import create_app
from casefile.data_postgres.models.reverse_parse import ImportedDocument, ParseItem
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


def _identity(actor_id: int) -> dict[str, str]:
    return {"X-CaseFile-User-Id": str(actor_id)}


def test_upload_parse_confirm_form_brief_flow(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    app = create_app(_test_database_url())
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        setting = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "api_key": "sk-reverse-parse-test-secret",
                "model_id": "gpt-5.6-sol",
                "model_is_custom": False,
            },
        )
        assert setting.status_code == 200
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "反向解析集成", "description": None, "profile": {}},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        # 1. 上传 txt 文档 → 201，queued 文档 + reverse_parse 任务
        upload = client.post(
            f"/api/v1/projects/{project_id}/reverse-parse/documents",
            headers=_identity(actor_id),
            files={
                "file": (
                    "archive.txt",
                    (
                        "档案修复师林晚接手了一批封存档案。\n\n"
                        "三份记录都指向一段不存在的时间。\n\n"
                        "是谁改写了记录中的时间？"
                    ).encode(),
                    "text/plain",
                )
            },
        )
        assert upload.status_code == 201
        uploaded = upload.json()
        assert uploaded["document"]["parse_status"] == "queued"
        assert uploaded["task"]["task_type"] == "reverse_parse"
        document_id = uploaded["document"]["id"]

        # 2. Worker 执行解析任务 → 文档 succeeded 且 parse_items > 0
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="reverse-parse-test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True
        parsed = client.get(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}",
            headers=_identity(actor_id),
        ).json()
        assert parsed["document"]["parse_status"] == "succeeded"
        items = parsed["items"]
        assert len(items) > 0

        # 3. confirm / reject 翻转 confirm_status
        question = next(item for item in items if item["item_type"] == "candidate_question")
        confirmed = client.patch(
            f"/api/v1/projects/{project_id}/reverse-parse/items/{question['id']}",
            headers=_identity(actor_id),
            json={"action": "confirm"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirm_status"] == "confirmed"
        rejectable = next(item for item in items if item["id"] != question["id"])
        rejected = client.patch(
            f"/api/v1/projects/{project_id}/reverse-parse/items/{rejectable['id']}",
            headers=_identity(actor_id),
            json={"action": "reject"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["confirm_status"] == "rejected"

        # 4. 存在 grading=conflicting 未确认项时 form_brief 409
        with factory() as session, session.begin():
            session.execute(
                update(ParseItem)
                .where(
                    ParseItem.document_id == document_id,
                    ParseItem.confirm_status == "unconfirmed",
                )
                .values(grading="conflicting")
            )
        blocked = client.post(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}/form-brief",
            headers=_identity(actor_id),
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "high_risk_unconfirmed"

        # 全部确认/驳回后 form_brief 返回 confirmation 阶段的 legacy_import 候选
        refreshed = client.get(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}",
            headers=_identity(actor_id),
        ).json()
        for item in refreshed["items"]:
            if item["confirm_status"] == "unconfirmed":
                resolved = client.patch(
                    f"/api/v1/projects/{project_id}/reverse-parse/items/{item['id']}",
                    headers=_identity(actor_id),
                    json={"action": "reject"},
                )
                assert resolved.status_code == 200
        formed = client.post(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}/form-brief",
            headers=_identity(actor_id),
        )
        assert formed.status_code == 200
        intake_view = formed.json()
        assert intake_view["stage"] == "confirmation"
        current = next(c for c in intake_view["candidates"] if c["is_current"])
        assert current["origin"] == "legacy_import"

        # 5. retry_parse：succeeded 状态 409；failed 状态可排队新任务
        retry_conflict = client.post(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}/retry",
            headers=_identity(actor_id),
        )
        assert retry_conflict.status_code == 409
        assert retry_conflict.json()["code"] == "already_succeeded"

        with factory() as session, session.begin():
            document = session.get(ImportedDocument, document_id)
            assert document is not None
            document.parse_status = "failed"

        retried = client.post(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}/retry",
            headers=_identity(actor_id),
        )
        assert retried.status_code == 202
        assert retried.json()["task"]["task_type"] == "reverse_parse"
        requeued = client.get(
            f"/api/v1/projects/{project_id}/reverse-parse/documents/{document_id}",
            headers=_identity(actor_id),
        ).json()
        assert requeued["document"]["parse_status"] == "queued"
