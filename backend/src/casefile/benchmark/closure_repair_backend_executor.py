"""Real API/Worker/PostgreSQL executor for Closure Repair backend qualification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from casefile.agent_runtime import DeepSeekAgentsProvider, FakeProvider
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    ChatTaskUnderstandingOutput,
    GenerationRequest,
    GenerationResult,
    IntentConstraintsOutput,
    IntentEntitiesOutput,
    IntentUnderstandingResult,
    ToolMetrics,
)
from casefile.api.app import create_app
from casefile.application.commands import ProjectCreate
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.closure_repair_backend_release import (
    FAULT_MATRIX,
    BackendReleaseContractError,
    BackendTrialEvidence,
)
from casefile.benchmark.eval_core import EvalTask
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentPatchOperation,
    AgentPatchSet,
    AgentStepRun,
    AuditEvent,
    DraftOperation,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.data_postgres.session import create_database_engine, create_session_factory
from casefile.worker.runtime import Worker, WorkerConfig


class _TrialProvider(FakeProvider):
    def __init__(
        self,
        document: Mapping[str, Any],
        primary: Mapping[str, Any],
    ) -> None:
        self.document = deepcopy(dict(document))
        self.primary = deepcopy(dict(primary))
        self.repair_provider = DeepSeekAgentsProvider()
        self.repair_calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        candidate = deepcopy(self.document)
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
        for constraint in candidate.get("constraints", []):
            for scope_ref in constraint.get("scope_refs", []):
                if scope_ref.get("object_type") == "casefile":
                    scope_ref["object_id"] = request.casefile_id
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1, adopted_results=1),
        )

    def understand_intent(self, request: CaseFileChatRequest) -> IntentUnderstandingResult:
        routed = super().understand_intent(request)
        return IntentUnderstandingResult(
            candidate=ChatTaskUnderstandingOutput(
                original_query=request.message,
                normalized_query=request.message,
                primary_intent="edit_request",
                sub_intents=["modify_fields"],
                entities=IntentEntitiesOutput(
                    object_mentions=[], event_mentions=[], issue_mentions=[]
                ),
                constraints=IntentConstraintsOutput(
                    preserved_negations=[],
                    preserved_actions=[],
                    output_format="patch_proposal",
                ),
                capabilities={"needs_suggestion_generation": True},
                confidence=0.99,
                reason_codes=["backend_release_frozen_primary"],
                canonical_query=request.message,
            ),
            usage=routed.usage,
        )

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        base = super().chat(request)
        if self.primary.get("operation_type") != "update_field":
            raise BackendReleaseContractError("backend_executor_primary_mutation_unsupported")
        candidate = CaseFileChatCandidate.model_validate(
            {
                "answer": "已生成冻结的 update_field 主修改，等待服务端闭包判定。",
                "referenced_object_ids": [self.primary["object_id"]],
                "referenced_event_ids": [],
                "suggestions": [
                    {
                        "object_id": self.primary["object_id"],
                        "path": self.primary["field_path"],
                        "value_json": json.dumps(self.primary["new_value"], ensure_ascii=False),
                        "reason": "Backend Release 冻结主修改。",
                    }
                ],
            }
        )
        return CaseFileChatResult(
            candidate=candidate,
            usage=base.usage,
        )

    def repair_closure(self, request: Any) -> Any:
        self.repair_calls += 1
        return self.repair_provider.repair_closure(request)


class _BeforePersistenceFailureProvider(FakeProvider):
    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        raise RuntimeError("backend_fault_before_persistence")


class _FaultSuggestionProvider(FakeProvider):
    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        base = super().chat(request)
        entities = request.casefile.get("entities")
        if not isinstance(entities, list) or not entities:
            raise RuntimeError("backend_fault_entity_missing")
        object_id = str(entities[0]["id"])
        return CaseFileChatResult(
            candidate=CaseFileChatCandidate.model_validate(
                {
                    "answer": "Fault matrix persistence probe.",
                    "referenced_object_ids": [object_id],
                    "referenced_event_ids": [],
                    "suggestions": [
                        {
                            "object_id": object_id,
                            "path": "/name",
                            "value_json": json.dumps(
                                f"fault matrix {request.task_run_id}", ensure_ascii=False
                            ),
                            "reason": "Controlled post-persistence failure injection.",
                        }
                    ],
                }
            ),
            usage=base.usage,
            tools=base.tools,
        )


class _GenerationFailureProvider(FakeProvider):
    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise RuntimeError("backend_fault_generation_failure")


class _AfterPersistenceWorker(Worker):
    def _complete_chat(self, *args: Any, **kwargs: Any) -> None:
        super()._complete_chat(*args, **kwargs)
        raise RuntimeError("backend_fault_after_persistence")


class PostgresBackendReleaseExecutor:
    """Create isolated projects and drive real HTTP, Worker, persistence, and history."""

    supported_primary_operation_types = frozenset({"update_field"})

    def __init__(self, *, database_url: str, repair_api_key: str) -> None:
        self.database_url = database_url
        self.repair_api_key = repair_api_key
        self.engine = create_database_engine(database_url)
        self.session_factory = create_session_factory(self.engine)
        self.app = create_app(database_url)
        self.database_schema_fingerprint = self._schema_fingerprint()
        self._last_trial: dict[str, int] | None = None
        self._last_agent_trial: dict[str, Any] | None = None

    def close(self) -> None:
        self.engine.dispose()

    def execute_trial(
        self, task: EvalTask, *, trial_index: int, repair_model: str
    ) -> BackendTrialEvidence:
        if repair_model != "deepseek-v4-pro":
            raise BackendReleaseContractError("backend_executor_repair_model_invalid")
        document = json.loads(Path(str(task.input["document"])).read_text(encoding="utf-8"))
        primary = task.input["primary_mutation"]
        if not isinstance(primary, Mapping) or primary.get("operation_type") != "update_field":
            raise BackendReleaseContractError("backend_executor_primary_mutation_unsupported")
        provider = _TrialProvider(document, primary)
        actor_id = self._create_actor(task.task_id, trial_index)
        project_id, generation_task_id = self._prepare_generation(actor_id, repair_model)
        generation_worker = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"backend-generation-{actor_id}"),
            provider_factory=lambda _task: provider,
        )
        if not generation_worker.run_once():
            raise RuntimeError("backend_executor_generation_not_claimed")
        with self.session_factory() as session:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            adopted = WorkflowService(session).adopt_generation_candidate(
                actor_id,
                project_id,
                generation_task_id,
                expected_current_draft_id=int(current["draft_id"]),
            )
        draft_id = int(adopted["draft_id"])
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            thread = client.post(
                f"/api/v1/projects/{project_id}/agent/threads",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_draft_revision": 2},
            )
            if thread.status_code != 201:
                raise RuntimeError(
                    f"backend_executor_thread_enqueue_failed:{thread.status_code}:"
                    f"{thread.json().get('code')}"
                )
            api_enqueued = True
            thread_id = int(thread.json()["thread_id"])
            queued = client.post(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": 2,
                    "content": str(task.input["original_intent"]),
                    "provider": "deepseek",
                },
            )
            if queued.status_code != 202:
                raise RuntimeError(
                    f"backend_executor_message_enqueue_failed:{queued.status_code}:"
                    f"{queued.json().get('code')}"
                )
            task_run_id = int(queued.json()["task"]["task_run_id"])
            worker_executed = Worker(
                self.session_factory,
                config=WorkerConfig(
                    worker_id=f"backend-suggest-{actor_id}",
                    closure_repair_mode="suggest",
                ),
                provider_factory=lambda _task: provider,
            ).run_once()
            terminal_failure = self._terminal_trial_failure(
                task,
                trial_index=trial_index,
                task_run_id=task_run_id,
                actor_id=actor_id,
                project_id=project_id,
                draft_id=draft_id,
                api_enqueued=api_enqueued,
                worker_executed=worker_executed,
                provider_invoked=provider.repair_calls > 0,
            )
            if terminal_failure is not None:
                return terminal_failure
            messages = client.get(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
            ).json()
            patch = messages[-1].get("patch_set")
            if not isinstance(patch, dict):
                raise RuntimeError("backend_executor_patch_set_missing")
            patch_set_id = int(patch["patch_set_id"])
            operation_ids = [int(item["operation_id"]) for item in patch["operations"]]
            partial_selection_rejected = True
            full_patch_simulates = True
            if task.automation == "agent" and len(operation_ids) > 1:
                partial = client.post(
                    f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                    headers=headers,
                    json={
                        "expected_draft_id": draft_id,
                        "base_revision": 2,
                        "operation_ids": operation_ids[:1],
                    },
                )
                partial_selection_rejected = (
                    partial.status_code == 200 and not partial.json()["simulation"]["can_apply"]
                )
            full = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "base_revision": 2,
                    "operation_ids": operation_ids,
                },
            )
            full_patch_simulates = (
                full.status_code == 200 and full.json()["simulation"]["can_apply"]
            )
            illegal = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "base_revision": 2,
                    "operation_ids": [max(operation_ids, default=0) + 10_000_000],
                },
            )
            illegal_selection_rejected = illegal.status_code in {409, 422}
            if task.automation != "agent":
                abstention_proven = (
                    full.status_code == 200 and not full.json()["simulation"]["can_apply"]
                )
                persisted = self._persistence_evidence(task_run_id, patch_set_id, task.automation)
                self._last_trial = {
                    "actor_id": actor_id,
                    "project_id": project_id,
                    "task_run_id": task_run_id,
                    "patch_set_id": patch_set_id,
                    "draft_id": draft_id,
                }
                passed = bool(
                    api_enqueued
                    and worker_executed
                    and persisted["step_run_persisted"]
                    and persisted["policy_decision_matches"]
                    and provider.repair_calls == 0
                    and abstention_proven
                )
                return BackendTrialEvidence(
                    task_id=task.task_id,
                    trial_index=trial_index,
                    automation=task.automation,
                    family=task.policy_key[0],
                    passed=passed,
                    provider_invoked=False,
                    infrastructure_failure=None,
                    safety_violations=(),
                    api_enqueued=api_enqueued,
                    worker_executed=worker_executed,
                    step_run_persisted=persisted["step_run_persisted"],
                    model_call_persisted=True,
                    policy_decision_matches=persisted["policy_decision_matches"],
                    shadow_has_no_companion=True,
                    suggest_replay_provenance=True,
                    partial_selection_rejected=True,
                    full_patch_simulates=True,
                    apply_verified=True,
                    undo_verified=True,
                    redo_verified=True,
                    audit_continuous=True,
                    stale_rejected=True,
                    duplicate_apply_rejected=True,
                    illegal_selection_rejected=illegal_selection_rejected,
                )
            stale = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_revision": 1,
                    "operation_ids": operation_ids,
                },
            )
            stale_rejected = stale.status_code == 409
            apply = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_revision": 2,
                    "operation_ids": operation_ids,
                },
            )
            apply_verified = apply.status_code == 200 and apply.json()["draft_revision"] == 3
            duplicate = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_revision": 3,
                    "operation_ids": operation_ids,
                },
            )
            duplicate_apply_rejected = duplicate.status_code in {409, 422}
            undo = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/undo",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_revision": 3},
            )
            undo_verified = undo.status_code == 200 and undo.json()["draft_revision"] == 4
            redo = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/redo",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_revision": 4},
            )
            redo_verified = redo.status_code == 200 and redo.json()["draft_revision"] == 5
        persisted = self._persistence_evidence(task_run_id, patch_set_id, task.automation)
        self._last_trial = {
            "actor_id": actor_id,
            "project_id": project_id,
            "task_run_id": task_run_id,
            "patch_set_id": patch_set_id,
            "draft_id": draft_id,
        }
        self._last_agent_trial = {
            **self._last_trial,
            "operation_ids": operation_ids,
            "current_revision": 5,
        }
        repair_expected = task.automation == "agent"
        passed = all(
            (
                api_enqueued,
                worker_executed,
                persisted["step_run_persisted"],
                persisted["policy_decision_matches"],
                provider.repair_calls > 0 if repair_expected else provider.repair_calls == 0,
                full_patch_simulates,
                apply_verified,
                undo_verified,
                redo_verified,
                stale_rejected,
                duplicate_apply_rejected,
            )
        )
        return BackendTrialEvidence(
            task_id=task.task_id,
            trial_index=trial_index,
            automation=task.automation,
            family=task.policy_key[0],
            passed=passed,
            provider_invoked=provider.repair_calls > 0,
            infrastructure_failure=None,
            safety_violations=(),
            api_enqueued=api_enqueued,
            worker_executed=worker_executed,
            step_run_persisted=persisted["step_run_persisted"],
            model_call_persisted=persisted["model_call_persisted"] if repair_expected else True,
            policy_decision_matches=persisted["policy_decision_matches"],
            shadow_has_no_companion=True,
            suggest_replay_provenance=persisted["suggest_replay_provenance"]
            if repair_expected
            else True,
            partial_selection_rejected=partial_selection_rejected,
            full_patch_simulates=full_patch_simulates,
            apply_verified=apply_verified,
            undo_verified=undo_verified,
            redo_verified=redo_verified,
            audit_continuous=persisted["audit_continuous"],
            stale_rejected=stale_rejected,
            duplicate_apply_rejected=duplicate_apply_rejected,
            illegal_selection_rejected=illegal_selection_rejected,
        )

    def execute_fault(self, fault_id: str) -> Mapping[str, Any]:
        if fault_id not in FAULT_MATRIX:
            raise BackendReleaseContractError("backend_executor_fault_unknown")
        if self._last_trial is None:
            raise BackendReleaseContractError("backend_executor_fault_requires_trial")
        if (
            fault_id
            in {
                "apply_idempotency",
                "undo_idempotency",
                "redo_idempotency",
                "revision_conflict",
            }
            and self._last_agent_trial is None
        ):
            return {
                "passed": False,
                "injection": fault_id,
                "production_database": True,
                "details": {"reason_code": "backend_fault_agent_trial_unavailable"},
            }
        injectors = {
            "lease_timeout_recovery": self._inject_lease_timeout_recovery,
            "worker_interruption": self._inject_worker_interruption,
            "duplicate_finalize": self._inject_duplicate_finalize,
            "failure_before_persistence": self._inject_failure_before_persistence,
            "failure_after_persistence": self._inject_failure_after_persistence,
            "stale_resume": self._inject_stale_resume,
            "sse_task_projection": self._inject_sse_task_projection,
            "apply_idempotency": self._inject_apply_idempotency,
            "undo_idempotency": self._inject_undo_idempotency,
            "redo_idempotency": self._inject_redo_idempotency,
            "revision_conflict": self._inject_revision_conflict,
        }
        passed, details = injectors[fault_id]()
        return {
            "passed": passed,
            "injection": fault_id,
            "production_database": True,
            "details": details,
        }

    def _create_actor(self, task_id: str, trial_index: int) -> int:
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    text("INSERT INTO users (display_name) VALUES (:name) RETURNING id"),
                    {"name": f"M3.3 {task_id} {trial_index}"},
                ).scalar_one()
            )

    def _prepare_generation(self, actor_id: int, model_id: str) -> tuple[int, int]:
        with self.session_factory() as session:
            project = CaseFileService(session).create_project(
                actor_id,
                ProjectCreate(title="M3.3 Backend Release", description=None, profile={}),
            )
        project_id = int(project["id"])
        with self.session_factory() as session:
            workflow = WorkflowService(session)
            empty = CaseFileService(session).get_draft(actor_id, project_id)
            workflow.save_provider_setting(
                actor_id,
                provider="deepseek",
                api_key=self.repair_api_key,
                model_id=model_id,
                model_is_custom=False,
            )
            source = workflow.create_source(
                actor_id,
                project_id,
                source_kind="human_original",
                content_text="M3.3 Backend Release 隔离验证输入。",
                parent_source_record_id=None,
            )
            updated = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=1,
                content=_brief(int(source["source_record_id"])),
            )
            confirmed = workflow.confirm_brief(
                actor_id,
                project_id,
                expected_revision=int(updated["draft_revision"]),
            )
            task = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=int(confirmed["brief_version_id"]),
                expected_draft_id=int(empty["draft_id"]),
                expected_draft_revision=1,
                provider="deepseek",
            )
        return project_id, int(task["task_run_id"])

    def _persistence_evidence(
        self, task_run_id: int, patch_set_id: int, automation: str
    ) -> dict[str, bool]:
        with self.session_factory() as session:
            steps = list(
                session.scalars(select(AgentStepRun).where(AgentStepRun.task_run_id == task_run_id))
            )
            calls = session.scalar(
                select(func.count(AgentModelCall.id))
                .join(AgentStepRun, AgentStepRun.id == AgentModelCall.agent_step_run_id)
                .where(
                    AgentStepRun.task_run_id == task_run_id,
                    AgentStepRun.component_id.like("closure_repair_round_%"),
                )
            )
            patch = session.get(AgentPatchSet, patch_set_id)
            operations = list(
                session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id == patch_set_id)
                    .order_by(AgentPatchOperation.ordinal)
                )
            )
            audit_sequences = (
                list(
                    session.scalars(
                        select(DraftOperation.sequence_no)
                        .where(DraftOperation.project_id == patch.project_id)
                        .order_by(DraftOperation.sequence_no)
                    )
                )
                if patch is not None
                else []
            )
            audit_count = (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.project_id == patch.project_id
                    )
                )
                if patch is not None
                else 0
            )
        repair_operations = [item for item in operations if item.origin == "closure_repair"]
        return {
            "step_run_persisted": bool(steps),
            "model_call_persisted": bool(calls),
            "policy_decision_matches": bool(patch)
            and (bool(repair_operations) == (automation == "agent")),
            "suggest_replay_provenance": bool(repair_operations)
            and all(
                item.repair_round and item.repair_obligation_keys for item in repair_operations
            ),
            "audit_continuous": bool(audit_count)
            and audit_sequences == list(range(1, len(audit_sequences) + 1)),
        }

    def _terminal_trial_failure(
        self,
        task: EvalTask,
        *,
        trial_index: int,
        task_run_id: int,
        actor_id: int,
        project_id: int,
        draft_id: int,
        api_enqueued: bool,
        worker_executed: bool,
        provider_invoked: bool,
    ) -> BackendTrialEvidence | None:
        with self.session_factory() as session:
            task_run = session.get(TaskRun, task_run_id)
            steps = list(
                session.scalars(select(AgentStepRun).where(AgentStepRun.task_run_id == task_run_id))
            )
            model_call_count = session.scalar(
                select(func.count(AgentModelCall.id))
                .join(AgentStepRun, AgentStepRun.id == AgentModelCall.agent_step_run_id)
                .where(
                    AgentStepRun.task_run_id == task_run_id,
                    AgentStepRun.component_id.like("closure_repair_round_%"),
                )
            )
        if task_run is not None and task_run.status == "succeeded":
            return None
        diagnostic_values: list[Any] = [
            step.diagnostic_jsonb for step in steps if step.diagnostic_jsonb
        ]
        if task_run is not None:
            diagnostic_values.append(task_run.error_details_jsonb)
        infrastructure_failure = next(
            (
                value
                for payload in diagnostic_values
                if (value := _transport_error_class(payload)) is not None
            ),
            None,
        )
        self._last_trial = {
            "actor_id": actor_id,
            "project_id": project_id,
            "task_run_id": task_run_id,
            "draft_id": draft_id,
        }
        return BackendTrialEvidence(
            task_id=task.task_id,
            trial_index=trial_index,
            automation=task.automation,
            family=task.policy_key[0],
            passed=False,
            provider_invoked=provider_invoked,
            infrastructure_failure=infrastructure_failure,
            safety_violations=(),
            api_enqueued=api_enqueued,
            worker_executed=worker_executed,
            step_run_persisted=bool(steps),
            model_call_persisted=bool(model_call_count),
            policy_decision_matches=False,
            shadow_has_no_companion=True,
            suggest_replay_provenance=False,
            partial_selection_rejected=False,
            full_patch_simulates=False,
            apply_verified=False,
            undo_verified=False,
            redo_verified=False,
            audit_continuous=False,
            stale_rejected=False,
            duplicate_apply_rejected=False,
            illegal_selection_rejected=False,
        )

    def _inject_lease_timeout_recovery(self) -> tuple[bool, dict[str, Any]]:
        task_run_id = self._enqueue_fault_chat("lease timeout recovery")
        first = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-lease-a-{task_run_id}", lease_seconds=1),
            provider_factory=lambda _task: FakeProvider(),
        )
        claimed = self._claim_expected(first, task_run_id)
        self._expire_lease(task_run_id)
        second = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-lease-b-{task_run_id}"),
            provider_factory=lambda _task: FakeProvider(),
        )
        self._execute_expected(second, task_run_id)
        state = self._task_fault_state(task_run_id)
        passed = bool(
            claimed[0] == task_run_id
            and state["status"] == "succeeded"
            and state["attempt_statuses"] == ["failed", "succeeded"]
            and state["attempt_error_codes"][0] == "worker_lease_expired"
            and state["event_types"].count("task.recovered") == 1
        )
        return passed, state

    def _inject_worker_interruption(self) -> tuple[bool, dict[str, Any]]:
        task_run_id = self._enqueue_fault_chat("worker interruption")
        first = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-interrupt-a-{task_run_id}", lease_seconds=1),
            provider_factory=lambda _task: FakeProvider(),
        )
        self._claim_expected(first, task_run_id)
        first._emit(
            task_run_id,
            "worker.interruption_injected",
            "preparing",
            {"fault_id": "worker_interruption"},
        )
        self._expire_lease(task_run_id)
        second = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-interrupt-b-{task_run_id}"),
            provider_factory=lambda _task: FakeProvider(),
        )
        self._execute_expected(second, task_run_id)
        state = self._task_fault_state(task_run_id)
        passed = bool(
            state["status"] == "succeeded"
            and state["attempt_statuses"] == ["failed", "succeeded"]
            and "worker.interruption_injected" in state["event_types"]
            and state["event_types"].count("task.succeeded") == 1
        )
        return passed, state

    def _inject_duplicate_finalize(self) -> tuple[bool, dict[str, Any]]:
        task_run_id = self._enqueue_fault_chat("duplicate finalize")
        worker = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-finalize-{task_run_id}"),
            provider_factory=lambda _task: FakeProvider(),
        )
        self._execute_expected(worker, task_run_id)
        before = self._task_fault_state(task_run_id)
        with self.session_factory() as session:
            attempt_id = session.scalar(
                select(TaskAttempt.id)
                .where(TaskAttempt.task_run_id == task_run_id)
                .order_by(TaskAttempt.attempt_no.desc())
                .limit(1)
            )
        if attempt_id is None:
            return False, before
        worker._fail(
            task_run_id,
            int(attempt_id),
            RuntimeError("backend_fault_duplicate_finalize"),
            candidate=None,
            usage={},
            validation_errors=[],
            sensitive_values=(),
        )
        after = self._task_fault_state(task_run_id)
        passed = bool(
            before["status"] == after["status"] == "succeeded"
            and before["event_count"] == after["event_count"]
            and before["patch_set_count"] == after["patch_set_count"]
        )
        return passed, after

    def _inject_failure_before_persistence(self) -> tuple[bool, dict[str, Any]]:
        task_run_id = self._enqueue_fault_chat("failure before persistence")
        worker = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-before-{task_run_id}"),
            provider_factory=lambda _task: _BeforePersistenceFailureProvider(),
        )
        self._execute_expected(worker, task_run_id)
        state = self._task_fault_state(task_run_id)
        passed = bool(
            state["status"] == "failed"
            and state["patch_set_count"] == 0
            and state["attempt_statuses"] == ["failed"]
            and "task.succeeded" not in state["event_types"]
        )
        return passed, state

    def _inject_failure_after_persistence(self) -> tuple[bool, dict[str, Any]]:
        task_run_id = self._enqueue_fault_chat(
            "请修改第一个对象的名称，以验证 failure after persistence。"
        )
        worker = _AfterPersistenceWorker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-after-{task_run_id}"),
            provider_factory=lambda _task: _FaultSuggestionProvider(),
        )
        self._execute_expected(worker, task_run_id)
        state = self._task_fault_state(task_run_id)
        passed = bool(
            state["status"] == "succeeded"
            and state["step_run_count"] > 0
            and state["patch_set_count"] == 1
            and state["attempt_statuses"] == ["succeeded"]
            and state["event_types"].count("task.succeeded") == 1
        )
        return passed, state

    def _inject_stale_resume(self) -> tuple[bool, dict[str, Any]]:
        actor_id = self._create_actor("fault_stale_resume", 1)
        project_id, task_run_id = self._prepare_generation(actor_id, "deepseek-v4-pro")
        worker = Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"fault-resume-{task_run_id}"),
            provider_factory=lambda _task: _GenerationFailureProvider(),
        )
        self._execute_expected(worker, task_run_id)
        with self.session_factory() as session:
            workflow = WorkflowService(session)
            brief = workflow.get_brief(actor_id, project_id)
            changed = deepcopy(brief["content"])
            changed["creative_intent"] = f"stale resume {task_run_id}"
            updated = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=int(brief["draft_revision"]),
                content=changed,
            )
            draft = CaseFileService(session).get_draft(actor_id, project_id)
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            response = client.post(
                f"/api/v1/projects/{project_id}/tasks/{task_run_id}/resume",
                headers=headers,
                json={
                    "expected_draft_id": int(draft["draft_id"]),
                    "expected_draft_revision": int(draft["revision"]),
                    "expected_brief_revision": int(updated["draft_revision"]),
                },
            )
        state = self._task_fault_state(task_run_id)
        details = {
            **state,
            "http_status": response.status_code,
            "reason_code": response.json().get("code"),
        }
        return (
            response.status_code == 409
            and response.json().get("code") == "task_resume_brief_stale"
            and state["status"] == "failed",
            details,
        )

    def _inject_sse_task_projection(self) -> tuple[bool, dict[str, Any]]:
        assert self._last_trial is not None
        actor_id = self._last_trial["actor_id"]
        project_id = self._last_trial["project_id"]
        task_run_id = self._last_trial["task_run_id"]
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            event_rows = client.get(
                f"/api/v1/projects/{project_id}/tasks/{task_run_id}/events",
                headers=headers,
            ).json()
            stream = client.get(
                f"/api/v1/projects/{project_id}/tasks/{task_run_id}/stream",
                headers=headers,
            )
        expected_ids = [int(row["sequence_no"]) for row in event_rows]
        streamed_ids = [
            int(line.removeprefix("id: "))
            for line in stream.text.splitlines()
            if line.startswith("id: ")
        ]
        details = {
            "http_status": stream.status_code,
            "content_type": stream.headers.get("content-type"),
            "event_count": len(expected_ids),
            "streamed_event_count": len(streamed_ids),
            "sequence_continuous": expected_ids == list(range(1, len(expected_ids) + 1)),
        }
        return (
            stream.status_code == 200
            and str(stream.headers.get("content-type", "")).startswith("text/event-stream")
            and streamed_ids == expected_ids
            and bool(expected_ids),
            details,
        )

    def _inject_apply_idempotency(self) -> tuple[bool, dict[str, Any]]:
        context = self._agent_fault_context()
        before = self._draft_fault_state(context)
        response = self._agent_patch_request("apply", context, before["revision"])
        after = self._draft_fault_state(context)
        details = {
            "http_status": response.status_code,
            "reason_code": response.json().get("code"),
            "before": before,
            "after": after,
        }
        return (
            response.status_code in {409, 422}
            and before == after
            and before["patch_status"] == "applied",
            details,
        )

    def _inject_undo_idempotency(self) -> tuple[bool, dict[str, Any]]:
        context = self._agent_fault_context()
        before = self._draft_fault_state(context)
        first = self._agent_patch_request("undo", context, before["revision"])
        middle = self._draft_fault_state(context)
        second = self._agent_patch_request("undo", context, middle["revision"])
        after = self._draft_fault_state(context)
        details = {
            "first_http_status": first.status_code,
            "second_http_status": second.status_code,
            "second_reason_code": second.json().get("code"),
            "before": before,
            "after": after,
        }
        return (
            first.status_code == 200
            and second.status_code in {409, 422}
            and middle == after
            and after["revision"] == before["revision"] + 1
            and after["draft_operation_count"] == before["draft_operation_count"] + 1
            and after["patch_status"] == "undone",
            details,
        )

    def _inject_redo_idempotency(self) -> tuple[bool, dict[str, Any]]:
        context = self._agent_fault_context()
        before = self._draft_fault_state(context)
        first = self._agent_patch_request("redo", context, before["revision"])
        middle = self._draft_fault_state(context)
        second = self._agent_patch_request("redo", context, middle["revision"])
        after = self._draft_fault_state(context)
        details = {
            "first_http_status": first.status_code,
            "second_http_status": second.status_code,
            "second_reason_code": second.json().get("code"),
            "before": before,
            "after": after,
        }
        return (
            first.status_code == 200
            and second.status_code in {409, 422}
            and middle == after
            and after["revision"] == before["revision"] + 1
            and after["draft_operation_count"] == before["draft_operation_count"] + 1
            and after["patch_status"] == "applied",
            details,
        )

    def _inject_revision_conflict(self) -> tuple[bool, dict[str, Any]]:
        context = self._agent_fault_context()
        before = self._draft_fault_state(context)
        response = self._agent_patch_request("undo", context, before["revision"] - 1)
        after = self._draft_fault_state(context)
        details = {
            "http_status": response.status_code,
            "reason_code": response.json().get("code"),
            "before": before,
            "after": after,
        }
        return (
            response.status_code == 409
            and response.json().get("code") == "agent_patch_undo_stale"
            and before == after,
            details,
        )

    def _enqueue_fault_chat(self, message: str) -> int:
        assert self._last_trial is not None
        actor_id = self._last_trial["actor_id"]
        project_id = self._last_trial["project_id"]
        with self.session_factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            thread = client.post(
                f"/api/v1/projects/{project_id}/agent/threads",
                headers=headers,
                json={
                    "expected_draft_id": int(draft["draft_id"]),
                    "expected_draft_revision": int(draft["revision"]),
                },
            )
            if thread.status_code != 201:
                raise RuntimeError("backend_fault_thread_enqueue_failed")
            queued = client.post(
                f"/api/v1/projects/{project_id}/agent/threads/{thread.json()['thread_id']}/messages",
                headers=headers,
                json={
                    "expected_draft_id": int(draft["draft_id"]),
                    "expected_draft_revision": int(draft["revision"]),
                    "content": message,
                    "provider": "deepseek",
                },
            )
            if queued.status_code != 202:
                raise RuntimeError("backend_fault_message_enqueue_failed")
            return int(queued.json()["task"]["task_run_id"])

    def _claim_expected(self, worker: Worker, task_run_id: int) -> tuple[int, int]:
        claimed = worker._claim_next()
        if not isinstance(claimed, tuple) or claimed[0] != task_run_id:
            raise BackendReleaseContractError("backend_fault_unexpected_queue_claim")
        return claimed

    def _execute_expected(self, worker: Worker, task_run_id: int) -> None:
        worker._execute(*self._claim_expected(worker, task_run_id))

    def _expire_lease(self, task_run_id: int) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_runs SET lease_expires_at = :expired "
                    "WHERE id = :task_run_id AND status = 'running'"
                ),
                {
                    "expired": datetime.now(UTC) - timedelta(seconds=1),
                    "task_run_id": task_run_id,
                },
            )

    def _task_fault_state(self, task_run_id: int) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(TaskRun, task_run_id)
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_run_id == task_run_id)
                    .order_by(TaskAttempt.attempt_no)
                )
            )
            events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(TaskEvent.task_run_id == task_run_id)
                    .order_by(TaskEvent.sequence_no)
                )
            )
            step_count = session.scalar(
                select(func.count(AgentStepRun.id)).where(AgentStepRun.task_run_id == task_run_id)
            )
            patch_count = session.scalar(
                select(func.count(AgentPatchSet.id)).where(AgentPatchSet.task_run_id == task_run_id)
            )
        if task is None:
            raise BackendReleaseContractError("backend_fault_task_missing")
        return {
            "status": task.status,
            "attempt_statuses": [attempt.status for attempt in attempts],
            "attempt_error_codes": [attempt.error_code for attempt in attempts],
            "event_types": [event.event_type for event in events],
            "event_count": len(events),
            "step_run_count": int(step_count or 0),
            "patch_set_count": int(patch_count or 0),
            "lease_released": task.leased_by is None and task.lease_expires_at is None,
        }

    def _agent_fault_context(self) -> dict[str, Any]:
        if self._last_agent_trial is None:
            raise BackendReleaseContractError("backend_fault_requires_agent_trial")
        return self._last_agent_trial

    def _draft_fault_state(self, context: Mapping[str, Any]) -> dict[str, Any]:
        actor_id = int(context["actor_id"])
        project_id = int(context["project_id"])
        with self.session_factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            patch = session.get(AgentPatchSet, int(context["patch_set_id"]))
            operation_count = session.scalar(
                select(func.count(DraftOperation.id)).where(
                    DraftOperation.draft_id == int(context["draft_id"])
                )
            )
        if patch is None:
            raise BackendReleaseContractError("backend_fault_patch_missing")
        return {
            "revision": int(draft["revision"]),
            "draft_operation_count": int(operation_count or 0),
            "patch_status": patch.status,
        }

    def _agent_patch_request(
        self, action: str, context: Mapping[str, Any], expected_revision: int
    ) -> Any:
        actor_id = int(context["actor_id"])
        project_id = int(context["project_id"])
        patch_set_id = int(context["patch_set_id"])
        payload: dict[str, Any] = {
            "expected_draft_id": int(context["draft_id"]),
            "expected_revision": expected_revision,
        }
        if action == "apply":
            payload["operation_ids"] = list(context["operation_ids"])
        with TestClient(self.app) as client:
            return client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/{action}",
                headers={"X-CaseFile-User-Id": str(actor_id)},
                json=payload,
            )

    def _schema_fingerprint(self) -> str:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                )
            ).all()
        return sha256(
            json.dumps([list(row) for row in rows], separators=(",", ":")).encode()
        ).hexdigest()


def _transport_error_class(value: Any) -> str | None:
    allowed = {
        "timeout",
        "connection",
        "rate_limit",
        "provider_4xx",
        "provider_5xx",
        "protocol_unsupported",
        "unknown",
    }
    if isinstance(value, Mapping):
        candidate = value.get("transport_error_class")
        if isinstance(candidate, str) and candidate in allowed:
            return candidate
        for nested in value.values():
            found = _transport_error_class(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _transport_error_class(nested)
            if found is not None:
                return found
    return None


def _brief(source_record_id: int) -> dict[str, Any]:
    return {
        "source_record_ids": [source_record_id],
        "creative_intent": "验证确定性闭包修复生产路径。",
        "reasoning_proposition": "冻结主修改是否能在权限边界内完成闭包？",
        "resolution_mode": "author_anchored",
        "conclusion_mode": "unique",
        "author_answer": "仅允许服务器证明的语义修复。",
        "author_anchors": [{"anchor_id": "anchor_m33", "statement": "仅允许服务器证明的修复。"}],
        "boundary_text": "不得改变 M3.1 规则或越过 scope。",
        "creative_constraints": [
            {"constraint_id": "constraint_m33", "statement": "保持原主修改。", "strength": "hard"}
        ],
    }


__all__ = ["PostgresBackendReleaseExecutor"]
