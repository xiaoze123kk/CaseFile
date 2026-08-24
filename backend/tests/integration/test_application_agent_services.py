"""PostgreSQL integration tests for the Brief-to-Draft application workflow."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace as dataclasses_replace
from unittest.mock import patch

import pytest
import rfc8785
from application_services_test_support import (
    PROFILE,
    ChatSuggestionProvider,
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.chat_intent import INTENT_ROUTER_VERSION
from casefile.agent_runtime.chat_routing import routing_policy
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerResult,
    MutationPlanV1,
)
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatResult,
    ChatTaskUnderstanding,
    agent_state_to_jsonable,
)
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.v1_editing import V1EditingService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.feedback_export import export_feedback_fixtures
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentPatchOperation,
    AgentPatchSet,
    AgentStepRun,
    AuditEvent,
    DraftOperation,
    TaskRun,
)
from casefile.domain.logical_mutation import CLOSURE_POLICY_V1, CLOSURE_POLICY_V2
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


class ClosureRepairFixtureProvider(RichFixtureProvider):
    def generate(self, request):  # type: ignore[no-untyped-def]
        result = super().generate(request)
        template = result.candidate["claims"][0]
        prerequisite = dict(template)
        prerequisite.update(
            id="claim_repair_prerequisite",
            title="修复前置主张",
            statement="隔离的前置主张。",
            dependency_claim_refs=[],
        )
        subject = dict(template)
        subject.update(
            id="claim_repair_subject",
            title="修复目标主张",
            statement="依赖前置主张。",
            dependency_claim_refs=[{"object_type": "claim", "object_id": prerequisite["id"]}],
        )
        result.candidate["claims"].extend((prerequisite, subject))
        result.candidate["information_units"][0]["supports_claim_refs"].extend(
            (
                {"object_type": "claim", "object_id": prerequisite["id"]},
                {"object_type": "claim", "object_id": subject["id"]},
            )
        )
        return result


def test_general_mutation_create_atomic_apply_undo_redo(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="m34-fixture"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请新增一个人物并生成可审阅的修改。",
            )
        Worker(
            factory,
            config=WorkerConfig(
                worker_id="m34-chat",
                general_mutation_mode="suggest",
                general_mutation_create_enabled=True,
            ),
            provider_factory=lambda _task: ChatSuggestionProvider(),
        ).run_once()

        with factory() as session:
            task = session.get(TaskRun, int(queued["task"]["task_run_id"]))
            assert task is not None and task.status == "succeeded", (
                None if task is None else task.error_details_jsonb
            )
        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(actor_id, project_id, thread["thread_id"])
            patch_set = messages[-1]["patch_set"]
            assert patch_set["review_mode"] == "atomic"
            assert patch_set["plan_version"] == "general-mutation-planner-v2"
            assert patch_set["operations"][0]["operation_type"] == "create_object"
            assert patch_set["operations"][0]["object_id"] is None
            assert patch_set["operations"][0]["target_object_key"].startswith("ent_agent_t")
            with pytest.raises(ApplicationError) as subset_error:
                workflow.simulate_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    base_revision=2,
                    operation_ids=[patch_set["operations"][0]["operation_id"]],
                )
            assert subset_error.value.code == "agent_patch_atomic_subset_forbidden"
            preview = workflow.simulate_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                base_revision=2,
                operation_ids=None,
            )
            debt_keys = preview["simulation"]["authorization_required_finding_keys"]
            applied = workflow.apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=None,
                accepted_debt_finding_keys=debt_keys,
                debt_acceptance_reason="测试确认新增对象尚待连接。",
            )
            assert applied["draft_revision"] == 3

        with factory() as session:
            workflow = WorkflowService(session)
            undone = workflow.undo_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=3,
            )
            assert undone["draft_revision"] == 4

        with factory() as session:
            redone = WorkflowService(session).redo_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=4,
            )
            assert redone["draft_revision"] == 5


def test_general_mutation_delete_requires_confirmed_impact_hash(
    workflow_database: tuple[Engine, int, str],
) -> None:
    class DeleteProvider(ChatSuggestionProvider):
        def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
            object_id = request.casefile["relationships"][0]["id"]
            return GeneralMutationPlannerResult(
                MutationPlanV1.model_validate(
                    {
                        "operations": [
                            {
                                "operation_key": "delete_entity",
                                "operation_type": "delete_object",
                                "target": {
                                    "ref_kind": "existing",
                                    "object_id": object_id,
                                },
                                "reason": "测试删除影响确认。",
                            }
                        ]
                    }
                ),
                {},
            )

    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="m34-delete-fixture"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请删除第一个人物。",
            )
        Worker(
            factory,
            config=WorkerConfig(
                worker_id="m34-delete-chat",
                general_mutation_mode="suggest",
                general_mutation_delete_enabled=True,
            ),
            provider_factory=lambda _task: DeleteProvider(),
        ).run_once()
        with factory() as session:
            events = WorkflowService(session).list_task_events(
                actor_id, project_id, int(queued["task"]["task_run_id"])
            )
        with factory() as session:
            task = session.get(TaskRun, int(queued["task"]["task_run_id"]))
            assert task is not None and task.status == "succeeded", events
        with factory() as session:
            messages = WorkflowService(session).list_agent_messages(
                actor_id, project_id, thread["thread_id"]
            )
            patch_set = messages[-1]["patch_set"]
            assert patch_set is not None, [
                (event["event_type"], event.get("payload")) for event in events
            ]
            assert patch_set["contains_delete"] is True
            target_object_key = patch_set["operations"][0]["target_object_key"]
            before = CaseFileService(session).get_draft(actor_id, project_id)
            original_target = next(
                item
                for item in before["content"]["relationships"]
                if item["id"] == target_object_key
            )
        with factory() as session:
            with pytest.raises(ApplicationError) as error:
                WorkflowService(session).apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=None,
                )
            assert error.value.code == "agent_patch_delete_impact_confirmation_required"
        with factory() as session:
            workflow = WorkflowService(session)
            with pytest.raises(ApplicationError) as error:
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=None,
                    confirmed_impact_hash="0" * 64,
                )
            assert error.value.code == "agent_patch_impact_hash_mismatch"
            preview = workflow.simulate_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                base_revision=2,
                operation_ids=None,
            )
            debt_keys = preview["simulation"]["authorization_required_finding_keys"]
            applied = workflow.apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=None,
                confirmed_impact_hash=patch_set["impact_hash"],
                accepted_debt_finding_keys=debt_keys,
                debt_acceptance_reason=("测试确认删除产生的逻辑债务。" if debt_keys else None),
            )
            assert applied["draft_revision"] == 3
        with factory() as session:
            after = CaseFileService(session).get_draft(actor_id, project_id)
            assert target_object_key not in {
                item["id"] for item in after["content"]["relationships"]
            }
            undone = WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=3,
            )
            assert undone["draft_revision"] == 4
        with factory() as session:
            restored = CaseFileService(session).get_draft(actor_id, project_id)
            restored_target = next(
                item
                for item in restored["content"]["relationships"]
                if item["id"] == target_object_key
            )
            assert {
                key: value for key, value in restored_target.items() if key != "updated_at"
            } == {key: value for key, value in original_target.items() if key != "updated_at"}


class ClosureRepairChatProvider(ChatSuggestionProvider):
    def chat(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        candidate = CaseFileChatCandidate.model_validate(
            {
                "answer": "已生成需要闭包同步调整的建议。",
                "referenced_object_ids": [
                    "claim_repair_prerequisite",
                    "claim_repair_subject",
                ],
                "referenced_event_ids": [],
                "suggestions": [
                    {
                        "object_id": "claim_repair_prerequisite",
                        "path": "/status",
                        "value_json": '"unresolved"',
                        "reason": "调整前置主张状态。",
                    }
                ],
            }
        )
        return CaseFileChatResult(
            candidate=candidate,
            usage={
                "requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )


class GeneralMutationClosureRepairProvider(ClosureRepairChatProvider):
    def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
        return GeneralMutationPlannerResult(
            MutationPlanV1.model_validate(
                {
                    "operations": [
                        {
                            "operation_key": "update_prerequisite",
                            "operation_type": "update_field",
                            "target": {
                                "ref_kind": "existing",
                                "object_id": "claim_repair_prerequisite",
                            },
                            "field_path": "/status",
                            "new_value": "unresolved",
                            "reason": "调整前置主张状态。",
                        }
                    ]
                }
            ),
            {},
        )


def test_general_mutation_closure_repair_appends_proven_companions_atomically(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m34-repair-generation"),
            provider_factory=lambda _task: ClosureRepairFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="将前置主张调整为未决，并保持依赖闭包。",
            )
        assert Worker(
            factory,
            config=WorkerConfig(
                worker_id="m34-repair-chat",
                general_mutation_mode="suggest",
                closure_repair_mode="suggest",
            ),
            provider_factory=lambda _task: GeneralMutationClosureRepairProvider(),
        ).run_once()
        with factory() as session:
            task_id = int(queued["task"]["task_run_id"])
            task = session.get(TaskRun, task_id)
            patch_set = session.scalar(
                select(AgentPatchSet).where(AgentPatchSet.task_run_id == task_id)
            )
            assert patch_set is not None, (
                None if task is None else (task.status, task.error_details_jsonb)
            )
            assert patch_set.review_mode == "atomic"
            operations = list(
                session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id == patch_set.id)
                    .order_by(AgentPatchOperation.ordinal)
                )
            )
            assert len(operations) > 1
            assert operations[0].origin == "primary"
            assert all(item.origin == "closure_repair" for item in operations[1:])
            assert all(item.repair_obligation_keys for item in operations[1:])
            patch_set_id = patch_set.id
        with factory() as session:
            preview = WorkflowService(session).simulate_agent_patch_set(
                actor_id,
                project_id,
                patch_set_id,
                expected_draft_id=draft_id,
                base_revision=2,
                operation_ids=None,
            )
            assert preview["simulation"]["can_apply"] is True
            applied = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set_id,
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=None,
            )
            assert applied["draft_revision"] == 3


@pytest.mark.parametrize("mode", ("shadow", "suggest"))
def test_closure_repair_mode_persists_round_audit_and_reviewable_provenance(
    workflow_database: tuple[Engine, int, str],
    mode: str,
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id=f"repair-{mode}-generation"),
            provider_factory=lambda _task: ClosureRepairFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="将前置主张调整为未决，并保持依赖闭包。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])

        provider = ClosureRepairChatProvider()
        assert Worker(
            factory,
            config=WorkerConfig(
                worker_id=f"repair-{mode}-chat",
                closure_repair_mode=mode,  # type: ignore[arg-type]
            ),
            provider_factory=lambda _task: provider,
        ).run_once()

        with factory() as session:
            steps = list(
                session.scalars(
                    select(AgentStepRun)
                    .where(
                        AgentStepRun.task_run_id == chat_task_id,
                        AgentStepRun.component_id.like("closure_repair_round_%"),
                    )
                    .order_by(AgentStepRun.component_id)
                )
            )
            assert [step.component_id for step in steps] == ["closure_repair_round_1"]
            assert steps[0].status == "succeeded"
            assert steps[0].component_version == "closure-repair-v3"
            calls = list(
                session.scalars(
                    select(AgentModelCall).where(AgentModelCall.agent_step_run_id == steps[0].id)
                )
            )
            assert len(calls) == 1
            assert calls[0].status == "succeeded"
            assert calls[0].prompt_version == "closure-repair-v3"
            patch_set = session.scalar(
                select(AgentPatchSet).where(AgentPatchSet.task_run_id == chat_task_id)
            )
            assert patch_set is not None
            operations = list(
                session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id == patch_set.id)
                    .order_by(AgentPatchOperation.ordinal)
                )
            )
            assert operations[0].origin == "primary"
            if mode == "suggest":
                assert len(operations) > 1
                assert all(operation.origin == "closure_repair" for operation in operations[1:])
                assert {operation.repair_round for operation in operations[1:]} == {1}
                assert all(operation.repair_obligation_keys for operation in operations[1:])
                patch_set_id = patch_set.id
                operation_ids = [operation.id for operation in operations]
            else:
                assert len(operations) == 1

        if mode == "suggest":
            with factory() as session:
                workflow = WorkflowService(session)
                partial = workflow.simulate_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set_id,
                    expected_draft_id=draft_id,
                    base_revision=2,
                    operation_ids=[operation_ids[0]],
                )
                assert partial["simulation"]["can_apply"] is False
                full = workflow.simulate_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set_id,
                    expected_draft_id=draft_id,
                    base_revision=2,
                    operation_ids=operation_ids,
                )
                assert full["simulation"]["can_apply"] is True


def test_agent_chat_persists_reviewable_batch_and_atomic_apply_undo(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="chat-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请通读整个卷宗并给出可以审阅的修改建议。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_input = session.scalar(
                select(TaskRun.input_jsonb).where(TaskRun.id == chat_task_id)
            )
            assert set(frozen_input) == {
                "casefile",
                "history",
                "message",
                "focus",
                "validation",
                "context_policy_version",
                "routing_hint",
                "verification_trigger",
                "router_version",
                "context_state",
            }
            assert frozen_input["history"] == []
            assert frozen_input["casefile"]["events"]
            assert frozen_input["focus"]["object_ids"] == []
            assert frozen_input["context_policy_version"] == "casefile-chat-context-v6"
            assert frozen_input["routing_hint"] == {
                "entrypoint": "free_text",
                "preset_id": None,
            }
            assert frozen_input["verification_trigger"] == "chat"
            assert frozen_input["router_version"] == "casefile-chat-router-v2"

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="chat-suggestion-worker"),
            provider_factory=lambda _task: provider,
        )
        with patch(
            "casefile.application.workflow.agent.CLOSURE_POLICY_VERSION",
            CLOSURE_POLICY_V1,
        ):
            assert chat_worker.run_once() is True
        assert len(provider.requests) == 1
        assert provider.requests[0].message == "请通读整个卷宗并给出可以审阅的修改建议。"
        routed_request = provider.requests[0]
        assert routed_request.route is not None
        assert routed_request.route.route_source == "llm"
        assert routed_request.route.execution_profile["prompt_component"] == "edit"
        assert routed_request.prompt_version == "casefile-chat-v12"
        assert routed_request.toolset_version == "casefile-chat-tools-v4"
        assert routed_request.context_policy_version == "casefile-chat-context-v6"
        assert routed_request.task_understanding is not None
        assert routed_request.task_understanding.primary_intent == "edit_request"

        with factory() as session:
            intent_step = session.scalar(
                select(AgentStepRun).where(
                    AgentStepRun.task_run_id == chat_task_id,
                    AgentStepRun.component_id == "intent_router",
                )
            )
            assert intent_step is not None
            assert intent_step.status == "succeeded"
            assert intent_step.ir_schema_id == "chat-task-understanding-v1"
            intent_call = session.scalar(
                select(AgentModelCall).where(
                    AgentModelCall.agent_step_run_id == intent_step.id,
                    AgentModelCall.prompt_component_id == "intent_router",
                )
            )
            assert intent_call is not None
            assert intent_call.target_schema_id == "chat-task-understanding-v1"

        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assert [message["role"] for message in messages] == ["user", "assistant"]
            assistant = messages[-1]
            assert assistant["status"] == "completed"
            assert assistant["referenced_object_ids"]
            patch_set = assistant["patch_set"]
            assert patch_set["status"] == "pending"
            assert patch_set["closure_policy_version"] == CLOSURE_POLICY_V1
            assert len(patch_set["operations"]) == 2
            operation_ids = [operation["operation_id"] for operation in patch_set["operations"]]
            with patch(
                "casefile.application.workflow.agent.CLOSURE_POLICY_VERSION",
                CLOSURE_POLICY_V1,
            ):
                preview = workflow.simulate_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    base_revision=2,
                    operation_ids=operation_ids,
                )
            debt_keys = preview["simulation"]["authorization_required_finding_keys"]
            assert debt_keys
            with pytest.raises(ApplicationError) as stale_error:
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=operation_ids,
                    accepted_debt_finding_keys=debt_keys,
                    debt_acceptance_reason="旧策略待处理批次不能在新策略下直接应用。",
                )
            assert stale_error.value.code == "closure_policy_version_stale"
            with (
                patch(
                    "casefile.application.workflow.agent.CLOSURE_POLICY_VERSION",
                    CLOSURE_POLICY_V1,
                ),
                patch(
                    "casefile.application.v1_editing.CLOSURE_POLICY_VERSION",
                    CLOSURE_POLICY_V1,
                ),
            ):
                applied = workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=operation_ids,
                    accepted_debt_finding_keys=debt_keys,
                    debt_acceptance_reason="测试作者明确接受关键 Claim 暂时失去支撑的逻辑债务。",
                )
            assert applied["draft_revision"] == 3
            assert applied["status"] == "applied"
            assert {issue["rule_id"] for issue in applied["validator_issues"]} == {"CF-W-CLAIM-001"}

        with factory() as session:
            applied_draft = CaseFileService(session).get_draft(actor_id, project_id)
            assert applied_draft["revision"] == 3
            assert (
                applied_draft["content"]["entities"][0]["description"]
                == "负责追查午夜重启原因的研究员。"
            )
            assert applied_draft["content"]["claims"][0]["support_refs"] == []
            operation_types = list(
                session.scalars(
                    select(DraftOperation.operation_type)
                    .where(
                        DraftOperation.project_id == project_id,
                        DraftOperation.operation_type.in_(
                            ("logical_mutation_apply", "logical_mutation_undo")
                        ),
                    )
                    .order_by(DraftOperation.sequence_no)
                )
            )
            assert operation_types == ["logical_mutation_apply"]
            debt_audit = session.scalar(
                select(AuditEvent).where(
                    AuditEvent.project_id == project_id,
                    AuditEvent.action == "logical_mutation.debt_accepted",
                )
            )
            assert debt_audit is not None
            assert debt_audit.actor_user_id == actor_id
            assert debt_audit.details_jsonb["finding_keys"] == debt_keys
            assert debt_audit.details_jsonb["closure_policy_version"] == "logical-mutation-v1"
            assert debt_audit.details_jsonb["accepted_at"]

        with factory() as session:
            undone = WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=3,
            )
            assert undone["draft_revision"] == 4
            assert undone["status"] == "undone"
            assert undone["simulation"]["closure_policy_version"] == CLOSURE_POLICY_V2

        with factory() as session:
            restored = CaseFileService(session).get_draft(actor_id, project_id)
            assert restored["revision"] == 4
            assert "description" not in restored["content"]["entities"][0]
            assert len(restored["content"]["claims"][0]["support_refs"]) == 1
            operation_types = list(
                session.scalars(
                    select(DraftOperation.operation_type)
                    .where(
                        DraftOperation.project_id == project_id,
                        DraftOperation.operation_type.in_(
                            ("logical_mutation_apply", "logical_mutation_undo")
                        ),
                    )
                    .order_by(DraftOperation.sequence_no)
                )
            )
            assert operation_types == ["logical_mutation_apply", "logical_mutation_undo"]
            undo_operation = session.scalar(
                select(DraftOperation).where(
                    DraftOperation.project_id == project_id,
                    DraftOperation.operation_type == "logical_mutation_undo",
                )
            )
            assert undo_operation is not None
            assert undo_operation.new_value_jsonb["closure_policy_version"] == CLOSURE_POLICY_V2
            assert (
                undo_operation.new_value_jsonb["source_closure_policy_version"]
                == "logical-mutation-v1"
            )

        with factory() as session:
            with pytest.raises(ApplicationError) as redo_error:
                WorkflowService(session).redo_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=4,
                )
        assert redo_error.value.code == "agent_patch_redo_policy_stale"

        with factory() as session:
            with (
                patch(
                    "casefile.application.workflow.mutation_history.ACTIVE_APPLY_POLICY",
                    CLOSURE_POLICY_V1,
                ),
                patch(
                    "casefile.application.v1_editing.CLOSURE_POLICY_VERSION",
                    CLOSURE_POLICY_V1,
                ),
            ):
                redone = WorkflowService(session).redo_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=4,
                )
            assert redone["draft_revision"] == 5
            assert redone["status"] == "applied"

        with factory() as session:
            redone_draft = CaseFileService(session).get_draft(actor_id, project_id)
            assert redone_draft["revision"] == 5
            assert (
                redone_draft["content"]["entities"][0]["description"]
                == "负责追查午夜重启原因的研究员。"
            )
            assert redone_draft["content"]["claims"][0]["support_refs"] == []


def test_agent_chat_preset_hint_freezes_routes_and_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="routing-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="对整个卷宗做一次体检，并说明时间线与推理的收束情况。",
                routing_hint={"entrypoint": "preset", "preset_id": "inspect"},
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_input, input_hash = session.execute(
                select(TaskRun.input_jsonb, TaskRun.input_hash).where(TaskRun.id == chat_task_id)
            ).one()

        assert set(frozen_input) == {
            "casefile",
            "history",
            "message",
            "focus",
            "validation",
            "context_policy_version",
            "routing_hint",
            "verification_trigger",
            "router_version",
            "context_state",
        }
        assert frozen_input["routing_hint"] == {
            "entrypoint": "preset",
            "preset_id": "inspect",
        }
        assert frozen_input["verification_trigger"] == "chat"
        assert frozen_input["router_version"] == INTENT_ROUTER_VERSION
        assert input_hash == hashlib.sha256(rfc8785.dumps(frozen_input)).hexdigest()

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="routing-preset-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        assert len(provider.requests) == 1
        routed_request = provider.requests[0]
        assert routed_request.task_understanding is not None
        assert routed_request.task_understanding.primary_intent == "analysis"
        assert routed_request.route is not None
        assert routed_request.route.route_source == "rule_preset"
        assert routed_request.route.routes[0]["profile"] == "analysis.healthcheck"
        assert routed_request.rewrite is not None
        assert routed_request.rewrite.rewrite_decision == "CONTEXTUALIZE"

        with factory() as session:
            workflow = WorkflowService(session)
            events = workflow.list_task_events(actor_id, project_id, chat_task_id)
            routing_event_types = [
                event["event_type"]
                for event in events
                if event["event_type"]
                in {
                    "intent.understood",
                    "route.decided",
                    "query.rewritten",
                    "route.suggestions_suppressed",
                    "route.outcome",
                }
            ]
            assert routing_event_types == [
                "intent.understood",
                "route.decided",
                "query.rewritten",
                "route.suggestions_suppressed",
                "route.outcome",
            ]
            suppressed = next(
                event for event in events if event["event_type"] == "route.suggestions_suppressed"
            )
            assert suppressed["payload"]["route_source"] == "rule_preset"
            assert suppressed["payload"]["suggestion_policy"] == "deny"
            assert suppressed["payload"]["suppressed_count"] == 2
            outcome = next(event for event in events if event["event_type"] == "route.outcome")
            assert outcome["payload"]["succeeded"] is True
            assert outcome["payload"]["route_hash"] == routed_request.route.route_hash
            assert outcome["payload"]["tool_metrics"]["calls"] == 0

            task = workflow.get_task(actor_id, project_id, chat_task_id)
            assert task["result"]["routing"]["suppressed_count"] == 2
            assert task["result"]["routing"]["suggestion_policy"] == "deny"
            assert task["result"]["tool_metrics"]["calls"] == 0

            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = messages[-1]
            assert assistant["status"] == "completed"
            assert assistant["patch_set"] is None
            patch_sets = list(
                session.scalars(
                    select(AgentPatchSet).where(AgentPatchSet.task_run_id == chat_task_id)
                )
            )
            assert patch_sets == []

        with factory() as session:
            workflow = WorkflowService(session)
            feedback = workflow.submit_agent_routing_feedback(
                actor_id,
                project_id,
                thread["thread_id"],
                int(assistant["message_id"]),
                correct_intent="question",
            )
            assert feedback["acknowledged"] is True
            with pytest.raises(ApplicationError, match="已经提交过路由反馈"):
                workflow.submit_agent_routing_feedback(
                    actor_id,
                    project_id,
                    thread["thread_id"],
                    int(assistant["message_id"]),
                    note="重复反馈",
                )
            feedback_events = [
                event
                for event in workflow.list_task_events(
                    actor_id,
                    project_id,
                    chat_task_id,
                )
                if event["event_type"] == "router.feedback"
            ]
            assert len(feedback_events) == 1
            assert feedback_events[0]["payload"]["correct_intent"] == "question"
            assert feedback_events[0]["payload"]["original"]["query"] == (
                "对整个卷宗做一次体检，并说明时间线与推理的收束情况。"
            )
            assert feedback_events[0]["payload"]["original"]["route"]["intent"] == "analysis"

        with factory() as session:
            exported = export_feedback_fixtures(factory, project_id=project_id)
            assert exported["schema_version"] == "casefile-chat-feedback-export-v1"
            assert exported["fixture_count"] == 1
            exported_fixture = exported["fixtures"][0]
            exported_source = exported["sources"][0]
            assert exported_fixture["expected_primary_intent"] == "question"
            assert exported_fixture["expected_prompt_component"] == "chat"
            assert exported_fixture["message"] == (
                "对整个卷宗做一次体检，并说明时间线与推理的收束情况。"
            )
            assert exported_fixture["casefile"]["entities"]
            assert exported_source["observed_intent"] == "analysis"
            assert exported_source["project_id"] == project_id

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            assert draft["revision"] == 2


def test_agent_chat_issue_route_allows_suggestions_and_records_route_outcome(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="issue-route-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请处理当前焦点中的验证问题。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            draft = CaseFileService(session).get_draft(actor_id, project_id)
        entity_id = draft["content"]["entities"][0]["id"]
        event_id = draft["content"]["events"][0]["id"]
        claim_id = draft["content"]["claims"][0]["id"]

        claim = Worker(
            factory,
            config=WorkerConfig(worker_id="issue-route-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )._claim_next()
        assert claim is not None
        assert claim[0] == chat_task_id

        with factory() as session:
            route = routing_policy(
                ChatTaskUnderstanding(
                    primary_intent="explain_issue",
                    confidence=1.0,
                    reason_codes=("rule_ui:issue_action",),
                ),
                budget={},
                route_source="rule_ui",
            )
            completion = WorkflowService(session).complete_chat_task(
                chat_task_id,
                claim[1],
                answer="已解释失败原因，并给出可逐项审阅的修改建议。",
                referenced_object_ids=[entity_id, event_id],
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
                suggestions=[
                    {
                        "object_id": entity_id,
                        "path": "/description",
                        "value": "负责追查午夜重启原因的研究员。",
                        "reason": "补充人物在卷宗中的职责。",
                    },
                    {
                        "object_id": claim_id,
                        "path": "/support_refs",
                        "value": [],
                        "reason": "暴露关键主张缺少支撑的语义警告。",
                    },
                ],
                usage={"requests": 1},
                route=agent_state_to_jsonable(route),
            )

        assert completion["message"]["status"] == "completed"
        patch_set = completion["message"]["patch_set"]
        assert patch_set is not None
        assert patch_set["status"] == "pending"
        assert len(patch_set["operations"]) == 2

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, chat_task_id)
            events = workflow.list_task_events(actor_id, project_id, chat_task_id)
            event_types = [event["event_type"] for event in events]
            assert "route.suggestions_suppressed" not in event_types
            assert event_types[-2:] == ["route.outcome", "task.succeeded"]
            assert task["result"]["routing"]["intent"] == "explain_issue"
            assert task["result"]["routing"]["suggestion_policy"] == "allow"
            assert task["result"]["routing"]["suppressed_count"] == 0


def test_agent_chat_marks_result_stale_after_concurrent_manual_edit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="stale-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="并发编辑",
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请在后台分析，我会继续编辑。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_revision = queued["task"]["input_draft_revision"]
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            entity_id = draft["content"]["entities"][0]["id"]

        with factory() as session:
            _entity, edited_revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_draft_id=draft_id,
                expected_revision=frozen_revision,
                changes={"description": "用户在 Agent 运行期间补充的说明。"},
            )
            assert edited_revision == frozen_revision + 1

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="stale-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, chat_task_id)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = messages[-1]
            assert task["status"] == "succeeded"
            assert assistant["content"]
            assert assistant["patch_set"]["status"] == "stale"
            with pytest.raises(ApplicationError) as stale_apply:
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    assistant["patch_set"]["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=edited_revision,
                    operation_ids=None,
                )
            assert stale_apply.value.code == "agent_patch_not_pending"


def test_agent_patch_structural_failure_rolls_back_entire_batch(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider(invalid_time=True)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="invalid-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="提出一条会触发结构门禁的建议。",
            )
        Worker(
            factory,
            config=WorkerConfig(worker_id="invalid-chat-worker"),
            provider_factory=lambda _task: provider,
        ).run_once()

        with factory() as session:
            workflow = WorkflowService(session)
            patch_set = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )[-1]["patch_set"]
            operation_id = patch_set["operations"][0]["operation_id"]
            with pytest.raises(ContractValidationError):
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=[operation_id],
                )

        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
            assert unchanged["revision"] == 2
            assert unchanged["content"]["events"][0]["time"]["end"] == "2042-06-01T20:03"
            messages = WorkflowService(session).list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assert messages[-1]["patch_set"]["status"] == "pending"


def test_agent_collaboration_freezes_and_reviews_atomic_patch_batches(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="agent-collaboration-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            generated_task = WorkflowService(session).get_task(
                actor_id,
                project_id,
                generation_task_id,
            )
            initial_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert generated_task["status"] == "succeeded"
        assert initial_draft["revision"] == 2

        entity = initial_draft["content"]["entities"][0]
        location = initial_draft["content"]["locations"][0]
        event = initial_draft["content"]["events"][0]
        entity_id = entity["id"]
        location_id = location["id"]
        event_id = event["id"]

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="核对关键对象",
            )
            sent = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请逐项建议调整研究员、实验室和重启事件。",
            )
        first_chat_task_id = int(sent["task"]["task_run_id"])

        with factory() as session:
            frozen_input, input_hash, input_draft_revision = session.execute(
                select(
                    TaskRun.input_jsonb,
                    TaskRun.input_hash,
                    TaskRun.input_draft_revision,
                ).where(TaskRun.id == first_chat_task_id)
            ).one()
        assert set(frozen_input) == {
            "casefile",
            "history",
            "message",
            "focus",
            "validation",
            "context_policy_version",
            "routing_hint",
            "verification_trigger",
            "router_version",
            "context_state",
        }
        assert frozen_input["casefile"] == initial_draft["content"]
        assert frozen_input["history"] == []
        assert frozen_input["message"] == "请逐项建议调整研究员、实验室和重启事件。"
        assert frozen_input["context_policy_version"] == "casefile-chat-context-v6"
        assert frozen_input["routing_hint"] == {
            "entrypoint": "free_text",
            "preset_id": None,
        }
        assert frozen_input["verification_trigger"] == "chat"
        assert frozen_input["router_version"] == "casefile-chat-router-v2"
        assert input_draft_revision == 2
        assert input_hash == hashlib.sha256(rfc8785.dumps(frozen_input)).hexdigest()

        with factory() as session:
            prompt_version, toolset_version = session.execute(
                select(TaskRun.prompt_version, TaskRun.toolset_version).where(
                    TaskRun.id == first_chat_task_id
                )
            ).one()
        assert prompt_version == "casefile-chat-v12"
        assert toolset_version == "casefile-chat-tools-v4"

        chat_claimer = Worker(
            factory,
            config=WorkerConfig(worker_id="agent-collaboration-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        first_claim = chat_claimer._claim_next()
        assert first_claim is not None
        assert first_claim[0] == first_chat_task_id

        with factory() as session:
            first_completion = WorkflowService(session).complete_chat_task(
                first_chat_task_id,
                first_claim[1],
                answer="我整理了三个互相独立、可逐项审阅的修改建议。",
                referenced_object_ids=[entity_id, location_id, entity_id],
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
                suggestions=[
                    {
                        "object_id": entity_id,
                        "path": "/name",
                        "value": "林首席研究员",
                        "reason": "明确人物在调查中的职责。",
                    },
                    {
                        "object_id": location_id,
                        "path": "/name",
                        "value": "中央实验室",
                        "reason": "统一地点称谓。",
                    },
                    {
                        "object_id": event_id,
                        "path": "/title",
                        "value": "不应采纳的重启标题",
                        "reason": "演示逐项拒绝。",
                    },
                ],
                usage={
                    "requests": 1,
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "total_tokens": 160,
                },
            )

        first_message = first_completion["message"]
        first_patch = first_message["patch_set"]
        assert first_message["status"] == "completed"
        assert first_message["referenced_object_ids"] == [entity_id, location_id]
        assert first_message["referenced_event_ids"] == [event_id]
        assert first_message["suggested_view"] is None
        assert first_patch["status"] == "pending"
        assert first_patch["base_draft_revision"] == 2
        assert [
            (operation["object_id"], operation["field_path"], operation["decision"])
            for operation in first_patch["operations"]
        ] == [
            (entity_id, "/name", "pending"),
            (location_id, "/name", "pending"),
            (event_id, "/title", "pending"),
        ]

        with factory() as session:
            second_sent = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="再给重启事件补一个候选标题。",
            )
        second_chat_task_id = int(second_sent["task"]["task_run_id"])
        with factory() as session:
            second_frozen_input = session.scalar(
                select(TaskRun.input_jsonb).where(TaskRun.id == second_chat_task_id)
            )
        assert second_frozen_input is not None
        assert second_frozen_input["history"] == [
            {
                "role": "user",
                "content": "请逐项建议调整研究员、实验室和重启事件。",
            },
            {
                "role": "assistant",
                "content": "我整理了三个互相独立、可逐项审阅的修改建议。",
            },
        ]

        second_claim = chat_claimer._claim_next()
        assert second_claim is not None
        assert second_claim[0] == second_chat_task_id
        with factory() as session:
            second_completion = WorkflowService(session).complete_chat_task(
                second_chat_task_id,
                second_claim[1],
                answer="补充了一条事件标题候选。",
                referenced_object_ids=[event_id],
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
                suggested_view="timeline",
                suggestions=[
                    {
                        "object_id": event_id,
                        "path": "/title",
                        "value": "系统重启与回航保护触发",
                        "reason": "让时间线标题直接表达关键事实。",
                    }
                ],
                usage={
                    "requests": 1,
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "total_tokens": 100,
                },
            )
        second_patch = second_completion["message"]["patch_set"]
        assert second_patch["status"] == "pending"
        assert second_patch["base_draft_revision"] == 2
        assert second_completion["message"]["suggested_view"] == "timeline"

        first_patch_id = int(first_patch["patch_set_id"])
        selected_operation_ids = [
            int(operation["operation_id"]) for operation in first_patch["operations"][:2]
        ]
        with factory() as session:
            rejected = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                int(second_patch["patch_set_id"]),
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=[],
            )
        assert rejected["status"] == "rejected"
        assert rejected["draft_revision"] == 2
        assert [operation["decision"] for operation in rejected["operations"]] == ["rejected"]
        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
        assert unchanged["revision"] == 2

        with factory() as session:
            applied = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                first_patch_id,
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=selected_operation_ids,
            )
        assert applied["draft_revision"] == 3
        assert applied["status"] == "applied"
        assert [operation["decision"] for operation in applied["operations"]] == [
            "accepted",
            "accepted",
            "rejected",
        ]

        with factory() as session:
            applied_draft = CaseFileService(session).get_draft(actor_id, project_id)
            apply_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.operation_type == "logical_mutation_apply"
                    )
                )
            )
        assert applied_draft["revision"] == 3
        assert (
            next(item for item in applied_draft["content"]["entities"] if item["id"] == entity_id)[
                "name"
            ]
            == "林首席研究员"
        )
        assert (
            next(
                item for item in applied_draft["content"]["locations"] if item["id"] == location_id
            )["name"]
            == "中央实验室"
        )
        assert (
            next(item for item in applied_draft["content"]["events"] if item["id"] == event_id)[
                "title"
            ]
            == event["title"]
        )
        assert len(apply_operations) == 1
        assert (
            apply_operations[0].base_revision,
            apply_operations[0].result_revision,
        ) == (2, 3)

        with factory() as session:
            undone = WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                first_patch_id,
                expected_draft_id=draft_id,
                expected_revision=3,
            )
        assert undone["draft_revision"] == 4
        assert undone["status"] == "undone"

        with factory() as session:
            undone_draft = CaseFileService(session).get_draft(actor_id, project_id)
            undo_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.operation_type == "logical_mutation_undo"
                    )
                )
            )
        assert undone_draft["revision"] == 4
        assert (
            next(item for item in undone_draft["content"]["entities"] if item["id"] == entity_id)[
                "name"
            ]
            == entity["name"]
        )
        assert (
            next(
                item for item in undone_draft["content"]["locations"] if item["id"] == location_id
            )["name"]
            == location["name"]
        )
        assert len(undo_operations) == 1
        assert (
            undo_operations[0].base_revision,
            undo_operations[0].result_revision,
        ) == (3, 4)

        with factory() as session, pytest.raises(ApplicationError) as rejected_patch:
            WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                int(second_patch["patch_set_id"]),
                expected_draft_id=draft_id,
                expected_revision=4,
                operation_ids=None,
            )
        assert rejected_patch.value.code == "agent_patch_not_pending"

        with factory() as session:
            final_draft = CaseFileService(session).get_draft(actor_id, project_id)
            messages = WorkflowService(session).list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
        assert final_draft["revision"] == 4
        second_assistant = next(
            message
            for message in messages
            if message["task"] is not None and message["task"]["task_run_id"] == second_chat_task_id
        )
        assert second_assistant["patch_set"]["status"] == "rejected"
        assert second_assistant["patch_set"]["is_stale"] is False


def test_agent_chat_reference_autofill_only_fills_empty_unique_slots(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_REFERENCE_AUTOFILL": "1",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="reference-autofill-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            entity = draft["content"]["entities"][0]
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请核对关键人物。",
            )
        chat_task_id = int(queued["task"]["task_run_id"])
        claim = Worker(
            factory,
            config=WorkerConfig(worker_id="reference-autofill-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )._claim_next()
        assert claim is not None
        assert claim[0] == chat_task_id

        with factory() as session:
            completion = WorkflowService(session).complete_chat_task(
                chat_task_id,
                claim[1],
                answer=f"{entity['name']} 在本案中负责关键调查。",
                referenced_object_ids=[],
                referenced_event_ids=[],
                referenced_validation_issue_ids=[],
                suggestions=[],
                usage={"requests": 1},
            )

        assert completion["message"]["referenced_object_ids"] == [entity["id"]]

        with factory() as session:
            events = WorkflowService(session).list_task_events(
                actor_id,
                project_id,
                chat_task_id,
            )
        autofill_events = [
            event for event in events if event["event_type"] == "context.reference_autofilled"
        ]
        assert len(autofill_events) == 1
        assert autofill_events[0]["payload"] == {
            "object_ids": [entity["id"]],
            "event_ids": [],
        }


def test_agent_chat_unknown_reference_gets_one_controlled_repair_call(
    workflow_database: tuple[Engine, int, str],
) -> None:
    class RepairingChatProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.chat_calls = 0

        def chat(self, request):
            self.chat_calls += 1
            result = super().chat(request)
            if self.chat_calls == 1:
                return dataclasses_replace(
                    result,
                    candidate=result.candidate.model_copy(
                        update={"referenced_object_ids": ["src_fabricated"]}
                    ),
                )
            return result

    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    provider = RepairingChatProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="reference-repair-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请核对关键人物。",
            )
        chat_task_id = int(queued["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="reference-repair-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            task = session.get(TaskRun, chat_task_id)
            assert task is not None
            assert task.status == "succeeded", (
                task.status,
                task.error_code,
                task.error_details_jsonb,
            )

        with factory() as session:
            events = WorkflowService(session).list_task_events(
                actor_id,
                project_id,
                chat_task_id,
            )
        assert provider.chat_calls == 2
        assert [event["event_type"] for event in events].count(
            "model.reference_repair_started"
        ) == 1
        repair_event = next(
            event for event in events if event["event_type"] == "model.reference_repair_started"
        )
        assert repair_event["payload"]["unknown_object_ids"] == ["src_fabricated"]
        assert repair_event["payload"]["repair_no"] == 1


def test_project_archive_unarchive_roundtrip_and_timestamps(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        service = CaseFileService(session)
        created = service.create_project(
            actor_id,
            ProjectCreate(title="归档往返", description=None, profile=PROFILE),
        )
        project_id = int(created["id"])
        assert created["status"] == "active"
        assert created["archived_at"] is None
        assert created["created_at"] is not None
        assert created["updated_at"] is not None

        listed = service.list_projects(actor_id)
        assert [item["id"] for item in listed] == [project_id]
        assert listed[0]["created_at"] == created["created_at"]

        archived = service.archive_project(actor_id, project_id)
        assert archived["status"] == "archived"
        assert archived["archived_at"] is not None

        restored = service.unarchive_project(actor_id, project_id)
        assert restored["status"] == "active"
        assert restored["archived_at"] is None

        # 取消归档是幂等的：再次调用保持不变。
        restored_again = service.unarchive_project(actor_id, project_id)
        assert restored_again["status"] == "active"


def test_get_brief_exposes_current_version_no(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    project_id, _task_run_id = _prepare_task(engine, actor_id)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        brief = WorkflowService(session).get_brief(actor_id, project_id)
        assert brief["current_version_id"] is not None
        assert brief["current_version_no"] == 1
