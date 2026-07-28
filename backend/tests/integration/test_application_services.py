"""PostgreSQL integration tests for the Brief-to-Draft application workflow."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.credentials import generate_master_key
from casefile.agent_runtime.models import GenerationRequest, GenerationResult, ToolMetrics
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.snapshot import casefile_content_hash
from casefile.application.v1_editing import V1EditingService
from casefile.application.workflow_service import WorkflowService
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    BriefVersion,
    DraftSnapshot,
    TaskAttempt,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE = {
    "content_type": "interactive_reasoning",
    "target_audience": "adult_general",
    "primary_use_case": "idea_to_playtest",
    "genres": ["mystery"],
    "target_duration_minutes": 90,
    "target_participant_count": 4,
    "difficulty_template": "medium",
    "collaboration_mode": "single_lead_review",
}
BRIEF = {
    "source_text": "一艘渡轮每天午夜会重新驶回同一座码头。",
    "one_line_concept": "玩家需要在重复靠岸前找出让渡轮回航的真实原因。",
    "core_mystery": "是谁修改了航行记录，以及回航是否在保护乘客？",
    "player_goal": "重建最后一小时的航行事实并决定是否终止回航。",
    "gameplay_loop": "调查舱室，交换信息，提出假设，验证记录，做出决定。",
    "constraints": ["真相必须唯一且可由公开线索验证"],
    "open_questions": ["船长缺失的十二分钟记录在哪里？"],
    "project_profile": PROFILE,
}


class RichFixtureProvider:
    """Return a deterministic, fully populated v1 fixture for editing tests."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        fixture_path = BACKEND_ROOT.parent / "fixtures" / "casefiles" / "restart_loop.casefile.json"
        candidate = json.loads(fixture_path.read_text(encoding="utf-8"))
        candidate["casefile_id"] = request.casefile_id
        candidate["version"] = {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        }
        candidate["brief_ref"] = {
            "brief_id": request.brief_id,
            "version": request.brief_version,
        }
        candidate["project_profile"] = request.project_profile
        for constraint in candidate["constraints"]:
            for scope_ref in constraint["scope_refs"]:
                if scope_ref["object_type"] == "casefile":
                    scope_ref["object_id"] = request.casefile_id
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1, adopted_results=1),
        )


def _test_database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("CASEFILE_TEST_DATABASE_URL must point to a disposable *_test database")
    return value


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture
def workflow_database() -> Iterator[tuple[Engine, int, str]]:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
    master_key = generate_master_key()
    with patch.dict(
        os.environ,
        {"DATABASE_URL": database_url, "CASEFILE_MASTER_KEY": master_key},
    ):
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                actor_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Workflow Owner') RETURNING id"
                        )
                    ).scalar_one()
                )
            yield engine, actor_id, master_key
        finally:
            engine.dispose()
            command.downgrade(config, "base")


def _prepare_task(engine: Engine, actor_id: int) -> tuple[int, int]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="午夜回航", description=None, profile=PROFILE),
        )
    project_id = int(project["id"])
    with factory() as session:
        workflow = WorkflowService(session)
        empty_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert empty_draft["content"] is None
        setting = workflow.save_provider_setting(
            actor_id,
            api_key="sk-test-workflow-secret",
            model_id="gpt-5.6-sol",
            model_is_custom=False,
        )
        assert setting["masked_api_key"].endswith("cret")
        updated = workflow.update_brief(
            actor_id,
            project_id,
            expected_revision=1,
            content=BRIEF,
        )
        confirmed = workflow.confirm_brief(
            actor_id,
            project_id,
            expected_revision=updated["draft_revision"],
        )
        task = workflow.create_generation_task(
            actor_id,
            project_id,
            brief_version_id=confirmed["brief_version_id"],
            expected_draft_revision=1,
        )
    return project_id, int(task["task_run_id"])


def test_fake_worker_writes_exact_roundtrip_snapshot_and_metrics(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True
        assert worker.run_once() is False

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            error_details = session.scalar(
                select(TaskRun.error_details_jsonb).where(TaskRun.id == task_run_id)
            )

        assert task["status"] == "succeeded", (task, events, error_details)
        assert task["result_snapshot_id"] is not None
        assert task["usage"]["tools"]["execution_success_rate"] == 1.0
        assert draft["revision"] == 2
        assert draft["content"]["title"] == BRIEF["one_line_concept"]
        assert [event["sequence_no"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert events[-1]["event_type"] == "task.succeeded"

        with engine.connect() as connection:
            ciphertext = connection.execute(
                select(UserProviderSetting.secret_ciphertext).where(
                    UserProviderSetting.user_id == actor_id
                )
            ).scalar_one()
            snapshot_json, snapshot_hash = connection.execute(
                select(DraftSnapshot.snapshot_jsonb, DraftSnapshot.content_hash).where(
                    DraftSnapshot.id == task["result_snapshot_id"]
                )
            ).one()
        assert b"sk-test-workflow-secret" not in bytes(ciphertext)
        assert snapshot_json == draft["content"]
        assert snapshot_hash == casefile_content_hash(draft["content"])


def test_expired_lease_creates_a_new_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        first = Worker(factory, config=WorkerConfig(worker_id="worker-a", lease_seconds=1))
        assert first._claim_next() is not None
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_runs SET lease_expires_at = "
                    "CURRENT_TIMESTAMP - INTERVAL '1 second'"
                )
            )
        second = Worker(factory, config=WorkerConfig(worker_id="worker-b", lease_seconds=60))
        claimed = second._claim_next()
        assert claimed is not None
        with factory() as session:
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_run_id == task_run_id)
                    .order_by(TaskAttempt.attempt_no)
                )
            )
        assert [attempt.status for attempt in attempts] == ["failed", "running"]
        assert attempts[0].error_code == "worker_lease_expired"


def test_confirmed_brief_and_task_events_are_database_immutable(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("UPDATE brief_versions SET version_no = 9"))
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("UPDATE task_events SET stage = 'changed' WHERE task_run_id = :task_id"),
                {"task_id": task_run_id},
            )
        with engine.connect() as connection:
            assert connection.execute(select(BriefVersion.version_no)).scalar_one() == 1
            assert connection.execute(select(TaskEvent.stage)).scalar_one() == "queued"


def test_v1_editing_updates_supported_objects_and_preserves_contract(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="editing-test-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            service = CaseFileService(session)
            initial = service.get_draft(actor_id, project_id)
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
            error_details = session.scalar(
                select(TaskRun.error_details_jsonb).where(TaskRun.id == task_run_id)
            )
            attempt_candidate = session.scalar(
                select(TaskAttempt.candidate_jsonb).where(TaskAttempt.task_run_id == task_run_id)
            )
            assert task["status"] == "succeeded", (
                task,
                error_details,
                attempt_candidate is not None,
            )
            assert initial["revision"] == 2
            content = initial["content"]
            entity_id = content["entities"][0]["id"]
            location_id = content["locations"][0]["id"]
            event_id = content["events"][0]["id"]
            read_only_id = content["resolution_specs"][0]["id"]

        with factory() as session:
            entity, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=2,
                changes={
                    "name": "Edited investigator",
                    "description": "Edited entity description",
                    "traits": ["careful", "persistent"],
                },
            )
            assert revision == 3
            assert entity["name"] == "Edited investigator"
            assert entity["description"] == "Edited entity description"
            assert entity["traits"] == ["careful", "persistent"]

        with factory() as session:
            location, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                location_id,
                expected_revision=3,
                changes={
                    "name": "Edited laboratory",
                    "description": "Edited location description",
                },
            )
            assert revision == 4
            assert location["name"] == "Edited laboratory"
            assert location["description"] == "Edited location description"

        with factory() as session:
            event, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                event_id,
                expected_revision=4,
                changes={
                    "title": "Edited restart event",
                    "description": "Edited event description",
                },
            )
            assert revision == 5
            assert event["title"] == "Edited restart event"
            assert event["description"] == "Edited event description"

        with factory() as session:
            final = CaseFileService(session).get_draft(actor_id, project_id)
            assert final["revision"] == 5
            validate_casefile(final["content"])

        with factory() as session, pytest.raises(ApplicationError) as read_only:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                read_only_id,
                expected_revision=5,
                changes={"title": "Forbidden"},
            )
        assert read_only.value.code == "object_read_only"

        with factory() as session, pytest.raises(ApplicationError) as field_read_only:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=5,
                changes={"id": "ent_replaced"},
            )
        assert field_read_only.value.code == "field_read_only"

        with factory() as session, pytest.raises(ApplicationError) as conflict:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=4,
                changes={"name": "Stale edit"},
            )
        assert conflict.value.code == "draft_revision_conflict"
