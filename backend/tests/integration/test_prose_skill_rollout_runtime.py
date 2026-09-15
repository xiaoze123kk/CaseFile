"""Real Worker executes new defaults and preserves an explicitly frozen v11 run."""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_prose_shadow_runtime import _prepare, _providers, _result, _run

from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.data_postgres.models import AgentModelCall

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("legacy", [False, True])
def test_worker_uses_frozen_writer_version(workflow_database, monkeypatch, legacy):
    def blocked(*args, **kwargs):
        raise AssertionError("No live provider calls allowed")

    monkeypatch.setattr("openai._base_client.SyncAPIClient.request", blocked)
    if legacy:
        with patch(
            "casefile.application.compiler.service.prose_runtime_binding",
            side_effect=lambda count, mode: prose_runtime_binding(
                count, mode, runtime_version="prose-shadow-runtime-v11"
            ),
        ):
            factory, _, run, _ = _prepare(workflow_database, prose_mode="quick_draft")
    else:
        factory, _, run, _ = _prepare(workflow_database, prose_mode="quick_draft")
    _run(factory, run, _providers(), workflow_database[2])
    _, manifest, _ = _result(factory, run)
    assert manifest["shadow_status"] == "succeeded"
    with factory() as session:
        calls = list(
            session.scalars(
                select(AgentModelCall).where(
                    AgentModelCall.task_run_id == run["task_run_id"],
                    AgentModelCall.prompt_component_id == "prose_writer",
                )
            )
        )
    assert len(calls) == 2
    assert {c.prompt_version for c in calls} == {"prose-writer-v4" if legacy else "prose-writer-v6"}
