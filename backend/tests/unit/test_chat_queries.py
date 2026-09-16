"""Evidence-query behavior, frozen tool surfaces and revision access boundaries."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext
from sqlalchemy import Integer, create_engine, text
from sqlalchemy.orm import sessionmaker
from test_chat_tools import make_request

from casefile.agent_runtime.chat_queries import query_character_knowledge, query_modification_impact
from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V4_VERSION,
    CHAT_TOOLSET_V5_VERSION,
    ChatToolContext,
    chat_tool_manifest,
    compare_draft_revisions,
    get_modification_impact,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.application.workflow.tasks import new_task
from casefile.data_postgres.models import DraftOperation
from casefile.worker.revision_history import read_revision_history


def test_impact_follows_dependency_chain_without_changing_document() -> None:
    path = Path(__file__).resolve().parents[3] / "fixtures/casefiles/restart_loop.casefile.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    before = deepcopy(document)
    result = query_modification_impact(document, "info_restart_log")
    records = {row["id"]: row for row in result["results"]}
    assert records["claim_backup_trigger"]["distance"] == "direct"
    assert records["res_root_cause"]["dependency_path"] == [
        "info_restart_log",
        "claim_backup_trigger",
        "res_root_cause",
    ]
    assert result["scope"] == "potential_dependencies_in_frozen_document"
    assert query_modification_impact(document, "missing") == {"error": "object_not_found"}
    assert document == before


def test_knowledge_does_not_infer_missing_event_or_conflate_beliefs() -> None:
    state = {
        "as_of_event_ref": {"object_type": "event", "object_id": "evt_a"},
        "knows_refs": ["info_a"],
        "believes_refs": ["claim_a"],
        "false_belief_refs": ["claim_b"],
    }
    document = {
        "entities": [{"id": "person_a", "knowledge_states": [state]}],
        "events": [{"id": "evt_a"}, {"id": "evt_b"}],
    }
    result = query_character_knowledge(document, "person_a", "evt_a")
    assert result["results"][0] == {"source_path": "/knowledge_states/0", **state}
    assert query_character_knowledge(document, "person_a", "evt_b")["status"] == "not_recorded"
    assert query_character_knowledge(document, "person_a", "missing")["error"] == "event_not_found"
    assert query_character_knowledge(document, "missing", None)["error"] == "character_not_found"


def test_knowledge_uses_real_contract_object_references() -> None:
    path = Path(__file__).resolve().parents[3] / "fixtures/casefiles/restart_loop.casefile.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    result = query_character_knowledge(document, "ent_researcher", "evt_restart_seven")
    assert result["status"] == "recorded"
    assert result["results"][0]["knows_refs"] == [
        {"object_type": "information_unit", "object_id": "info_restart_log"}
    ]


def test_v5_manifest_and_prompt_keep_old_tool_surface_frozen() -> None:
    request = make_request(toolset=["get_casefile_object"], max_tool_calls=3)
    assert request.route is not None
    assert [
        tool.name
        for tool in chat_tool_manifest(request.route, toolset_version=CHAT_TOOLSET_V4_VERSION)
    ] == ["get_casefile_object"]
    assert {
        tool.name
        for tool in chat_tool_manifest(request.route, toolset_version=CHAT_TOOLSET_V5_VERSION)
    } == {
        "get_casefile_object",
        "get_modification_impact",
        "get_character_knowledge",
        "compare_draft_revisions",
    }
    package = load_prompt("casefile_chat", "casefile-chat-v24").package
    assert package is not None
    assert package.runtime_toolset_version == CHAT_TOOLSET_V5_VERSION


def test_new_tools_obey_budget_and_report_missing_history() -> None:
    request = make_request(toolset=["get_casefile_object"], max_tool_calls=1)
    assert request.route is not None
    context = ChatToolContext(request=request, route=request.route)
    wrapper = ToolContext.from_agent_context(
        RunContextWrapper(context),
        "call_1",
        tool_name="compare_draft_revisions",
        tool_arguments="{}",
    )
    first = json.loads(
        asyncio.run(
            compare_draft_revisions.on_invoke_tool(
                wrapper, '{"from_revision":1,"to_revision":2,"offset":0,"limit":1}'
            )
        )
    )
    assert first["error"] == "revision_history_unavailable"
    second = json.loads(
        asyncio.run(
            get_modification_impact.on_invoke_tool(
                wrapper, '{"object_id":"missing","offset":0,"limit":1}'
            )
        )
    )
    assert second["error"] == "tool_budget_exhausted"
    assert len(context.recent_tool_results) == 2


def test_history_rejects_future_revisions_and_nonowner_before_reading_rows() -> None:
    sessions = MagicMock()
    args = dict(
        sessions=sessions,
        actor_id=1,
        project_id=2,
        casefile_id=3,
        draft_id=4,
        frozen_revision=5,
        from_revision=1,
        to_revision=6,
        offset=0,
        limit=1,
    )
    assert read_revision_history(**args)["error"] == "revision_range_outside_frozen_draft"
    sessions.assert_not_called()
    args["to_revision"] = 5
    session = sessions.return_value.__enter__.return_value
    session.scalar.return_value = None
    assert read_revision_history(**args)["error"] == "revision_history_unavailable"
    session.scalars.assert_not_called()


def test_history_query_binds_task_identity_and_frozen_revision() -> None:
    sessions = MagicMock()
    session = sessions.return_value.__enter__.return_value
    session.scalar.return_value = 2
    session.scalars.return_value = []
    result = read_revision_history(
        sessions,
        actor_id=1,
        project_id=2,
        casefile_id=3,
        draft_id=4,
        frozen_revision=5,
        from_revision=2,
        to_revision=5,
        offset=0,
        limit=1,
    )
    statement = session.scalars.call_args.args[0]
    params = statement.compile().params
    assert params["project_id_1"] == 2
    assert params["casefile_id_1"] == 3
    assert params["draft_id_1"] == 4
    assert params["result_revision_1"] == 2
    assert params["result_revision_2"] == 5
    assert result["scope"] == "recorded_operations_not_net_diff"


def test_history_sql_isolates_drafts_and_pages_operations() -> None:
    # Exercise the real ORM SELECT against SQLite; no production database is touched.
    engine = create_engine("sqlite://")
    columns = ", ".join(
        f'"{column.name}" {"INTEGER" if isinstance(column.type, Integer) else "TEXT"}'
        for column in DraftOperation.__table__.columns
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE projects (id INTEGER, owner_user_id INTEGER)"))
        connection.execute(text("INSERT INTO projects VALUES (2, 1)"))
        connection.execute(text(f"CREATE TABLE draft_operations ({columns})"))
        for operation_id, draft_id, revision in [(1, 4, 2), (2, 99, 3), (3, 4, 3), (4, 4, 10)]:
            connection.execute(
                text(
                    "INSERT INTO draft_operations "
                    "(id, project_id, casefile_id, draft_id, sequence_no, base_revision, "
                    "result_revision, operation_type, field_path, actor_kind) "
                    "VALUES (:id, 2, 3, :draft, :id, :base, :revision, 'replace', '/name', 'user')"
                ),
                {"id": operation_id, "draft": draft_id, "base": revision - 1, "revision": revision},
            )
    sessions = sessionmaker(engine)
    args = dict(
        actor_id=1,
        project_id=2,
        casefile_id=3,
        draft_id=4,
        frozen_revision=5,
        from_revision=1,
        to_revision=5,
        offset=0,
        limit=1,
    )
    first = read_revision_history(sessions, **args)
    assert [str(row["operation_id"]) for row in first["results"]] == ["1"]
    assert first["next_offset"] == 1
    second = read_revision_history(sessions, **{**args, "offset": 1})
    assert [str(row["operation_id"]) for row in second["results"]] == ["3"]
    assert second["next_offset"] is None
    assert read_revision_history(sessions, **{**args, "actor_id": 99})["error"] == (
        "revision_history_unavailable"
    )
    engine.dispose()


def test_new_goal_task_freezes_matching_prompt_and_tool_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASEFILE_CHAT_GOAL_ROLLOUT", "active")
    monkeypatch.setattr(
        "casefile.application.workflow.tasks._chat_context_policy_version",
        lambda: "casefile-chat-context-v6",
    )
    owned = SimpleNamespace(
        project=SimpleNamespace(id=1),
        casefile=SimpleNamespace(id=2),
        draft=SimpleNamespace(id=3, revision=4),
    )
    setting = SimpleNamespace(
        id=1, provider="deepseek", model_id="test", config_version=1, default_budget_jsonb={}
    )
    task = new_task(
        owned,
        actor_user_id=1,
        setting=setting,
        task_type="casefile_chat",
        brief_version_id=None,
        input_source_record_id=None,
        input_brief_revision=None,
        input_hash="unused",
        input_jsonb={"message": "test"},
    )
    assert task.prompt_version == "casefile-chat-v27"
    assert task.toolset_version == "casefile-chat-tools-v6"
    assert task.input_draft_revision == 4
