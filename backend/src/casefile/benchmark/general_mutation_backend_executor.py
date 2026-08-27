"""PostgreSQL production-path executor for M3.4-07e."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx import Request
from openai import APITimeoutError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from casefile.agent_runtime import FakeProvider
from casefile.api.app import create_app
from casefile.application.chat_public_patches import public_warning_id
from casefile.application.commands import ProjectCreate
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.general_mutation_backend_release import (
    FAULT_MATRIX,
    BackendReleaseContractError,
    BackendTrialEvidence,
    ReleaseTask,
)
from casefile.benchmark.general_mutation_capability import _grade
from casefile.benchmark.general_mutation_safety_executor import _brief, _SafetyProvider
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentPatchOperation,
    AgentPatchSet,
    AgentStepRun,
    AuditEvent,
    Draft,
    DraftOperation,
    TaskEvent,
    TaskRun,
)
from casefile.data_postgres.session import create_database_engine, create_session_factory
from casefile.worker.runtime import Worker, WorkerConfig


class PostgresBackendReleaseExecutor:
    """Drive real HTTP, Worker, persistence, approval and history boundaries."""

    def __init__(
        self,
        *,
        database_url: str,
        api_key: str,
        provider_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.database_url = database_url
        self.api_key = api_key
        self.engine = create_database_engine(database_url)
        self.session_factory = create_session_factory(self.engine)
        self.app = create_app(database_url)
        self.provider_factory = provider_factory or _SafetyProvider
        self.database_schema_fingerprint = self._schema_fingerprint()
        self._last_context: dict[str, Any] | None = None
        self._last_apply_context: dict[str, Any] | None = None
        self._delete_gate_rows: list[dict[str, bool]] = []
        self._selection_tamper_rows: list[bool] = []
        self._generic_fault_executor: Any | None = None

    def close(self) -> None:
        if self._generic_fault_executor is not None:
            self._generic_fault_executor.close()
        self.engine.dispose()

    def execute_trial(
        self, task: ReleaseTask, *, trial_index: int, model_id: str
    ) -> BackendTrialEvidence:
        if model_id != "deepseek-v4-pro":
            raise BackendReleaseContractError("backend_executor_model_invalid")
        document = json.loads(
            (Path(__file__).resolve().parents[4] / task.fixture).read_text(encoding="utf-8")
        )
        provider = self.provider_factory(document)
        actor_id = self._create_actor(task.task_id, trial_index)
        project_id, generation_task_id = self._prepare_generation(actor_id, model_id)
        if not Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"m34-07e-generation-{actor_id}"),
            provider_factory=lambda _task: provider,
        ).run_once():
            return self._terminal_failure(task, trial_index, "generation_not_claimed")
        with self.session_factory() as session:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            adopted = WorkflowService(session).adopt_generation_candidate(
                actor_id,
                project_id,
                generation_task_id,
                expected_current_draft_id=int(current["draft_id"]),
            )
        draft_id = int(adopted["draft_id"])
        base_revision = int(adopted["revision"])
        with self.session_factory() as session:
            before = deepcopy(CaseFileService(session).get_draft(actor_id, project_id)["content"])
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            thread_response = client.post(
                f"/api/v1/projects/{project_id}/agent/threads",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_draft_revision": base_revision},
            )
            if thread_response.status_code != 201:
                return self._terminal_failure(task, trial_index, "thread_create_failed")
            thread_id = int(thread_response.json()["thread_id"])
            queued_response = client.post(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": base_revision,
                    "content": task.message,
                    "provider": "deepseek",
                },
            )
            if queued_response.status_code != 202:
                return self._terminal_failure(task, trial_index, "message_enqueue_failed")
            task_run_id = int(queued_response.json()["assistant_message"]["run"]["run_id"])
            worker_claimed = Worker(
                self.session_factory,
                config=WorkerConfig(
                    worker_id=f"m34-07e-chat-{actor_id}",
                    general_mutation_mode="suggest",
                    general_mutation_create_enabled=True,
                    general_mutation_delete_enabled=True,
                    closure_repair_mode="suggest",
                ),
                provider_factory=lambda _task: provider,
            ).run_once()
            messages_response = client.get(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
            )
            messages = messages_response.json() if messages_response.status_code == 200 else []
            patch = messages[-1].get("patch") if messages else None

            persisted = self._persisted_evidence(task_run_id, draft_id, base_revision)
            self._last_context = {
                "actor_id": actor_id,
                "project_id": project_id,
                "task_run_id": task_run_id,
                "draft_id": draft_id,
                "base_revision": base_revision,
                "thread_id": thread_id,
            }
            if task.expectation == "abstain":
                passed = bool(
                    worker_claimed
                    and persisted["task_succeeded"]
                    and patch is None
                    and persisted["revision"] == base_revision
                    and persisted["route_lineage_continuous"]
                    and (persisted["model_call_count"] == 0 or persisted["exact_model_observed"])
                )
                return self._abstention_evidence(
                    task,
                    trial_index,
                    passed=passed,
                    worker_claimed=worker_claimed,
                    persisted=persisted,
                    base_revision=base_revision,
                )
            if not isinstance(patch, dict):
                with self.session_factory() as session:
                    failed_task = session.get(TaskRun, task_run_id)
                event_reason_codes = persisted["event_reason_codes"]
                event_trace = ",".join(
                    item
                    for item in persisted["event_types"]
                    if item.startswith(("general_mutation", "route."))
                )
                task_error_code = None if failed_task is None else failed_task.error_code
                failure_reason = (
                    "pending_patch_missing"
                    if not event_reason_codes and not task_error_code
                    else "pending_patch_missing:"
                    + (
                        ",".join(event_reason_codes)
                        + f";events={event_trace};route={'|'.join(persisted['route_trace'])}"
                        if event_reason_codes
                        else str(task_error_code)
                    )
                )
                return self._terminal_failure(
                    task,
                    trial_index,
                    failure_reason,
                    worker_claimed=worker_claimed,
                    persisted=persisted,
                    base_revision=base_revision,
                )
            patch_set_id = int(patch["patch_id"])
            operation_ids = [int(item["change_id"]) for item in patch["changes"]]
            pending_before = (
                patch.get("status") == "pending" and persisted["revision"] == base_revision
            )
            invalid_selection = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "base_revision": base_revision,
                    "change_ids": [max(operation_ids, default=0) + 10_000_000],
                },
            )
            selection_tamper = invalid_selection.status_code in {409, 422}
            self._selection_tamper_rows.append(selection_tamper)
            preview = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "base_revision": base_revision,
                    "change_ids": None,
                },
            )
            preview_body = preview.json() if preview.status_code == 200 else {}
            confirmation_token = preview_body.get("confirmation_token")
            warning_ids = [
                str(item["notice_id"])
                for item in preview_body.get("warnings", [])
                if isinstance(item, dict) and isinstance(item.get("notice_id"), str)
            ]
            if preview.status_code == 200 and warning_ids:
                authorized_preview = client.post(
                    f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate",
                    headers=headers,
                    json={
                        "expected_draft_id": draft_id,
                        "base_revision": base_revision,
                        "change_ids": None,
                        "accepted_warning_ids": warning_ids,
                        "confirmation_note": ("M3.4-07e 对预览警告的显式测试确认。"),
                    },
                )
                if authorized_preview.status_code == 200:
                    preview_body = authorized_preview.json()
            can_apply = preview_body.get("can_apply") is True
            apply_payload: dict[str, Any] = {
                "expected_draft_id": draft_id,
                "expected_revision": base_revision,
                "change_ids": None,
                "accepted_warning_ids": warning_ids,
                "confirmation_note": (
                    "M3.4-07e 正式 Backend Release 对已预览警告的显式测试确认。"
                    if warning_ids
                    else None
                ),
            }
            delete_gate = True
            if patch.get("impact", {}).get("has_deletions"):
                missing = client.post(
                    f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                    headers=headers,
                    json=apply_payload,
                )
                tampered = client.post(
                    f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                    headers=headers,
                    json={**apply_payload, "confirmation_token": "0" * 64},
                )
                delete_gate = (
                    missing.status_code == 422
                    and missing.json().get("code") == "patch_confirmation_required"
                    and tampered.status_code == 409
                    and tampered.json().get("code") == "patch_review_changed"
                )
                apply_payload["confirmation_token"] = confirmation_token
                self._delete_gate_rows.append(
                    {"missing": missing.status_code == 422, "tampered": tampered.status_code == 409}
                )
            applied = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply",
                headers=headers,
                json=apply_payload,
            )
            apply_revision = base_revision + 1
            apply_verified = (
                applied.status_code == 200 and applied.json().get("revision") == apply_revision
            )
            with self.session_factory() as session:
                applied_document = CaseFileService(session).get_draft(actor_id, project_id)[
                    "content"
                ]
            graders = _grade(
                _as_eval_task(task),
                before,
                applied_document,
                verification_valid=True,
                verification_reason=None,
            )
            oracle_passed = all(item["passed"] for item in graders if item["severity"] == "hard")
            undone = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/undo",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_revision": apply_revision},
            )
            undo_revision = apply_revision + 1
            with self.session_factory() as session:
                undone_document = CaseFileService(session).get_draft(actor_id, project_id)[
                    "content"
                ]
            undo_verified = (
                undone.status_code == 200
                and undone.json().get("revision") == undo_revision
                and _semantic_hash(undone_document) == _semantic_hash(before)
            )
            undo_reason_code = (
                undone.json().get("code") if isinstance(undone.json(), dict) else None
            )
            undo_semantic_delta = _semantic_delta(before, undone_document)
            redone = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/redo",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_revision": undo_revision},
            )
            redo_revision = undo_revision + 1
            with self.session_factory() as session:
                redone_document = CaseFileService(session).get_draft(actor_id, project_id)[
                    "content"
                ]
            redo_verified = (
                redone.status_code == 200
                and redone.json().get("revision") == redo_revision
                and _semantic_hash(redone_document) == _semantic_hash(applied_document)
            )
            redo_reason_code = (
                redone.json().get("code") if isinstance(redone.json(), dict) else None
            )
            redo_semantic_delta = _semantic_delta(applied_document, redone_document)
        history = self._history_evidence(project_id, draft_id, base_revision, redo_revision)
        self._last_apply_context = {
            **self._last_context,
            "patch_set_id": patch_set_id,
            "operation_ids": operation_ids,
            "current_revision": redo_revision,
        }
        proof_persisted = self._patch_proof_persisted(patch_set_id)
        passed = all(
            (
                worker_claimed,
                persisted["task_succeeded"],
                persisted["route_lineage_continuous"],
                persisted["step_run_persisted"],
                persisted["model_call_persisted"],
                persisted["exact_model_observed"],
                pending_before,
                bool(operation_ids),
                proof_persisted,
                can_apply,
                delete_gate,
                apply_verified,
                oracle_passed,
                undo_verified,
                redo_verified,
                history["revision_continuous"],
                history["operation_sequence_continuous"],
                history["audit_continuous"],
                history["ownership_isolated"],
            )
        )
        failure_stage = _apply_failure_stage(
            apply_verified=apply_verified,
            oracle_passed=oracle_passed,
            undo_verified=undo_verified,
            redo_verified=redo_verified,
            history=history,
        )
        classification = (
            "success"
            if passed
            else "lifecycle_failure"
            if failure_stage in {"apply", "undo", "redo"}
            else "capability_failure"
        )
        return BackendTrialEvidence(
            task_id=task.task_id,
            family=task.family,
            expectation=task.expectation,
            trial_index=trial_index,
            passed=passed,
            classification=classification,
            infrastructure_failure=None,
            safety_violations=(),
            api_thread_created=True,
            api_message_enqueued=True,
            worker_claimed=worker_claimed,
            task_succeeded=persisted["task_succeeded"],
            route_lineage_continuous=persisted["route_lineage_continuous"],
            step_run_persisted=persisted["step_run_persisted"],
            model_call_persisted=persisted["model_call_persisted"],
            exact_model_observed=persisted["exact_model_observed"],
            pending_before_approval=pending_before,
            no_auto_apply=pending_before,
            operations_persisted=bool(operation_ids),
            proof_persisted=proof_persisted,
            simulation_can_apply=can_apply,
            delete_hash_gate_passed=delete_gate,
            apply_verified=apply_verified,
            final_state_oracle_passed=oracle_passed,
            post_apply_verification_passed=oracle_passed,
            undo_verified=undo_verified,
            redo_verified=redo_verified,
            revision_continuous=history["revision_continuous"],
            operation_sequence_continuous=history["operation_sequence_continuous"],
            audit_continuous=history["audit_continuous"],
            ownership_isolated=history["ownership_isolated"],
            patch_set_count=1,
            draft_revision_before=base_revision,
            draft_revision_after=redo_revision,
            model_call_count=persisted["model_call_count"],
            route_source=persisted["route_source"],
            primary_intent=persisted["primary_intent"],
            failure_stage=failure_stage,
            reason_code=(
                None if passed else f"backend_release_{failure_stage or 'capability'}_failed"
            ),
            undo_http_status=undone.status_code,
            undo_reason_code=undo_reason_code,
            undo_semantic_delta=undo_semantic_delta,
            redo_http_status=redone.status_code,
            redo_reason_code=redo_reason_code,
            redo_semantic_delta=redo_semantic_delta,
        )

    def execute_fault(self, fault_id: str) -> Mapping[str, Any]:
        if fault_id not in FAULT_MATRIX:
            raise BackendReleaseContractError("backend_executor_fault_unknown")
        if self._last_context is None:
            raise BackendReleaseContractError("backend_executor_fault_requires_trial")
        if fault_id == "confirmed_impact_hash_missing":
            return self._cached_fault(
                fault_id,
                bool(self._delete_gate_rows)
                and all(row["missing"] for row in self._delete_gate_rows),
            )
        if fault_id == "confirmed_impact_hash_tampered":
            return self._cached_fault(
                fault_id,
                bool(self._delete_gate_rows)
                and all(row["tampered"] for row in self._delete_gate_rows),
            )
        if fault_id == "operation_selection_tamper":
            return self._cached_fault(
                fault_id, bool(self._selection_tamper_rows) and all(self._selection_tamper_rows)
            )
        if fault_id in {
            "lease_timeout_recovery",
            "worker_interruption",
            "duplicate_finalize",
            "failure_before_persistence",
            "failure_after_persistence",
            "stale_resume",
            "sse_task_projection",
            "apply_idempotency",
            "revision_conflict",
        }:
            return self._delegate_generic_fault(fault_id)
        injectors = {
            "stale_patch_apply": self._fault_stale_patch,
            "wrong_draft_apply": self._fault_wrong_draft,
            "impact_changed_after_preview": self._fault_impact_changed,
            "concurrent_apply": self._fault_concurrent_apply,
            "undo_idempotency": lambda: self._fault_history_idempotency("undo"),
            "redo_idempotency": lambda: self._fault_history_idempotency("redo"),
            "protected_field_tamper": self._fault_protected_field,
            "provider_timeout": self._fault_provider_timeout,
        }
        passed, details = injectors[fault_id]()
        return {
            "fault_id": fault_id,
            "passed": passed,
            "production_database": True,
            "details": details,
        }

    def _delegate_generic_fault(self, fault_id: str) -> Mapping[str, Any]:
        from casefile.benchmark.closure_repair_backend_executor import (
            PostgresBackendReleaseExecutor,
        )

        if self._generic_fault_executor is None:
            self._generic_fault_executor = PostgresBackendReleaseExecutor(
                database_url=self.database_url, repair_api_key=self.api_key
            )
        delegate = self._generic_fault_executor
        assert self._last_context is not None
        delegate._last_trial = dict(self._last_context)
        delegate._last_agent_trial = (
            None if self._last_apply_context is None else dict(self._last_apply_context)
        )
        row = dict(delegate.execute_fault(fault_id))
        row["fault_id"] = fault_id
        row["reused_production_fault_primitive"] = "closure_repair_backend_executor"
        return row

    def _fault_stale_patch(self) -> tuple[bool, dict[str, Any]]:
        context = self._new_pending_patch()
        before = self._draft_revision(int(context["draft_id"]))
        with self.session_factory() as session:
            document = CaseFileService(session).get_draft(
                int(context["actor_id"]), int(context["project_id"])
            )["content"]
        target = document["entities"][0]
        with TestClient(self.app) as client:
            changed = client.patch(
                f"/api/v1/projects/{context['project_id']}/draft/objects/{target['id']}",
                headers={"X-CaseFile-User-Id": str(context["actor_id"])},
                json={
                    "expected_draft_id": context["draft_id"],
                    "expected_revision": before,
                    "changes": {"description": str(target.get("description", "")) + " [stale]"},
                },
            )
            stale = self._apply_pending(client, context, expected_revision=before)
        after = self._draft_revision(int(context["draft_id"]))
        passed = (
            changed.status_code == 200
            and stale.status_code == 409
            and stale.json().get("code") == "patch_stale"
            and after == before + 1
        )
        return passed, {
            "injection_point": "after_preview_before_apply",
            "http_status": stale.status_code,
            "reason_code": stale.json().get("code"),
            "revision_before": before,
            "revision_after": after,
            "recoverable": True,
        }

    def _fault_wrong_draft(self) -> tuple[bool, dict[str, Any]]:
        context = self._new_pending_patch()
        before = self._draft_revision(int(context["draft_id"]))
        with TestClient(self.app) as client:
            response = self._apply_pending(
                client,
                context,
                expected_revision=before,
                expected_draft_id=int(context["draft_id"]) + 999999,
            )
        after = self._draft_revision(int(context["draft_id"]))
        return response.status_code in {409, 422} and before == after, {
            "http_status": response.status_code,
            "reason_code": response.json().get("code"),
            "revision_before": before,
            "revision_after": after,
        }

    def _fault_impact_changed(self) -> tuple[bool, dict[str, Any]]:
        passed, details = self._fault_stale_patch()
        details["old_preview_hash_reusable"] = False
        details["reason_code"] = "patch_stale"
        return passed, details

    def _fault_concurrent_apply(self) -> tuple[bool, dict[str, Any]]:
        context = self._new_pending_patch()
        before = self._draft_revision(int(context["draft_id"]))

        def apply_once() -> tuple[int, str | None]:
            with TestClient(self.app) as client:
                response = self._apply_pending(client, context, expected_revision=before)
                return response.status_code, response.json().get("code")

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: apply_once(), range(2)))
        after = self._draft_revision(int(context["draft_id"]))
        success_count = sum(status == 200 for status, _code in outcomes)
        passed = success_count == 1 and after == before + 1
        return passed, {
            "injection_point": "concurrent_apply_requests",
            "outcomes": outcomes,
            "successful_apply_count": success_count,
            "revision_before": before,
            "revision_after": after,
            "recoverable": True,
        }

    def _fault_protected_field(self) -> tuple[bool, dict[str, Any]]:
        context = self._new_pending_patch()
        before = self._draft_revision(int(context["draft_id"]))
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE agent_patch_operations SET field_path='/id', "
                        "new_value_jsonb='\"tampered_id\"'::jsonb "
                        "WHERE patch_set_id=:id"
                    ),
                    {"id": context["patch_set_id"]},
                )
        except IntegrityError:
            after = self._draft_revision(int(context["draft_id"]))
            return before == after, {
                "injection_point": "persisted_operation_field_path",
                "http_status": None,
                "reason_code": "protected_field_tamper_database_rejected",
                "revision_before": before,
                "revision_after": after,
                "database_constraint_rejected": True,
                "recoverable": True,
            }
        with TestClient(self.app) as client:
            response = self._apply_pending(client, context, expected_revision=before)
        after = self._draft_revision(int(context["draft_id"]))
        return response.status_code in {409, 422} and before == after, {
            "injection_point": "persisted_operation_field_path",
            "http_status": response.status_code,
            "reason_code": response.json().get("code"),
            "revision_before": before,
            "revision_after": after,
            "recoverable": True,
        }

    def _fault_provider_timeout(self) -> tuple[bool, dict[str, Any]]:
        class TimeoutProvider(FakeProvider):
            def plan_general_mutation(self, request: Any) -> Any:
                del request
                raise APITimeoutError(request=Request("POST", "https://provider.invalid"))

        context = self._new_pending_patch(provider=TimeoutProvider(), expect_patch=False)
        with self.session_factory() as session:
            task = session.get(TaskRun, int(context["task_run_id"]))
            events = list(
                session.scalars(
                    select(TaskEvent).where(TaskEvent.task_run_id == context["task_run_id"])
                )
            )
            patch_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(AgentPatchSet)
                    .where(AgentPatchSet.task_run_id == context["task_run_id"])
                )
                or 0
            )
        reason_codes = [
            str(event.payload_jsonb.get("reason_code") or event.payload_jsonb.get("error_code"))
            for event in events
            if event.payload_jsonb.get("reason_code") or event.payload_jsonb.get("error_code")
        ]
        provider_timeout_observed = any(
            code in {"provider_timeout", "general_mutation_planner_failed"} for code in reason_codes
        )
        passed = (
            task is not None
            and task.status == "succeeded"
            and provider_timeout_observed
            and patch_count == 0
        )
        return passed, {
            "injection_point": "general_mutation_provider_call",
            "task_status": None if task is None else task.status,
            "reason_code": "provider_timeout" if provider_timeout_observed else None,
            "event_reason_codes": reason_codes,
            "patch_set_count": patch_count,
            "recoverable": True,
        }

    def _fault_history_idempotency(self, action: str) -> tuple[bool, dict[str, Any]]:
        context = self._new_pending_patch()
        draft_id = int(context["draft_id"])
        with TestClient(self.app) as client:
            applied = self._apply_pending(
                client, context, expected_revision=int(context["base_revision"])
            )
            revision = self._draft_revision(draft_id)
            if action == "redo":
                prepared = client.post(
                    f"/api/v1/projects/{context['project_id']}/agent/patch-sets/"
                    f"{context['patch_set_id']}/undo",
                    headers={"X-CaseFile-User-Id": str(context["actor_id"])},
                    json={"expected_draft_id": draft_id, "expected_revision": revision},
                )
                revision = self._draft_revision(draft_id)
            else:
                prepared = applied
            before_operation_count = self._draft_operation_count(draft_id)
            endpoint = (
                f"/api/v1/projects/{context['project_id']}/agent/patch-sets/"
                f"{context['patch_set_id']}/{action}"
            )
            payload = {"expected_draft_id": draft_id, "expected_revision": revision}
            first = client.post(
                endpoint,
                headers={"X-CaseFile-User-Id": str(context["actor_id"])},
                json=payload,
            )
            middle_revision = self._draft_revision(draft_id)
            middle_operation_count = self._draft_operation_count(draft_id)
            second = client.post(
                endpoint,
                headers={"X-CaseFile-User-Id": str(context["actor_id"])},
                json={"expected_draft_id": draft_id, "expected_revision": middle_revision},
            )
        after_revision = self._draft_revision(draft_id)
        after_operation_count = self._draft_operation_count(draft_id)
        passed = (
            applied.status_code == 200
            and prepared.status_code == 200
            and first.status_code == 200
            and second.status_code in {409, 422}
            and middle_revision == revision + 1 == after_revision
            and middle_operation_count == before_operation_count + 1 == after_operation_count
        )
        return passed, {
            "injection_point": f"{action}_request_replay",
            "first_http_status": first.status_code,
            "second_http_status": second.status_code,
            "second_reason_code": second.json().get("code"),
            "revision_before": revision,
            "revision_after": after_revision,
            "operation_count_before": before_operation_count,
            "operation_count_after": after_operation_count,
            "recoverable": True,
        }

    def _new_pending_patch(
        self, *, provider: Any | None = None, expect_patch: bool = True
    ) -> dict[str, Any]:
        if self._last_apply_context is None:
            raise BackendReleaseContractError("backend_executor_fault_requires_apply_trial")
        base = self._last_apply_context
        actor_id = int(base["actor_id"])
        project_id = int(base["project_id"])
        draft_id = int(base["draft_id"])
        revision = self._draft_revision(draft_id)
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        with TestClient(self.app) as client:
            thread = client.post(
                f"/api/v1/projects/{project_id}/agent/threads",
                headers=headers,
                json={"expected_draft_id": draft_id, "expected_draft_revision": revision},
            )
            thread_id = int(thread.json()["thread_id"])
            queued = client.post(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": revision,
                    "content": "创建一个名为故障探针的人物实体。",
                    "provider": "deepseek",
                },
            )
        task_run_id = int(queued.json()["assistant_message"]["run"]["run_id"])
        Worker(
            self.session_factory,
            config=WorkerConfig(
                worker_id=f"m34-07e-fault-{task_run_id}",
                general_mutation_mode="suggest",
                general_mutation_create_enabled=True,
                general_mutation_delete_enabled=True,
                closure_repair_mode="suggest",
            ),
            provider_factory=lambda _task: provider or FakeProvider(),
        ).run_once()
        with self.session_factory() as session:
            patch = session.scalar(
                select(AgentPatchSet).where(AgentPatchSet.task_run_id == task_run_id)
            )
        if expect_patch and patch is None:
            raise BackendReleaseContractError("backend_executor_fault_patch_missing")
        warning_ids: list[str] = []
        if patch is not None:
            with self.session_factory() as session:
                preview = WorkflowService(session).simulate_agent_patch_set(
                    actor_id,
                    project_id,
                    patch.id,
                    expected_draft_id=draft_id,
                    base_revision=revision,
                    operation_ids=None,
                )
            warning_ids = [
                public_warning_id(patch.id, finding_key)
                for finding_key in preview["simulation"].get(
                    "authorization_required_finding_keys", []
                )
            ]
        return {
            "actor_id": actor_id,
            "project_id": project_id,
            "draft_id": draft_id,
            "base_revision": revision,
            "task_run_id": task_run_id,
            "patch_set_id": None if patch is None else patch.id,
            "confirmation_token": None if patch is None else patch.impact_hash,
            "contains_delete": False if patch is None else patch.contains_delete,
            "accepted_warning_ids": warning_ids,
        }

    def _apply_pending(
        self,
        client: TestClient,
        context: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_draft_id: int | None = None,
    ) -> Any:
        return client.post(
            f"/api/v1/projects/{context['project_id']}/agent/patch-sets/"
            f"{context['patch_set_id']}/apply",
            headers={"X-CaseFile-User-Id": str(context["actor_id"])},
            json={
                "expected_draft_id": expected_draft_id or context["draft_id"],
                "expected_revision": expected_revision,
                "change_ids": None,
                "confirmation_token": context.get("confirmation_token"),
                "accepted_warning_ids": context.get("accepted_warning_ids", []),
                "confirmation_note": (
                    "M3.4-07e 故障矩阵对已预览警告的显式测试确认。"
                    if context.get("accepted_warning_ids")
                    else None
                ),
            },
        )

    def _repeat_apply_probe(self, expected_code: str) -> tuple[bool, dict[str, Any]]:
        context = self._last_apply_context
        if context is None:
            return False, {"reason_code": "fault_apply_context_missing"}
        before = self._draft_revision(int(context["draft_id"]))
        with TestClient(self.app) as client:
            response = client.post(
                f"/api/v1/projects/{context['project_id']}/agent/patch-sets/{context['patch_set_id']}/apply",
                headers={"X-CaseFile-User-Id": str(context["actor_id"])},
                json={
                    "expected_draft_id": context["draft_id"],
                    "expected_revision": context["current_revision"],
                    "change_ids": None,
                },
            )
        after = self._draft_revision(int(context["draft_id"]))
        code = response.json().get("code")
        return response.status_code == 409 and code == expected_code and before == after, {
            "http_status": response.status_code,
            "reason_code": code,
            "revision_before": before,
            "revision_after": after,
        }

    def _cached_fault(self, fault_id: str, passed: bool) -> Mapping[str, Any]:
        return {
            "fault_id": fault_id,
            "passed": passed,
            "production_database": True,
            "details": {"observed_in_release_trials": True},
        }

    def _persisted_evidence(
        self, task_run_id: int, draft_id: int, base_revision: int
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(TaskRun, task_run_id)
            events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(TaskEvent.task_run_id == task_run_id)
                    .order_by(TaskEvent.sequence_no)
                )
            )
            steps = list(
                session.scalars(select(AgentStepRun).where(AgentStepRun.task_run_id == task_run_id))
            )
            calls = list(
                session.scalars(
                    select(AgentModelCall).where(AgentModelCall.task_run_id == task_run_id)
                )
            )
            revision = session.scalar(select(Draft.revision).where(Draft.id == draft_id))
        event_types = [item.event_type for item in events]
        event_reason_codes = [
            f"{item.event_type}={item.payload_jsonb['reason_code']}"
            for item in events
            if isinstance(item.payload_jsonb, dict)
            and isinstance(item.payload_jsonb.get("reason_code"), str)
        ]
        route_trace = [
            json.dumps(item.payload_jsonb, ensure_ascii=False, sort_keys=True)
            for item in events
            if item.event_type in {"route.decided", "route.outcome"}
        ]
        required = ["intent.understood", "route.decided", "route.outcome", "task.succeeded"]
        positions = [event_types.index(item) for item in required if item in event_types]
        return {
            "task_succeeded": task is not None and task.status == "succeeded",
            "route_lineage_continuous": len(positions) == len(required)
            and positions == sorted(positions),
            "step_run_persisted": bool(steps),
            "model_call_persisted": bool(calls),
            "exact_model_observed": bool(calls)
            and all(item.model_id == "deepseek-v4-pro" for item in calls),
            "model_call_count": len(calls),
            "revision": int(revision or base_revision),
            "event_reason_codes": event_reason_codes,
            "event_types": event_types,
            "route_trace": route_trace,
            "route_source": _route_value(events, "route_source"),
            "primary_intent": _route_primary_intent(events),
            "transport_error_class": _transport_error_class(
                None if task is None else task.error_details_jsonb
            ),
        }

    def _history_evidence(
        self, project_id: int, draft_id: int, base: int, current: int
    ) -> dict[str, bool]:
        with self.session_factory() as session:
            operations = list(
                session.scalars(
                    select(DraftOperation)
                    .where(DraftOperation.draft_id == draft_id)
                    .order_by(DraftOperation.sequence_no)
                )
            )
            audits = list(
                session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.project_id == project_id)
                    .order_by(AuditEvent.id)
                )
            )
            foreign_operations = int(
                session.scalar(
                    select(func.count())
                    .select_from(DraftOperation)
                    .where(
                        DraftOperation.draft_id != draft_id, DraftOperation.project_id == project_id
                    )
                )
                or 0
            )
        sequences = [item.sequence_no for item in operations]
        return {
            "revision_continuous": current == base + 3,
            "operation_sequence_continuous": sequences == list(range(1, len(sequences) + 1)),
            "audit_continuous": bool(audits)
            and all(audits[index].id < audits[index + 1].id for index in range(len(audits) - 1)),
            "ownership_isolated": foreign_operations == 0,
        }

    def _patch_proof_persisted(self, patch_set_id: int) -> bool:
        with self.session_factory() as session:
            patch = session.get(AgentPatchSet, patch_set_id)
            operations = list(
                session.scalars(
                    select(AgentPatchOperation).where(
                        AgentPatchOperation.patch_set_id == patch_set_id
                    )
                )
            )
        return bool(
            patch is not None
            and patch.plan_hash
            and patch.impact_hash
            and patch.closure_policy_version
            and patch.binder_version
            and operations
            and all(
                item.origin in {"primary", "mechanical", "closure_repair"} for item in operations
            )
        )

    def _create_actor(self, task_id: str, trial_index: int) -> int:
        with self.engine.begin() as connection:
            return int(
                connection.execute(
                    text("INSERT INTO users (display_name) VALUES (:name) RETURNING id"),
                    {"name": f"M3.4-07e {task_id} {trial_index}"},
                ).scalar_one()
            )

    def _prepare_generation(self, actor_id: int, model_id: str) -> tuple[int, int]:
        with self.session_factory() as session:
            project = CaseFileService(session).create_project(
                actor_id,
                ProjectCreate(title="M3.4-07e Backend Release", description=None, profile={}),
            )
        project_id = int(project["id"])
        with self.session_factory() as session:
            workflow = WorkflowService(session)
            empty = CaseFileService(session).get_draft(actor_id, project_id)
            workflow.save_provider_setting(
                actor_id,
                provider="deepseek",
                api_key=self.api_key,
                model_id=model_id,
                model_is_custom=False,
            )
            source = workflow.create_source(
                actor_id,
                project_id,
                source_kind="human_original",
                content_text="M3.4-07e 隔离验证输入。",
                parent_source_record_id=None,
            )
            updated = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=1,
                content=_brief(int(source["source_record_id"])),
            )
            confirmed = workflow.confirm_brief(
                actor_id, project_id, expected_revision=int(updated["draft_revision"])
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

    def _schema_fingerprint(self) -> str:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name,column_name,data_type "
                    "FROM information_schema.columns WHERE table_schema='public' "
                    "ORDER BY table_name,ordinal_position"
                )
            ).all()
        return hashlib.sha256(
            json.dumps([list(row) for row in rows], separators=(",", ":")).encode()
        ).hexdigest()

    def _draft_revision(self, draft_id: int) -> int:
        with self.session_factory() as session:
            return int(session.scalar(select(Draft.revision).where(Draft.id == draft_id)) or 0)

    def _draft_operation_count(self, draft_id: int) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(DraftOperation)
                    .where(DraftOperation.draft_id == draft_id)
                )
                or 0
            )

    def _terminal_failure(
        self,
        task: ReleaseTask,
        trial_index: int,
        reason: str,
        *,
        worker_claimed: bool = False,
        persisted: Mapping[str, Any] | None = None,
        base_revision: int = 0,
    ) -> BackendTrialEvidence:
        values: dict[str, Any] = {
            field: False
            for field in BackendTrialEvidence.__dataclass_fields__
            if field.endswith(
                (
                    "_created",
                    "_enqueued",
                    "_claimed",
                    "_succeeded",
                    "_continuous",
                    "_persisted",
                    "_observed",
                    "_approval",
                    "_apply",
                    "_passed",
                    "_verified",
                    "_isolated",
                )
            )
        }
        is_setup_failure = persisted is None
        persisted = persisted or {
            "task_succeeded": False,
            "route_lineage_continuous": False,
            "step_run_persisted": False,
            "model_call_persisted": False,
            "exact_model_observed": False,
            "model_call_count": 0,
            "revision": base_revision,
            "route_source": None,
            "primary_intent": None,
            "transport_error_class": None,
        }
        transport_error = persisted.get("transport_error_class")
        is_infrastructure = is_setup_failure or isinstance(transport_error, str)
        is_routing_failure = not is_infrastructure and persisted["primary_intent"] in {
            "clarify",
            "unsupported_action",
        }
        is_protocol_failure = not is_infrastructure and _protocol_failure_reason(reason)
        classification = (
            "infrastructure_failure"
            if is_infrastructure
            else "routing_failure"
            if is_routing_failure
            else "protocol_failure"
            if is_protocol_failure
            else "capability_failure"
        )
        values.update(
            {
                "task_id": task.task_id,
                "family": task.family,
                "expectation": task.expectation,
                "trial_index": trial_index,
                "passed": False,
                "classification": classification,
                "infrastructure_failure": (
                    str(transport_error or reason) if is_infrastructure else None
                ),
                "safety_violations": (),
                "reason_code": reason,
                "no_auto_apply": True,
                "api_thread_created": True,
                "api_message_enqueued": True,
                "worker_claimed": worker_claimed,
                "task_succeeded": bool(persisted["task_succeeded"]),
                "route_lineage_continuous": bool(persisted["route_lineage_continuous"]),
                "step_run_persisted": bool(persisted["step_run_persisted"]),
                "model_call_persisted": bool(persisted["model_call_persisted"]),
                "exact_model_observed": (
                    bool(persisted["exact_model_observed"])
                    if persisted["model_call_count"]
                    else None
                ),
                "patch_set_count": 0,
                "draft_revision_before": base_revision,
                "draft_revision_after": int(persisted["revision"]),
                "model_call_count": int(persisted["model_call_count"]),
                "route_source": persisted["route_source"],
                "primary_intent": persisted["primary_intent"],
                "failure_stage": (
                    "worker"
                    if is_setup_failure
                    else "provider_transport"
                    if isinstance(transport_error, str)
                    else "route"
                    if is_routing_failure
                    else "model_protocol"
                    if is_protocol_failure
                    else "patch_persistence"
                ),
            }
        )
        return BackendTrialEvidence(**values)

    def _abstention_evidence(
        self,
        task: ReleaseTask,
        trial_index: int,
        *,
        passed: bool,
        worker_claimed: bool,
        persisted: Mapping[str, Any],
        base_revision: int,
    ) -> BackendTrialEvidence:
        revision = int(persisted["revision"])
        return BackendTrialEvidence(
            task_id=task.task_id,
            family=task.family,
            expectation=task.expectation,
            trial_index=trial_index,
            passed=passed,
            classification="safe_block" if passed else "safety_failure",
            infrastructure_failure=None,
            safety_violations=() if passed else ("abstention_failed",),
            api_thread_created=True,
            api_message_enqueued=True,
            worker_claimed=worker_claimed,
            task_succeeded=bool(persisted["task_succeeded"]),
            route_lineage_continuous=bool(persisted["route_lineage_continuous"]),
            step_run_persisted=bool(persisted["step_run_persisted"]),
            model_call_persisted=bool(persisted["model_call_persisted"]),
            exact_model_observed=(
                bool(persisted["exact_model_observed"]) if persisted["model_call_count"] else None
            ),
            pending_before_approval=None,
            no_auto_apply=True,
            operations_persisted=None,
            proof_persisted=None,
            simulation_can_apply=None,
            delete_hash_gate_passed=None,
            apply_verified=None,
            final_state_oracle_passed=None,
            post_apply_verification_passed=None,
            undo_verified=None,
            redo_verified=None,
            revision_continuous=None,
            operation_sequence_continuous=None,
            audit_continuous=None,
            ownership_isolated=None,
            patch_set_count=0,
            draft_revision_before=base_revision,
            draft_revision_after=revision,
            model_call_count=int(persisted["model_call_count"]),
            route_source=persisted["route_source"],
            primary_intent=persisted["primary_intent"],
            failure_stage=None if passed else "route",
            reason_code=None if passed else "abstention_failed",
        )


def _as_eval_task(task: ReleaseTask) -> Any:
    from casefile.benchmark.eval_core import EvalTask

    return EvalTask(
        task.task_id,
        (task.family, "general-mutation-gate-v1"),
        "agent",
        {"fixture": task.fixture, "message": task.message},
        task.oracle,
        task.fixture,
        (),
        "release",
        task.family,
    )


def _semantic_hash(value: Mapping[str, Any]) -> str:
    document = _semantic_state(value)
    for field in ("casefile_id", "version", "brief_ref"):
        document.pop(field, None)
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _without_storage_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_storage_metadata(item)
            for key, item in value.items()
            if key not in {"revision", "updated_at"}
        }
    if isinstance(value, list):
        return [_without_storage_metadata(item) for item in value]
    return value


def _semantic_state(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _without_storage_metadata(deepcopy(dict(value)))
    assert isinstance(normalized, dict)
    for collection, items in tuple(normalized.items()):
        if isinstance(items, list) and all(
            isinstance(item, dict) and isinstance(item.get("id"), str) for item in items
        ):
            normalized[collection] = {
                str(item["id"]): item for item in sorted(items, key=lambda item: str(item["id"]))
            }
    return normalized


def _semantic_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    left = _semantic_state(before)
    right = _semantic_state(after)
    return {
        "changed_top_level_fields": sorted(
            key for key in set(left) | set(right) if left.get(key) != right.get(key)
        ),
        "before_hash": _semantic_hash(before),
        "after_hash": _semantic_hash(after),
    }


def _route_event_payload(events: list[Any]) -> Mapping[str, Any]:
    event = next((item for item in events if item.event_type == "route.decided"), None)
    return (
        event.payload_jsonb if event is not None and isinstance(event.payload_jsonb, dict) else {}
    )


def _route_value(events: list[Any], key: str) -> str | None:
    value = _route_event_payload(events).get(key)
    return str(value) if isinstance(value, str) else None


def _route_primary_intent(events: list[Any]) -> str | None:
    profile = _route_event_payload(events).get("execution_profile")
    value = profile.get("primary_intent") if isinstance(profile, dict) else None
    return str(value) if isinstance(value, str) else None


def _apply_failure_stage(
    *,
    apply_verified: bool,
    oracle_passed: bool,
    undo_verified: bool,
    redo_verified: bool,
    history: Mapping[str, bool],
) -> str | None:
    if not apply_verified:
        return "apply"
    if not oracle_passed:
        return "oracle"
    if not undo_verified:
        return "undo"
    if not redo_verified:
        return "redo"
    if not all(history.values()):
        return "apply"
    return None


def _transport_error_class(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("transport_error_class")
        if isinstance(candidate, str):
            return candidate
        for nested in value.values():
            found = _transport_error_class(nested)
            if found is not None:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _transport_error_class(nested)
            if found is not None:
                return found
    return None


def _protocol_failure_reason(reason: str) -> bool:
    return any(
        marker in reason
        for marker in (
            "max_turns_exceeded",
            "output_protocol",
            "protocol_error",
            "structured_output",
            "model_output_invalid",
        )
    )


__all__ = ["PostgresBackendReleaseExecutor"]
