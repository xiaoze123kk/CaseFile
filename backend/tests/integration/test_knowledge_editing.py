"""Knowledge edits through the production review, apply and history boundaries."""
from __future__ import annotations

import os
from copy import deepcopy
from unittest.mock import patch

import pytest
from application_services_test_support import (
    ChatSuggestionProvider,
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.agent_runtime.general_mutation import GeneralMutationPlannerResult, MutationPlanV1
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.v1_editing import V1EditingService
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import TaskRun
from casefile.domain.verification_engine import VerificationEngine
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


class KnowledgeFixtureProvider(RichFixtureProvider):
    def __init__(self, with_conflict: bool = True) -> None:
        self.with_conflict = with_conflict

    def generate(self, request):  # type: ignore[no-untyped-def]
        result = super().generate(request)
        document = result.candidate
        later = deepcopy(document["events"][0])
        later.update(id="evt_later", title="稍后的真相", time={
            "kind": "exact", "value": "2042-06-01T23:00", "precision": "minute",
        }, cause_refs=[], effect_refs=[], participant_refs=[], observed_by_refs=[])
        document["events"].append(later)
        if self.with_conflict:
            document["information_units"][0]["source_event_ref"]["object_id"] = later["id"]
        other = deepcopy(document["entities"][0])
        other.update(id="ent_second", name="第二位调查者")
        document["entities"].append(other)
        return result


class KnowledgePatchProvider(ChatSuggestionProvider):
    def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
        return GeneralMutationPlannerResult(MutationPlanV1.model_validate({
            "operations": [
                {
                    "operation_key": f"fix_{index}", "operation_type": "update_field",
                    "target": {"ref_kind": "existing", "object_id": object_id},
                    "field_path": "/knowledge_states/0/knows_refs", "new_value": [],
                    "reason": "移除该时点尚未产生的信息，保留后续真相及角色其他认知。",
                }
                for index, object_id in enumerate(("ent_researcher", "ent_second"))
            ],
        }), {})


def _knowledge(document):  # type: ignore[no-untyped-def]
    return {item["id"]: item["knowledge_states"] for item in document["entities"]}


@pytest.mark.parametrize("with_conflict", [False, True])
def test_knowledge_patch_review_apply_undo_redo(
    workflow_database: tuple[Engine, int, str], with_conflict: bool,
) -> None:
    engine, actor, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project, generation = _prepare_task(engine, actor)
        assert Worker(factory, config=WorkerConfig(worker_id="knowledge-fixture"),
                      provider_factory=lambda _: KnowledgeFixtureProvider(with_conflict)).run_once()
        draft = int(_adopt_candidate(engine, actor, project, generation)["draft_id"])
        with factory() as session:
            before = CaseFileService(session).get_draft(actor, project)["content"]
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(actor, project, expected_draft_id=draft,
                                                 expected_draft_revision=2, title=None)
            queued = workflow.send_agent_message(actor, project, thread["thread_id"],
                expected_draft_id=draft, expected_draft_revision=2,
                content="请修正两位调查者的所知范围，保留真相发生时间，生成待审修改。")
        baseline = VerificationEngine().verify(before)
        assert sum(f.rule_code == "knowledge_state_available_before_source"
                   for f in baseline.findings) == (2 if with_conflict else 0)
        assert Worker(factory, config=WorkerConfig(worker_id="knowledge-patch",
                      general_mutation_mode="suggest"),
                      provider_factory=lambda _: KnowledgePatchProvider()).run_once()
        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(actor, project, thread["thread_id"])
            patch_set = messages[-1]["patch_set"]
            if patch_set is None:
                task = session.get(TaskRun, int(queued["task"]["task_run_id"]))
                pytest.fail(str((task.error_details_jsonb, task.result_jsonb)))
            assert len(patch_set["operations"]) == 2
            assert _knowledge(CaseFileService(session).get_draft(actor, project)["content"]) == (
                _knowledge(before)
            )
            preview = workflow.simulate_agent_patch_set(actor, project, patch_set["patch_set_id"],
                expected_draft_id=draft, base_revision=2, operation_ids=None)
            assert preview["simulation"]["can_apply"]
            applied = workflow.apply_agent_patch_set(actor, project, patch_set["patch_set_id"],
                expected_draft_id=draft, expected_revision=2, operation_ids=None)
            assert applied["draft_revision"] == 3
        with factory() as session:
            after = CaseFileService(session).get_draft(actor, project)["content"]
            for object_id in ("ent_researcher", "ent_second"):
                state = _knowledge(after)[object_id][0]
                assert state["knows_refs"] == []
                assert state["believes_refs"] == _knowledge(before)[object_id][0]["believes_refs"]
            assert after["events"] == before["events"]
            assert after["information_units"] == before["information_units"]
            assert not any(f.rule_code == "knowledge_state_available_before_source"
                           for f in VerificationEngine().verify(after).findings)
            with pytest.raises(ApplicationError):
                V1EditingService(session).patch_object(actor, project, "ent_researcher",
                    expected_draft_id=draft, expected_revision=2, changes={"knowledge_states": []})
        if with_conflict:
            # Undo must not silently reintroduce hard timeline violations.
            with factory() as session, pytest.raises(ApplicationError):
                WorkflowService(session).undo_agent_patch_set(
                    actor, project, patch_set["patch_set_id"],
                    expected_draft_id=draft, expected_revision=3,
                )
            with factory() as session:
                restored = CaseFileService(session).get_draft(actor, project)
                assert restored["revision"] == 3
                assert _knowledge(restored["content"]) == _knowledge(after)
            return
        with factory() as session:
            WorkflowService(session).undo_agent_patch_set(actor, project, patch_set["patch_set_id"],
                expected_draft_id=draft, expected_revision=3)
        with factory() as session:
            assert _knowledge(CaseFileService(session).get_draft(actor, project)["content"]) == (
                _knowledge(before)
            )
            WorkflowService(session).redo_agent_patch_set(actor, project, patch_set["patch_set_id"],
                expected_draft_id=draft, expected_revision=4)
        with factory() as session:
            assert _knowledge(CaseFileService(session).get_draft(actor, project)["content"]) == (
                _knowledge(after)
            )


def test_knowledge_slot_count_and_invalid_references(workflow_database: tuple[Engine, int, str]):
    engine, actor, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project, generation = _prepare_task(engine, actor)
        assert Worker(factory, config=WorkerConfig(worker_id="knowledge-count"),
                      provider_factory=lambda _: RichFixtureProvider()).run_once()
        draft = int(_adopt_candidate(engine, actor, project, generation)["draft_id"])
        empty = {"as_of_event_ref": None, "knows_refs": [], "believes_refs": [],
                 "false_belief_refs": []}
        for revision, states in enumerate(([empty, empty], [], [empty]), start=2):
            with factory() as session:
                V1EditingService(session).patch_object(actor, project, "ent_researcher",
                    expected_draft_id=draft, expected_revision=revision,
                    changes={"knowledge_states": states})
            with factory() as session:
                assert _knowledge(CaseFileService(session).get_draft(actor, project)["content"])[
                    "ent_researcher"
                ] == states
        for bad_ref in ({"object_type": "information_unit", "object_id": "info_missing"},
                        {"object_type": "event", "object_id": "evt_restart_seven"}):
            with factory() as session, pytest.raises(ApplicationError):
                V1EditingService(session).patch_object(actor, project, "ent_researcher",
                    expected_draft_id=draft, expected_revision=5,
                    changes={"knowledge_states": [{**empty, "knows_refs": [bad_ref]}]})
        with factory() as session:
            final = CaseFileService(session).get_draft(actor, project)
            assert final["revision"] == 5
            assert _knowledge(final["content"])["ent_researcher"] == [empty]


@pytest.mark.parametrize("planner_fails", [False, True])
def test_followup_planner_receives_history_and_failure_does_not_claim_a_card(
    workflow_database: tuple[Engine, int, str], planner_fails: bool,
) -> None:
    from casefile.agent_runtime.models import CaseFileChatCandidate, CaseFileChatResult

    class ContextProvider(KnowledgePatchProvider):
        def __init__(self):
            super().__init__()
            self.planner_request = None

        def chat(self, request):
            return CaseFileChatResult(candidate=CaseFileChatCandidate(
                answer="已确认修复两位角色的所知范围，保留事件时间。修改卡片已经准备好。",
                referenced_object_ids=[], suggestions=[],
            ), usage={})

        def plan_general_mutation(self, request):
            self.planner_request = request
            assert request.message == "好，按刚才的方案生成补丁"
            assert any("保留事件时间" in item["content"] for item in request.thread_history)
            assert request.validation_issues
            if planner_fails:
                raise RuntimeError("test planner unavailable")
            return super().plan_general_mutation(request)

    engine, actor, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    provider = ContextProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project, generation = _prepare_task(engine, actor)
        assert Worker(factory, config=WorkerConfig(worker_id="context-fixture"),
                      provider_factory=lambda _: KnowledgeFixtureProvider()).run_once()
        draft = int(_adopt_candidate(engine, actor, project, generation)["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(actor, project, expected_draft_id=draft,
                                                 expected_draft_revision=2, title=None)
            workflow.send_agent_message(actor, project, thread["thread_id"],
                expected_draft_id=draft, expected_draft_revision=2,
                content="请解释两位角色所知范围的问题，保留事件时间。")
        assert Worker(factory, config=WorkerConfig(worker_id="context-discuss",
                      general_mutation_mode="off"), provider_factory=lambda _: provider).run_once()
        with factory() as session:
            WorkflowService(session).send_agent_message(actor, project, thread["thread_id"],
                expected_draft_id=draft, expected_draft_revision=2,
                content="好，按刚才的方案生成补丁")
        assert Worker(factory, config=WorkerConfig(worker_id="context-followup",
                      general_mutation_mode="suggest"),
                      provider_factory=lambda _: provider).run_once()
        assert provider.planner_request is not None
        with factory() as session:
            messages = WorkflowService(session).list_agent_messages(
                actor, project, thread["thread_id"],
            )
            message = messages[-1]
            if planner_fails:
                assert message["patch_set"] is None
                assert "未能生成" in message["content"]
                assert "已经准备好" not in message["content"]
            else:
                assert len(message["patch_set"]["operations"]) == 2
            assert CaseFileService(session).get_draft(actor, project)["revision"] == 2
