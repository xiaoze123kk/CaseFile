"""Application-level acceptance for one Current Draft among many Drafts."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from application_services_test_support import PROFILE, _adopt_candidate, _prepare_task
from casefile.agent_runtime import FakeProvider
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.v1_editing import V1EditingService
from casefile.application.workbench_read_model import WorkbenchReadModel
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AuditEvent,
    CanonVersion,
    Draft,
    DraftSnapshot,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def _prepare_two_drafts(engine: Engine, actor_id: int) -> tuple[int, int, int]:
    project_id, first_task_id = _prepare_task(engine, actor_id)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    worker = Worker(
        factory,
        config=WorkerConfig(worker_id="multiple-draft-worker"),
        provider_factory=lambda _task: FakeProvider(),
    )
    assert worker.run_once() is True
    adopted_a = _adopt_candidate(engine, actor_id, project_id, first_task_id)
    draft_a_id = int(adopted_a["draft_id"])

    with factory() as session:
        brief = WorkflowService(session).get_brief(actor_id, project_id)
        draft_a = CaseFileService(session).get_draft(actor_id, project_id)
        second_task = WorkflowService(session).create_generation_task(
            actor_id,
            project_id,
            brief_version_id=int(brief["current_version_id"]),
            expected_draft_id=draft_a_id,
            expected_draft_revision=int(draft_a["revision"]),
        )
    assert worker.run_once() is True
    adopted_b = _adopt_candidate(
        engine,
        actor_id,
        project_id,
        int(second_task["task_run_id"]),
        expected_current_draft_id=draft_a_id,
    )
    return project_id, draft_a_id, int(adopted_b["draft_id"])


def test_activation_is_atomic_and_rejects_stale_foreign_archived_or_locked_targets(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, draft_a_id, draft_b_id = _prepare_two_drafts(engine, actor_id)

        def activate_a() -> str:
            try:
                with factory() as session:
                    CaseFileService(session).activate_draft(
                        actor_id,
                        project_id,
                        draft_a_id,
                        expected_current_draft_id=draft_b_id,
                    )
                return "activated"
            except ApplicationError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(lambda _index: activate_a(), range(2)))
        assert outcomes == ["activated", "current_draft_changed"]

        with factory() as session:
            persisted = CaseFileService(session).get_project(actor_id, project_id)
            CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_b_id,
                expected_current_draft_id=draft_a_id,
            )
        assert persisted["current_draft_id"] == draft_a_id

        with factory() as session, pytest.raises(ApplicationError) as stale:
            CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_a_id,
            )
        assert stale.value.code == "current_draft_changed"
        assert "刷新" in stale.value.message

        with factory() as session:
            foreign_project = CaseFileService(session).create_project(
                actor_id,
                ProjectCreate(title="外部项目", description=None, profile=PROFILE),
            )
        with factory() as session, pytest.raises(ApplicationError) as foreign:
            CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                int(foreign_project["current_draft_id"]),
                expected_current_draft_id=draft_b_id,
            )
        assert foreign.value.code == "not_found"

        with factory() as session:
            CaseFileService(session).archive_project(actor_id, project_id)
        with factory() as session:
            archived_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert archived_draft["document_status"] == "archived"
        assert archived_draft["content"]["status"] == "archived"
        with factory() as session, pytest.raises(ApplicationError) as archived:
            CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_b_id,
            )
        assert archived.value.code == "project_archived"
        assert "已归档" in archived.value.message

        with factory() as session:
            CaseFileService(session).unarchive_project(actor_id, project_id)
        with factory() as session:
            unarchived_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert unarchived_draft["document_status"] == "draft"
        assert unarchived_draft["content"]["status"] == "draft"
        with factory() as session, session.begin():
            session.execute(update(Draft).where(Draft.id == draft_a_id).values(status="locked"))
        with factory() as session, pytest.raises(ApplicationError) as locked:
            CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_b_id,
            )
        assert locked.value.code == "draft_locked"
        assert "已锁定" in locked.value.message


def test_candidate_branch_inherits_canon_base_and_audits_current_switch(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    worker = Worker(
        factory,
        config=WorkerConfig(worker_id="canon-branch-worker"),
        provider_factory=lambda _task: FakeProvider(),
    )
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, first_task_id = _prepare_task(engine, actor_id)
        assert worker.run_once() is True
        adopted_a = _adopt_candidate(engine, actor_id, project_id, first_task_id)
        draft_a_id = int(adopted_a["draft_id"])

        with factory() as session, session.begin():
            task = session.get(TaskRun, first_task_id)
            assert task is not None and task.result_snapshot_id is not None
            snapshot = session.get(DraftSnapshot, task.result_snapshot_id)
            assert snapshot is not None
            canon = CanonVersion(
                project_id=snapshot.project_id,
                casefile_id=snapshot.casefile_id,
                parent_canon_version_id=None,
                source_snapshot_id=snapshot.id,
                version_no=1,
                schema_version=snapshot.schema_version,
                content_jsonb=snapshot.snapshot_jsonb,
                content_hash=snapshot.content_hash,
                confirmed_by_user_id=actor_id,
            )
            session.add(canon)
            session.flush()
            canon_id = canon.id

        with factory() as session:
            workflow = WorkflowService(session)
            brief = workflow.get_brief(actor_id, project_id)
            source = CaseFileService(session).get_draft(actor_id, project_id)
            assert source["draft_id"] == draft_a_id
            assert source["document_status"] == "canon"
            second_task = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=int(brief["current_version_id"]),
                expected_draft_id=draft_a_id,
                expected_draft_revision=int(source["revision"]),
            )
        assert worker.run_once() is True
        adopted_b = _adopt_candidate(
            engine,
            actor_id,
            project_id,
            int(second_task["task_run_id"]),
            expected_current_draft_id=draft_a_id,
        )
        draft_b_id = int(adopted_b["draft_id"])

        with factory() as session:
            draft_b = session.get(Draft, draft_b_id)
            activation_events = list(
                session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.project_id == project_id,
                        AuditEvent.action == "draft.activated",
                    )
                    .order_by(AuditEvent.id)
                )
            )
        assert draft_b is not None
        assert draft_b.base_canon_version_id == canon_id
        assert len(activation_events) == 1
        assert activation_events[0].target_type == "draft"
        assert activation_events[0].target_id == draft_b_id
        assert activation_events[0].details_jsonb == {
            "previous_draft_id": draft_a_id,
            "task_run_id": int(second_task["task_run_id"]),
        }


def test_switching_isolates_edits_snapshots_validation_sources_and_audit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, draft_a_id, draft_b_id = _prepare_two_drafts(engine, actor_id)

        with factory() as session:
            draft_b_before = CaseFileService(session).get_draft(actor_id, project_id)
            draft_a = CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_b_id,
            )
        entity_a_id = draft_a["content"]["entities"][0]["id"]
        with factory() as session:
            edited_a, revision_a = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_a_id,
                expected_draft_id=draft_a_id,
                expected_revision=2,
                changes={"name": "工作稿 A 独立人物"},
            )
        assert revision_a == 3

        with factory() as session:
            snapshot_a, created = CaseFileService(session).create_snapshot(
                actor_id,
                project_id,
                draft_a_id,
                revision_a,
            )
        assert created is True
        assert snapshot_a["draft_id"] == draft_a_id

        with factory() as session:
            context_a = WorkbenchReadModel(session).get_context(actor_id, project_id)
            snapshots_a = CaseFileService(session).list_snapshots(actor_id, project_id)
            draft_b = CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_b_id,
                expected_current_draft_id=draft_a_id,
            )
        assert context_a["draft_id"] == draft_a_id
        assert context_a["validation"]["status"] == "passed"
        assert context_a["sources"]
        assert any(
            entry["action"] == "logical_mutation_apply"
            for entry in context_a["audit_entries"]
        )
        assert snapshots_a
        assert {item["draft_id"] for item in snapshots_a} == {draft_a_id}
        assert snapshot_a["id"] in {item["id"] for item in snapshots_a}
        assert draft_b["content"] == draft_b_before["content"]

        entity_b_id = draft_b["content"]["entities"][0]["id"]
        with factory() as session:
            edited_b, revision_b = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_b_id,
                expected_draft_id=draft_b_id,
                expected_revision=2,
                changes={"name": "工作稿 B 独立人物"},
            )
        assert revision_b == 3

        with factory() as session:
            context_b = WorkbenchReadModel(session).get_context(actor_id, project_id)
            snapshots_b = CaseFileService(session).list_snapshots(actor_id, project_id)
            restored_a = CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_b_id,
            )
        assert context_b["draft_id"] == draft_b_id
        assert context_b["validation"]["status"] == "passed"
        assert context_b["sources"] == context_a["sources"]
        assert snapshots_b
        assert {item["draft_id"] for item in snapshots_b} == {draft_b_id}
        assert edited_a["name"] == "工作稿 A 独立人物"
        assert edited_b["name"] == "工作稿 B 独立人物"
        assert restored_a["content"]["entities"][0]["name"] == edited_a["name"]

        with factory() as session:
            operations_by_draft = dict(
                session.execute(
                    select(Draft.id, Draft.revision).where(
                        Draft.id.in_((draft_a_id, draft_b_id))
                    )
                ).all()
            )
        assert operations_by_draft == {draft_a_id: 3, draft_b_id: 3}
