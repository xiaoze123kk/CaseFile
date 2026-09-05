"""Ensure the reusable test schema still isolates committed application data."""

from unittest.mock import patch

import pytest
from alembic import command
from application_services_test_support import _prepare_task, workflow_database
from casefile.agent_runtime import FakeProvider
from casefile.data_postgres.models import TaskRun
from casefile.data_postgres.session import EXPECTED_DATABASE_REVISION, current_database_revision
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_reuses_schema_and_cleans_independently_committed_data() -> None:
    with (
        patch.object(command, "upgrade", wraps=command.upgrade) as upgrade,
        patch.object(command, "downgrade", side_effect=AssertionError("unexpected downgrade")),
    ):
        first = workflow_database.__wrapped__()
        engine, actor_id, _key = next(first)
        try:
            assert actor_id == 1
            with engine.connect() as connection:
                schema_identity = connection.execute(text(
                    "SELECT oid, relname FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace ORDER BY oid"
                )).all()
            _project, run_id = _prepare_task(engine, actor_id)
            factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
            assert Worker(
                factory, config=WorkerConfig(worker_id="isolation-regression"),
                provider_factory=lambda _task: FakeProvider(),
            ).run_once(task_run_id=run_id)
            # Worker and API-style sessions commit separately; outer rollback cannot clean them.
            with factory() as session:
                task = session.scalar(select(TaskRun).where(TaskRun.id == run_id))
                assert task is not None and task.status == "succeeded"
            migrations_after_first = upgrade.call_count
        finally:
            first.close()

        second = workflow_database.__wrapped__()
        engine, actor_id, _key = next(second)
        try:
            assert actor_id == 1
            assert upgrade.call_count == migrations_after_first
            assert current_database_revision(engine) == EXPECTED_DATABASE_REVISION
            with engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT oid, relname FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace ORDER BY oid"
                )).all() == schema_identity
                quote = engine.dialect.identifier_preparer.quote
                for table in inspect(connection).get_table_names():
                    if table not in {"users", "alembic_version"}:
                        assert connection.scalar(text(
                            f"SELECT count(*) FROM {quote(table)}"
                        )) == 0, table
        finally:
            second.close()
