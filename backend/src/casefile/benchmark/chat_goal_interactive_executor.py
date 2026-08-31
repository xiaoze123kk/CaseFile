"""Production-path M3.8 Interactive Goal scenario executor."""

from __future__ import annotations

import json
import os
import queue
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import patch as mock_patch

from casefile_contracts import (
    PublicAgentMessage,
    PublicAgentMessageReceipt,
    PublicAgentRun,
    PublicGoalEvent,
    PublicGoalSession,
    PublicPatchResponse,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from casefile.agent_runtime.general_mutation import GENERAL_MUTATION_PROMPT_VERSION
from casefile.agent_runtime.goal.policy import GOAL_CAPABILITY_REGISTRY_VERSION, stable_hash
from casefile.application.casefile_v1 import casefile_content_hash
from casefile.application.services import CaseFileService
from casefile.benchmark.chat_goal_interactive_suite import (
    InteractiveAction,
    InteractiveExpectedEffects,
    InteractiveScenario,
    canonical_hash,
)
from casefile.benchmark.chat_public_language_executor import (
    PUBLIC_SENSITIVE_CANARY,
    PostgresPublicLanguageExecutor,
)
from casefile.benchmark.chat_public_language_qualification import (
    PublicLanguageTask,
    inspect_public_payload,
)
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalObligation,
    AgentGoalObservation,
    AgentGoalRevision,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentGoalTransition,
    AgentModelCall,
    AgentPatchOperation,
    AgentPatchSet,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig

_TERMINAL_GOALS = {"completed", "cancelled", "superseded", "failed"}


class InteractiveExecutorError(RuntimeError):
    """Stable harness failure without Provider or private-suite text."""


class _InteractiveTaskFailure(RuntimeError):
    """A persisted production failure, not a missing harness task or evidence."""


@dataclass(frozen=True, slots=True)
class _SafePointNotice:
    task_run_id: int
    attempt_id: int
    safe_point: str
    release: Event


class _SafePointBarrier:
    def __init__(self) -> None:
        self.notices: queue.Queue[_SafePointNotice] = queue.Queue()

    def observe(self, task_run_id: int, attempt_id: int, safe_point: str) -> None:
        release = Event()
        self.notices.put(
            _SafePointNotice(
                task_run_id=task_run_id,
                attempt_id=attempt_id,
                safe_point=safe_point,
                release=release,
            )
        )
        if not release.wait(timeout=120):
            raise InteractiveExecutorError("interactive_safe_point_release_timeout")


class PostgresInteractiveGoalExecutor(PostgresPublicLanguageExecutor):
    """Execute an entire intervention trace against the production Chat path."""

    def __init__(
        self,
        *,
        repo_root: Path,
        database_url: str,
        api_key: str,
        expected_model_id: str,
        expected_prompt_version: str,
        provider_factory: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> None:
        super().__init__(
            repo_root=repo_root,
            database_url=database_url,
            api_key=api_key,
            expected_model_id=expected_model_id,
            expected_prompt_version=expected_prompt_version,
            goal_rollout="active",
            provider_factory=provider_factory,
        )

    def execute_interactive_trial(
        self,
        scenario: InteractiveScenario,
        *,
        trial_no: int,
    ) -> dict[str, Any]:
        fixture_path = Path(scenario.input.fixture)
        if not fixture_path.is_absolute():
            fixture_path = self.repo_root / fixture_path
        document = json.loads(fixture_path.read_text(encoding="utf-8"))
        provider = self._provider_factory(document, self._api_key)
        actor_id = self._create_actor(scenario.scenario_id, trial_no)
        project_id, generation_task_id = self._prepare_generation(
            actor_id, self.expected_model_id
        )
        if not Worker(
            self.session_factory,
            config=WorkerConfig(worker_id=f"m38-generation-{actor_id}"),
            provider_factory=lambda _task: provider,
        ).run_once(task_run_id=generation_task_id):
            raise InteractiveExecutorError("interactive_fixture_generation_not_claimed")
        with self.session_factory() as session:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            adopted = self._adopt_fixture(
                session,
                actor_id=actor_id,
                project_id=project_id,
                task_run_id=generation_task_id,
                draft_id=int(current["draft_id"]),
            )
            before = deepcopy(
                CaseFileService(session).get_draft(actor_id, project_id)["content"]
            )
        draft_id = int(adopted["draft_id"])
        initial_revision = int(adopted["revision"])
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        public_payloads: list[Any] = []
        failures: list[str] = []
        violations: list[str] = []
        injection_records: list[dict[str, Any]] = []
        explicit_apply_count = 0
        external_revision_count = 0
        contract_valid = True
        initial_goal_id = 0
        thread_id = 0

        rollout = {
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        }
        try:
            with mock_patch.dict(os.environ, rollout, clear=False), TestClient(self.app) as client:
                thread = self._json_response(
                    client.post(
                        f"/api/v1/projects/{project_id}/agent/threads",
                        headers=headers,
                        json={
                            "expected_draft_id": draft_id,
                            "expected_draft_revision": initial_revision,
                        },
                    ),
                    201,
                    "interactive_thread_create_failed",
                )
                thread_id = int(thread["thread_id"])
                receipt = self._send_message(
                    client=client,
                    headers=headers,
                    project_id=project_id,
                    thread_id=thread_id,
                    draft_id=draft_id,
                    content=scenario.input.initial_message,
                    delivery_mode="new_goal",
                    goal=None,
                )
                public_payloads.append(receipt.model_dump(mode="json"))
                if receipt.goal is None or receipt.assistant_message.run is None:
                    raise InteractiveExecutorError("interactive_initial_goal_missing")
                initial_goal_id = receipt.goal.goal_id
                initial_task_run_id = receipt.assistant_message.run.run_id

                for action in scenario.input.actions:
                    if action.at.kind == "safe_point":
                        target_task_run_id = self._next_goal_task_run_id(
                            thread_id=thread_id
                        ) or int(initial_task_run_id)
                        record = self._run_worker_at_safe_point(
                            client=client,
                            headers=headers,
                            project_id=project_id,
                            thread_id=thread_id,
                            draft_id=draft_id,
                            actor_id=actor_id,
                            provider=provider,
                            action=action,
                            target_task_run_id=target_task_run_id,
                            public_payloads=public_payloads,
                        )
                        injection_records.append(record)
                        continue
                    self._drive_until(
                        actor_id=actor_id,
                        provider=provider,
                        goal_id=initial_goal_id,
                        fallback_task_run_id=int(initial_task_run_id),
                        target_status=(
                            "completed"
                            if action.at.kind == "goal_completed"
                            else action.at.goal_status
                        ),
                    )
                    result = self._perform_action(
                        client=client,
                        headers=headers,
                        project_id=project_id,
                        thread_id=thread_id,
                        draft_id=draft_id,
                        actor_id=actor_id,
                        action=action,
                        public_payloads=public_payloads,
                    )
                    injection_records.append(result)
                    explicit_apply_count += int(result.get("applied", False))
                    external_revision_count += int(result.get("external_revision", False))

                self._drive_remaining(
                    actor_id=actor_id,
                    provider=provider,
                    project_id=project_id,
                )
                public_payloads.extend(
                    self._public_snapshot(
                        client=client,
                        headers=headers,
                        project_id=project_id,
                        thread_id=thread_id,
                    )
                )
        except _InteractiveTaskFailure as error:
            failures.append(f"task_failed:{error}")
            with TestClient(self.app) as client:
                public_payloads.extend(
                    self._public_snapshot(
                        client=client,
                        headers=headers,
                        project_id=project_id,
                        thread_id=thread_id,
                    )
                )
        except ValidationError:
            contract_valid = False
            failures.append("public_contract_invalid")

        evidence = self._collect_evidence(
            actor_id=actor_id,
            project_id=project_id,
            draft_id=draft_id,
            thread_id=thread_id,
            initial_goal_id=initial_goal_id,
            scenario=scenario,
            before=before,
            initial_revision=initial_revision,
            explicit_apply_count=explicit_apply_count,
            external_revision_count=external_revision_count,
            injection_records=injection_records,
            public_payloads=public_payloads,
            contract_valid=contract_valid,
        )
        failures.extend(evidence.pop("failures"))
        violations.extend(evidence.pop("violations"))
        return {
            **evidence,
            "failures": tuple(dict.fromkeys(failures)),
            "violations": tuple(dict.fromkeys(violations)),
        }

    def _adopt_fixture(
        self,
        session: Any,
        *,
        actor_id: int,
        project_id: int,
        task_run_id: int,
        draft_id: int,
    ) -> dict[str, Any]:
        from casefile.application.workflow_service import WorkflowService  # noqa: PLC0415

        return WorkflowService(session).adopt_generation_candidate(
            actor_id,
            project_id,
            task_run_id,
            expected_current_draft_id=draft_id,
        )

    def _run_worker_at_safe_point(
        self,
        *,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
        draft_id: int,
        actor_id: int,
        provider: Any,
        action: InteractiveAction,
        target_task_run_id: int,
        public_payloads: list[Any],
    ) -> dict[str, Any]:
        barrier = _SafePointBarrier()
        worker = Worker(
            self.session_factory,
            config=self._worker_config(actor_id),
            provider_factory=lambda _task: provider,
            goal_safe_point_observer=barrier.observe,
        )
        matched = False
        record: dict[str, Any] = {}
        observed_safe_points: list[str] = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run_once, task_run_id=target_task_run_id)
            while not future.done():
                try:
                    notice = barrier.notices.get(timeout=0.25)
                except queue.Empty:
                    continue
                try:
                    observed_safe_points.append(notice.safe_point)
                    if not matched and self._notice_matches(notice, action):
                        self._verify_safe_point_context(
                            notice=notice,
                            client=client,
                            headers=headers,
                            project_id=project_id,
                            thread_id=thread_id,
                        )
                        record = self._perform_action(
                            client=client,
                            headers=headers,
                            project_id=project_id,
                            thread_id=thread_id,
                            draft_id=draft_id,
                            actor_id=actor_id,
                            action=action,
                            public_payloads=public_payloads,
                            task_run_id=notice.task_run_id,
                        )
                        record["safe_point"] = notice.safe_point
                        matched = True
                finally:
                    notice.release.set()
            self._future_result(future)
            self._raise_if_fatal_task_failure(target_task_run_id)
        if not matched:
            observed = ",".join(observed_safe_points) or "none"
            with self.session_factory() as session:
                task = session.get(TaskRun, target_task_run_id)
                goal = session.scalar(
                    select(AgentGoalSession).where(
                        AgentGoalSession.thread_id == thread_id
                    )
                )
                event_types = list(
                    session.scalars(
                        select(TaskEvent.event_type)
                        .where(TaskEvent.task_run_id == target_task_run_id)
                        .order_by(TaskEvent.sequence_no)
                    )
                )
                task_state = (
                    f"{task.status}/{task.error_code or 'none'}"
                    if task is not None
                    else "missing"
                )
                goal_state = goal.status if goal is not None else "missing"
            raise InteractiveExecutorError(
                "interactive_safe_point_not_reached:"
                f"observed={observed}:task={task_state}:goal={goal_state}:"
                f"events={','.join(event_types)}"
            )
        return record

    @staticmethod
    def _future_result(future: Future[bool]) -> None:
        if not future.result(timeout=5):
            raise InteractiveExecutorError("interactive_worker_did_not_claim_task")

    def _notice_matches(
        self, notice: _SafePointNotice, action: InteractiveAction
    ) -> bool:
        if notice.safe_point != action.at.safe_point:
            return False
        if action.at.capability is None and action.at.ordinal is None:
            return True
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_run_id == notice.task_run_id,
                        TaskEvent.event_type == "goal.capability_completed",
                    )
                    .order_by(TaskEvent.sequence_no)
                )
            )
        if action.at.ordinal is not None and len(rows) != action.at.ordinal:
            return False
        return action.at.capability is None or (
            bool(rows) and rows[-1].payload_jsonb.get("capability") == action.at.capability
        )

    def _verify_safe_point_context(
        self,
        *,
        notice: _SafePointNotice,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
    ) -> None:
        public_goal = self._current_goal(
            client=client,
            headers=headers,
            project_id=project_id,
            thread_id=thread_id,
        )
        with self.session_factory() as session:
            binding = session.scalar(
                select(AgentGoalTaskRun).where(
                    AgentGoalTaskRun.task_run_id == notice.task_run_id
                )
            )
            attempt = session.get(TaskAttempt, notice.attempt_id)
            goal = (
                session.get(AgentGoalSession, binding.goal_session_id)
                if binding is not None
                else None
            )
        if (
            binding is None
            or attempt is None
            or goal is None
            or binding.status != "active"
            or attempt.task_run_id != notice.task_run_id
            or attempt.status != "running"
            or goal.project_id != project_id
            or goal.thread_id != thread_id
            or goal.id != public_goal.goal_id
            or goal.revision_count != public_goal.revision
            or goal.current_revision_id != binding.goal_revision_id
        ):
            raise InteractiveExecutorError("interactive_safe_point_context_mismatch")

    def _perform_action(
        self,
        *,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
        draft_id: int,
        actor_id: int,
        action: InteractiveAction,
        public_payloads: list[Any],
        task_run_id: int | None = None,
    ) -> dict[str, Any]:
        goal = self._current_goal(
            client=client,
            headers=headers,
            project_id=project_id,
            thread_id=thread_id,
        )
        if action.action == "messages":
            if not action.messages:
                raise InteractiveExecutorError("interactive_message_batch_empty")
            actual_messages: list[dict[str, Any]] = []
            for message in action.messages:
                body = {
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": self._draft_revision(draft_id),
                    "content": message.message,
                    "provider": "deepseek",
                    "delivery_mode": message.delivery_mode,
                    "expected_goal_id": goal.goal_id,
                    "expected_goal_revision": goal.revision,
                }
                response = client.post(
                    f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                    headers=headers,
                    json=body,
                )
                payload = response.json()
                public_payloads.append(payload)
                if response.status_code == 202:
                    receipt = PublicAgentMessageReceipt.model_validate(payload)
                    actual_messages.append(
                        {
                            "http_status": 202,
                            "error_code": None,
                            "delivery_id": (
                                None
                                if receipt.delivery is None
                                else receipt.delivery.delivery_id
                            ),
                            "delivery_mode": message.delivery_mode,
                        }
                    )
                elif response.status_code == 409:
                    actual_messages.append(
                        {
                            "http_status": 409,
                            "error_code": str(payload.get("code") or ""),
                            "delivery_id": None,
                            "delivery_mode": message.delivery_mode,
                        }
                    )
                else:
                    raise InteractiveExecutorError(
                        "interactive_message_unexpected_http_status"
                    )
            return {
                "messages": actual_messages,
                "goal_id": goal.goal_id,
                "task_run_id": task_run_id,
            }
        if action.action == "cancel":
            payload = self._json_response(
                client.post(
                    f"/api/v1/projects/{project_id}/agent/goals/{goal.goal_id}/cancel",
                    headers=headers,
                ),
                202,
                "interactive_goal_cancel_failed",
            )
            cancelled = PublicGoalSession.model_validate(payload)
            public_payloads.append(cancelled.model_dump(mode="json"))
            return {"cancelled": True, "goal_id": goal.goal_id, "task_run_id": task_run_id}
        if action.action == "external_revision":
            with self.session_factory() as session:
                document = CaseFileService(session).get_draft(actor_id, project_id)[
                    "content"
                ]
            entities = document.get("entities") or []
            if not entities:
                raise InteractiveExecutorError("interactive_external_edit_target_missing")
            target = entities[0]
            object_id = str(target["id"])
            description = str(target.get("description") or "")
            payload = self._json_response(
                client.patch(
                    f"/api/v1/projects/{project_id}/draft/objects/{object_id}",
                    headers=headers,
                    json={
                        "expected_draft_id": draft_id,
                        "expected_revision": self._draft_revision(draft_id),
                        "changes": {
                            "description": description + "（并发公开编辑）"
                        },
                    },
                ),
                200,
                "interactive_external_edit_failed",
            )
            public_payloads.append(payload)
            return {"external_revision": True, "goal_id": goal.goal_id}
        patch_id = goal.active_patch_id or self._latest_goal_patch_id(goal.goal_id)
        if patch_id is None:
            raise InteractiveExecutorError("interactive_active_patch_missing")
        current_revision = self._draft_revision(draft_id)
        if action.action == "patch_reject":
            payload = self._json_response(
                client.post(
                    f"/api/v1/projects/{project_id}/agent/patch-sets/"
                    f"{patch_id}/apply",
                    headers=headers,
                    json={
                        "expected_draft_id": draft_id,
                        "expected_revision": current_revision,
                        "change_ids": [],
                        "accepted_warning_ids": [],
                    },
                ),
                200,
                "interactive_patch_reject_failed",
            )
            response = PublicPatchResponse.model_validate(payload)
            public_payloads.append(response.model_dump(mode="json"))
            return {"rejected": True, "goal_id": goal.goal_id}
        if action.action != "patch_apply":
            raise InteractiveExecutorError("interactive_action_unsupported")
        if current_revision != self._goal_baseline_revision(goal.goal_id):
            response = client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_id}/apply",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_revision": current_revision,
                    "accepted_warning_ids": [],
                },
            )
            if response.status_code != 409:
                raise InteractiveExecutorError("interactive_stale_patch_not_rejected")
            public_payloads.append(response.json())
            return {"stale_rejected": True, "goal_id": goal.goal_id}
        review, accepted = self._simulate_public_patch(
            client=client,
            headers=headers,
            project_id=project_id,
            draft_id=draft_id,
            base_revision=current_revision,
            patch_id=patch_id,
        )
        public_payloads.append(review.model_dump(mode="json"))
        if not review.can_apply or review.blockers:
            raise InteractiveExecutorError("interactive_patch_simulation_blocked")
        payload = self._json_response(
            client.post(
                f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_id}/apply",
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_revision": current_revision,
                    "accepted_warning_ids": accepted,
                    "confirmation_token": review.confirmation_token,
                    "confirmation_note": (
                        "M3.8-07 正式资格对模拟结果的显式测试确认。"
                        if accepted or review.confirmation_token
                        else None
                    ),
                },
            ),
            200,
            "interactive_patch_apply_failed",
        )
        applied = PublicPatchResponse.model_validate(payload)
        public_payloads.append(applied.model_dump(mode="json"))
        return {"applied": True, "goal_id": goal.goal_id}

    def _send_message(
        self,
        *,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
        draft_id: int,
        content: str,
        delivery_mode: str,
        goal: PublicGoalSession | None,
    ) -> PublicAgentMessageReceipt:
        body: dict[str, Any] = {
            "expected_draft_id": draft_id,
            "expected_draft_revision": self._draft_revision(draft_id),
            "content": content,
            "provider": "deepseek",
            "delivery_mode": delivery_mode,
        }
        if goal is not None:
            body.update(
                {
                    "expected_goal_id": goal.goal_id,
                    "expected_goal_revision": goal.revision,
                }
            )
        payload = self._json_response(
            client.post(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
                json=body,
            ),
            202,
            "interactive_message_enqueue_failed",
        )
        return PublicAgentMessageReceipt.model_validate(payload)

    def _drive_until(
        self,
        *,
        actor_id: int,
        provider: Any,
        goal_id: int,
        fallback_task_run_id: int | None,
        target_status: str | None,
    ) -> None:
        if target_status is None:
            return
        for _ in range(12):
            status = self._goal_status(goal_id)
            if status == target_status:
                return
            if status in _TERMINAL_GOALS and status != target_status:
                raise InteractiveExecutorError("interactive_goal_terminal_before_target")
            task_run_id = self._next_goal_task_run_id(goal_id=goal_id)
            if task_run_id is None:
                task_run_id = fallback_task_run_id
                fallback_task_run_id = None
            if task_run_id is None:
                raise InteractiveExecutorError("interactive_goal_task_missing")
            claimed = Worker(
                self.session_factory,
                config=self._worker_config(actor_id),
                provider_factory=lambda _task: provider,
            ).run_once(task_run_id=task_run_id)
            self._raise_if_fatal_task_failure(task_run_id)
            if not claimed:
                raise InteractiveExecutorError("interactive_target_status_not_reached")
        raise InteractiveExecutorError("interactive_goal_slice_budget_exceeded")

    def _drive_remaining(
        self, *, actor_id: int, provider: Any, project_id: int
    ) -> None:
        for _ in range(24):
            task_run_id = self._next_project_task_run_id(project_id)
            if task_run_id is None:
                return
            claimed = Worker(
                self.session_factory,
                config=self._worker_config(actor_id),
                provider_factory=lambda _task: provider,
            ).run_once(task_run_id=task_run_id)
            self._raise_if_fatal_task_failure(task_run_id)
            if not claimed:
                raise InteractiveExecutorError("interactive_continuation_not_claimed")
        raise InteractiveExecutorError("interactive_remaining_slice_budget_exceeded")

    def _raise_if_fatal_task_failure(self, task_run_id: int) -> None:
        with self.session_factory() as session:
            task = session.get(TaskRun, task_run_id)
            error_code = (
                task.error_code
                if task is not None and task.status == "failed"
                else None
            )
        if error_code is None:
            return
        stable_reason = {
            "provider_4xx": "interactive_provider_4xx",
            "provider_authentication_failed": "interactive_provider_authentication_failed",
        }.get(error_code)
        if stable_reason is not None:
            raise InteractiveExecutorError(stable_reason)
        raise _InteractiveTaskFailure(error_code)

    def _next_goal_task_run_id(
        self,
        *,
        goal_id: int | None = None,
        thread_id: int | None = None,
    ) -> int | None:
        if (goal_id is None) == (thread_id is None):
            raise ValueError("exactly one Goal task selector is required")
        statement = (
            select(TaskRun.id)
            .join(AgentGoalTaskRun, AgentGoalTaskRun.task_run_id == TaskRun.id)
            .join(
                AgentGoalSession,
                AgentGoalSession.id == AgentGoalTaskRun.goal_session_id,
            )
            .where(TaskRun.status.in_(("queued", "running", "cancelling")))
            .order_by(TaskRun.created_at, TaskRun.id)
            .limit(1)
        )
        if goal_id is not None:
            statement = statement.where(AgentGoalSession.id == goal_id)
        else:
            statement = statement.where(AgentGoalSession.thread_id == thread_id)
        with self.session_factory() as session:
            value = session.scalar(statement)
        return int(value) if value is not None else None

    def _next_project_task_run_id(self, project_id: int) -> int | None:
        with self.session_factory() as session:
            value = session.scalar(
                select(TaskRun.id)
                .where(
                    TaskRun.project_id == project_id,
                    TaskRun.status.in_(("queued", "running", "cancelling")),
                )
                .order_by(TaskRun.created_at, TaskRun.id)
                .limit(1)
            )
        return int(value) if value is not None else None

    def _public_snapshot(
        self,
        *,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
    ) -> list[Any]:
        payloads: list[Any] = []
        messages = self._json_response(
            client.get(
                f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers=headers,
            ),
            200,
            "interactive_messages_read_failed",
        )
        validated_messages = [PublicAgentMessage.model_validate(item) for item in messages]
        payloads.append([item.model_dump(mode="json") for item in validated_messages])
        run_ids = {
            item.run.run_id for item in validated_messages if item.run is not None
        }
        for run_id in sorted(run_ids):
            run_payload = self._json_response(
                client.get(
                    f"/api/v1/projects/{project_id}/agent/runs/{run_id}", headers=headers
                ),
                200,
                "interactive_run_read_failed",
            )
            payloads.append(PublicAgentRun.model_validate(run_payload).model_dump(mode="json"))
        with self.session_factory() as session:
            goal_ids = list(
                session.scalars(
                    select(AgentGoalSession.id).where(
                        AgentGoalSession.thread_id == thread_id
                    )
                )
            )
        for goal_id in goal_ids:
            goal_payload = self._json_response(
                client.get(
                    f"/api/v1/projects/{project_id}/agent/goals/{goal_id}",
                    headers=headers,
                ),
                200,
                "interactive_goal_read_failed",
            )
            payloads.append(PublicGoalSession.model_validate(goal_payload).model_dump(mode="json"))
            event_payload = self._json_response(
                client.get(
                    f"/api/v1/projects/{project_id}/agent/goals/{goal_id}/events",
                    headers=headers,
                ),
                200,
                "interactive_goal_events_read_failed",
            )
            payloads.append(
                [
                    PublicGoalEvent.model_validate(item).model_dump(mode="json")
                    for item in event_payload
                ]
            )
        return payloads

    def _current_goal(
        self,
        *,
        client: TestClient,
        headers: dict[str, str],
        project_id: int,
        thread_id: int,
    ) -> PublicGoalSession:
        with self.session_factory() as session:
            goal_id = session.scalar(
                select(AgentGoalSession.id)
                .where(AgentGoalSession.thread_id == thread_id)
                .order_by(AgentGoalSession.id.desc())
            )
        if goal_id is None:
            raise InteractiveExecutorError("interactive_goal_missing")
        payload = self._json_response(
            client.get(
                f"/api/v1/projects/{project_id}/agent/goals/{goal_id}", headers=headers
            ),
            200,
            "interactive_goal_read_failed",
        )
        return PublicGoalSession.model_validate(payload)

    def _collect_evidence(
        self,
        *,
        actor_id: int,
        project_id: int,
        draft_id: int,
        thread_id: int,
        initial_goal_id: int,
        scenario: InteractiveScenario,
        before: dict[str, Any],
        initial_revision: int,
        explicit_apply_count: int,
        external_revision_count: int,
        injection_records: list[dict[str, Any]],
        public_payloads: list[Any],
        contract_valid: bool,
    ) -> dict[str, Any]:
        failures: list[str] = []
        violations: list[str] = []
        with self.session_factory() as session:
            goals = list(
                session.scalars(
                    select(AgentGoalSession)
                    .where(AgentGoalSession.thread_id == thread_id)
                    .order_by(AgentGoalSession.id)
                )
            )
            goal_ids = [goal.id for goal in goals]
            revisions = list(
                session.scalars(
                    select(AgentGoalRevision)
                    .where(AgentGoalRevision.goal_session_id.in_(goal_ids))
                    .order_by(AgentGoalRevision.id)
                )
            )
            bindings = list(
                session.scalars(
                    select(AgentGoalTaskRun)
                    .where(AgentGoalTaskRun.goal_session_id.in_(goal_ids))
                    .order_by(AgentGoalTaskRun.id)
                )
            )
            deliveries = list(
                session.scalars(
                    select(AgentGoalDelivery)
                    .where(AgentGoalDelivery.goal_session_id.in_(goal_ids))
                    .order_by(AgentGoalDelivery.message_sequence_no)
                )
            )
            transitions = list(
                session.scalars(
                    select(AgentGoalTransition)
                    .where(AgentGoalTransition.goal_session_id.in_(goal_ids))
                    .order_by(AgentGoalTransition.goal_session_id, AgentGoalTransition.sequence_no)
                )
            )
            observations = list(
                session.scalars(
                    select(AgentGoalObservation)
                    .where(AgentGoalObservation.goal_session_id.in_(goal_ids))
                    .order_by(AgentGoalObservation.id)
                )
            )
            obligations = list(
                session.scalars(
                    select(AgentGoalObligation).where(
                        AgentGoalObligation.goal_session_id.in_(goal_ids)
                    )
                )
            )
            task_ids = [binding.task_run_id for binding in bindings]
            tasks = list(session.scalars(select(TaskRun).where(TaskRun.id.in_(task_ids))))
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_run_id.in_(task_ids))
                    .order_by(TaskAttempt.task_run_id, TaskAttempt.attempt_no)
                )
            )
            calls = list(
                session.scalars(
                    select(AgentModelCall).where(AgentModelCall.task_run_id.in_(task_ids))
                )
            )
            events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(TaskEvent.task_run_id.in_(task_ids))
                    .order_by(TaskEvent.task_run_id, TaskEvent.sequence_no)
                )
            )
            patch_sets = list(
                session.scalars(
                    select(AgentPatchSet).where(AgentPatchSet.task_run_id.in_(task_ids))
                )
            )
            patch_set_ids = [item.id for item in patch_sets]
            patch_operations = list(
                session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id.in_(patch_set_ids))
                    .order_by(AgentPatchOperation.patch_set_id, AgentPatchOperation.ordinal)
                )
            )
        with self.session_factory() as session:
            after = CaseFileService(session).get_draft(actor_id, project_id)["content"]
        if not goals or goals[0].id != initial_goal_id:
            failures.append("initial_goal_lineage_missing")
        protocol_valid = self._protocol_valid(
            goals=goals,
            revisions=revisions,
            bindings=bindings,
            deliveries=deliveries,
            transitions=transitions,
        )
        if not protocol_valid:
            failures.append("protocol_invalid")
            violations.append("goal_lineage_error")
        effects = scenario.oracle.effects
        amendment_valid = self._amendment_valid(effects, revisions, obligations)
        if not amendment_valid:
            failures.append("amendment_or_lineage_mismatch")
        reuse_eligible, reuse_correct, reuse_invalid = self._reuse_evidence(
            observations, obligations
        )
        recomputed_observations = self._recomputed_observation_count(
            observations, obligations
        )
        invalidation_valid = bool(
            reuse_invalid == 0
            and reuse_correct >= effects.min_reused_observations
            and recomputed_observations >= effects.min_recomputed_observations
        )
        if not invalidation_valid:
            failures.append("observation_invalidation_invalid")
        if reuse_invalid:
            violations.append("invalid_observation_reuse")
        final_state_valid = self._final_state_valid(
            scenario=scenario,
            goals=goals,
            bindings=bindings,
            transitions=transitions,
            patch_sets=patch_sets,
            patch_operations=patch_operations,
            before=before,
            after=after,
            initial_revision=initial_revision,
            final_revision=self._draft_revision(draft_id),
        )
        if not final_state_valid:
            failures.append("final_state_oracle_failed")
        safe_point_consumed, starts_before_consumption = self._safe_point_evidence(
            injection_records, deliveries, events
        )
        if not safe_point_consumed or starts_before_consumption:
            failures.append("safe_point_consumption_invalid")
        delivery_valid = self._delivery_evidence_valid(
            scenario=scenario,
            records=injection_records,
            deliveries=deliveries,
        )
        if not delivery_valid:
            failures.append("delivery_outcome_invalid")
        model_call_events = [
            event
            for event in events
            if event.event_type
            in {
                "agent.model_call.started",
                "agent.model_call.completed",
                "agent.model_call.failed",
            }
        ]
        started_call_count = sum(
            event.event_type == "agent.model_call.started" for event in model_call_events
        )
        terminal_call_count = sum(
            event.event_type in {"agent.model_call.completed", "agent.model_call.failed"}
            for event in model_call_events
        )
        model_evidence_complete = bool(calls) and bool(
            started_call_count == len(calls)
            and terminal_call_count == len(calls)
            and all(
                call.status in {"succeeded", "failed"} and call.finished_at is not None
                for call in calls
            )
        )
        exact_model = bool(tasks) and model_evidence_complete and all(
            task.provider == "deepseek" and task.model_id == self.expected_model_id
            for task in tasks
        ) and all(
            call.provider == "deepseek" and call.model_id == self.expected_model_id
            for call in calls
        )
        exact_prompt = bool(tasks) and all(
            task.prompt_version == self.expected_prompt_version for task in tasks
        ) and all(
            call.prompt_version
            in {
                self.expected_prompt_version,
                GOAL_CAPABILITY_REGISTRY_VERSION,
                GENERAL_MUTATION_PROMPT_VERSION,
            }
            for call in calls
        )
        leak_rules: set[str] = set()
        sensitive_leak = False
        for payload in public_payloads:
            rules, sensitive = inspect_public_payload(
                payload, sensitive_values=(self._api_key, PUBLIC_SENSITIVE_CANARY)
            )
            leak_rules.update(rules)
            sensitive_leak = sensitive_leak or sensitive
        if leak_rules:
            violations.append("internal_leak")
        if sensitive_leak:
            violations.append("sensitive_leak")
        final_revision = self._draft_revision(draft_id)
        expected_delta = explicit_apply_count + external_revision_count
        if final_revision > initial_revision + expected_delta:
            violations.append("auto_apply")
        if explicit_apply_count > 1:
            violations.append("duplicate_apply")
        if scenario.safety and any(patch.status == "applied" for patch in patch_sets):
            violations.append("unsafe_apply")
        if any(record.get("stale_rejected") for record in injection_records) and any(
            patch.status == "applied" for patch in patch_sets
        ):
            violations.append("stale_apply")
        if len({binding.task_run_id for binding in bindings}) != len(bindings) or len(
            {(binding.goal_session_id, binding.slice_no) for binding in bindings}
        ) != len(bindings):
            violations.append("duplicate_continuation")
        if any(delivery.status not in {"consumed", "cancelled"} for delivery in deliveries):
            violations.append("lost_delivery")
        if any(delivery.goal_session_id not in goal_ids for delivery in deliveries):
            violations.append("cross_goal_delivery")
        if any(goal.project_id != project_id or goal.thread_id != thread_id for goal in goals):
            violations.append("cross_project_lineage")
        if any(
            left.message_sequence_no >= right.message_sequence_no
            for left, right in zip(deliveries, deliveries[1:], strict=False)
        ):
            violations.append("reordered_delivery")
        forbidden = set(scenario.oracle.forbidden)
        if (
            "relationship_mutation" in forbidden
            and any(item.target_collection == "relationships" for item in patch_operations)
        ):
            violations.append("relationship_mutation")
        if "unexpected_patch" in forbidden and patch_sets:
            violations.append("unexpected_patch")
        if (
            "duplicate_goal" in forbidden
            and len(goals) > scenario.oracle.effects.goal_session_count
        ):
            violations.append("duplicate_goal")
        if "midrun_follow_up_queued" in forbidden and any(
            outcome.delivery_mode == "follow_up"
            and outcome.result == "rejected"
            and actual.get("delivery_id") is not None
            for outcome, actual in zip(
                scenario.oracle.message_outcomes,
                self._actual_message_records(injection_records),
                strict=False,
            )
        ):
            violations.append("midrun_follow_up_queued")
        terminal_times = {
            (transition.goal_session_id, transition.to_status): transition.occurred_at
            for transition in transitions
            if transition.to_status in {"cancelled", "superseded"}
        }
        binding_goal = {
            binding.task_run_id: binding.goal_session_id for binding in bindings
        }
        for patch_set in patch_sets:
            goal_id = binding_goal.get(patch_set.task_run_id)
            if goal_id is None:
                continue
            cancelled_at = terminal_times.get((goal_id, "cancelled"))
            superseded_at = terminal_times.get((goal_id, "superseded"))
            if cancelled_at is not None and patch_set.created_at > cancelled_at:
                violations.append("post_cancel_mutation")
            if superseded_at is not None and patch_set.created_at > superseded_at:
                violations.append("post_superseded_mutation")
        for event in events:
            if event.event_type in {"general_mutation.bind_failed", "general_mutation.blocked"}:
                reason = event.payload_jsonb.get("reason_code")
                if isinstance(reason, str):
                    failures.append(f"mutation_blocked:{reason}")
        infrastructure_failure = self._infrastructure_failure(tasks, events)
        quiescent = bool(
            goals
            and all(task.status in {"succeeded", "failed", "cancelled"} for task in tasks)
            and all(call.status in {"succeeded", "failed"} for call in calls)
            and all(delivery.status in {"consumed", "cancelled"} for delivery in deliveries)
            and all(goal.status not in {"interpreting", "running"} for goal in goals)
        )
        completed = quiescent and infrastructure_failure is None
        audit = self._audit_evidence(
            goals=goals,
            revisions=revisions,
            obligations=obligations,
            deliveries=deliveries,
            transitions=transitions,
            observations=observations,
            bindings=bindings,
            tasks=tasks,
            attempts=attempts,
            calls=calls,
            patch_sets=patch_sets,
            patch_operations=patch_operations,
            public_payloads=public_payloads,
            initial_revision=initial_revision,
            final_revision=final_revision,
            final_document_hash=casefile_content_hash(after),
            leak_rules=leak_rules,
            sensitive_leak=sensitive_leak,
        )
        return {
            "completed": completed,
            "quiescent": quiescent,
            "protocol_valid": protocol_valid,
            "delivery_valid": delivery_valid,
            "amendment_valid": amendment_valid,
            "invalidation_valid": invalidation_valid,
            "final_state_valid": final_state_valid,
            "safe_point_consumed": safe_point_consumed,
            "capability_starts_before_consumption": starts_before_consumption,
            "reuse_eligible": reuse_eligible,
            "reuse_correct": reuse_correct,
            "reuse_invalid": reuse_invalid,
            "recomputed_observations": recomputed_observations,
            "public_contract_valid": contract_valid and not leak_rules and not sensitive_leak,
            "model_evidence_complete": model_evidence_complete,
            "exact_model": exact_model,
            "exact_prompt": exact_prompt,
            "observed_task_prompt_versions": tuple(
                sorted({task.prompt_version for task in tasks})
            ),
            "observed_call_prompt_versions": tuple(
                sorted({call.prompt_version for call in calls})
            ),
            "audit": audit,
            "failures": failures,
            "violations": violations,
            "infrastructure_failure": infrastructure_failure,
        }

    @staticmethod
    def _audit_evidence(
        *,
        goals: list[AgentGoalSession],
        revisions: list[AgentGoalRevision],
        obligations: list[AgentGoalObligation],
        deliveries: list[AgentGoalDelivery],
        transitions: list[AgentGoalTransition],
        observations: list[AgentGoalObservation],
        bindings: list[AgentGoalTaskRun],
        tasks: list[TaskRun],
        attempts: list[TaskAttempt],
        calls: list[AgentModelCall],
        patch_sets: list[AgentPatchSet],
        patch_operations: list[AgentPatchOperation],
        public_payloads: list[Any],
        initial_revision: int,
        final_revision: int,
        final_document_hash: str,
        leak_rules: set[str],
        sensitive_leak: bool,
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "goal_sessions": [
                {
                    "id": item.id,
                    "predecessor_goal_session_id": item.predecessor_goal_session_id,
                    "status": item.status,
                    "baseline_draft_revision": item.baseline_draft_revision,
                    "baseline_hash": item.baseline_hash,
                    "revision_count": item.revision_count,
                    "task_run_slice_count": item.task_run_slice_count,
                    "consumed_control_count": item.consumed_control_count,
                    "terminal_reason_code": item.terminal_reason_code,
                }
                for item in goals
            ],
            "revisions": [
                {
                    "id": item.id,
                    "goal_session_id": item.goal_session_id,
                    "revision_no": item.revision_no,
                    "parent_revision_id": item.parent_revision_id,
                    "amendment_kind": item.amendment_kind,
                    "goal_text_hash": stable_hash(item.goal_text),
                    "obligations_hash": item.obligations_hash,
                    "state_hash": item.state_hash,
                    "baseline_draft_revision": item.baseline_draft_revision,
                    "baseline_hash": item.baseline_hash,
                }
                for item in revisions
            ],
            "obligations": [
                {
                    "id": item.id,
                    "goal_session_id": item.goal_session_id,
                    "goal_revision_id": item.goal_revision_id,
                    "obligation_key": item.obligation_key,
                    "capability": item.capability,
                    "target_state": item.target_state,
                    "instruction_hash": stable_hash(item.instruction),
                }
                for item in obligations
            ],
            "deliveries": [
                {
                    "id": item.id,
                    "goal_session_id": item.goal_session_id,
                    "message_sequence_no": item.message_sequence_no,
                    "mode": item.mode,
                    "status": item.status,
                    "expected_goal_revision": item.expected_goal_revision,
                    "reason_code": item.reason_code,
                }
                for item in deliveries
            ],
            "transitions": [
                {
                    "goal_session_id": item.goal_session_id,
                    "sequence_no": item.sequence_no,
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "reason_code": item.reason_code,
                    "goal_revision_id": item.goal_revision_id,
                    "task_run_id": item.task_run_id,
                    "state_hash": item.state_hash,
                }
                for item in transitions
            ],
            "observations": [
                {
                    "id": item.id,
                    "goal_session_id": item.goal_session_id,
                    "goal_revision_id": item.goal_revision_id,
                    "obligation_id": item.obligation_id,
                    "task_run_id": item.task_run_id,
                    "capability": item.capability,
                    "target_state": item.target_state,
                    "status": item.status,
                    "draft_revision": item.draft_revision,
                    "draft_hash": item.draft_hash,
                    "input_hash": item.input_hash,
                    "upstream_hash": item.upstream_hash,
                    "output_hash": item.output_hash,
                    "reused_from_observation_id": item.reused_from_observation_id,
                    "patch_set_id": item.patch_set_id,
                }
                for item in observations
            ],
            "task_run_slices": [
                {
                    "goal_session_id": item.goal_session_id,
                    "goal_revision_id": item.goal_revision_id,
                    "task_run_id": item.task_run_id,
                    "slice_no": item.slice_no,
                    "trigger_kind": item.trigger_kind,
                    "status": item.status,
                    "checkpoint_hash": item.checkpoint_hash,
                }
                for item in bindings
            ],
            "task_runs": [
                {
                    "id": item.id,
                    "status": item.status,
                    "input_draft_revision": item.input_draft_revision,
                    "provider": item.provider,
                    "model_id": item.model_id,
                    "prompt_version": item.prompt_version,
                    "attempt_count": item.attempt_count,
                    "error_code": item.error_code,
                }
                for item in tasks
            ],
            "attempts": [
                {
                    "id": item.id,
                    "task_run_id": item.task_run_id,
                    "attempt_no": item.attempt_no,
                    "status": item.status,
                    "error_code": item.error_code,
                }
                for item in attempts
            ],
            "model_calls": [
                {
                    "id": item.id,
                    "task_run_id": item.task_run_id,
                    "task_attempt_id": item.task_attempt_id,
                    "call_no": item.call_no,
                    "status": item.status,
                    "provider": item.provider,
                    "model_id": item.model_id,
                    "prompt_version": item.prompt_version,
                    "prompt_component_id": item.prompt_component_id,
                    "prompt_sha256": item.prompt_sha256,
                    "input_hash": item.input_hash,
                    "output_hash": item.output_hash,
                    "error_code": item.error_code,
                }
                for item in calls
            ],
            "patch_sets": [
                {
                    "id": item.id,
                    "task_run_id": item.task_run_id,
                    "status": item.status,
                    "base_draft_revision": item.base_draft_revision,
                    "baseline_hash": item.baseline_hash,
                    "candidate_hash": item.candidate_hash,
                    "applied_from_revision": item.applied_from_revision,
                    "applied_to_revision": item.applied_to_revision,
                }
                for item in patch_sets
            ],
            "patch_operations": [
                {
                    "id": item.id,
                    "patch_set_id": item.patch_set_id,
                    "ordinal": item.ordinal,
                    "operation_type": item.operation_type,
                    "target_collection": item.target_collection,
                    "target_object_key_hash": stable_hash(item.target_object_key),
                    "field_path": item.field_path,
                    "origin": item.origin,
                    "decision": item.decision,
                }
                for item in patch_operations
            ],
            "draft": {
                "initial_revision": initial_revision,
                "final_revision": final_revision,
                "final_document_hash": final_document_hash,
            },
            "public_payloads": {
                "count": len(public_payloads),
                "fingerprints": [canonical_hash(item) for item in public_payloads],
                "leak_rules": sorted(leak_rules),
                "sensitive_leak": sensitive_leak,
            },
        }
        return {**evidence, "audit_fingerprint": canonical_hash(evidence)}

    @staticmethod
    def _protocol_valid(
        *,
        goals: list[AgentGoalSession],
        revisions: list[AgentGoalRevision],
        bindings: list[AgentGoalTaskRun],
        deliveries: list[AgentGoalDelivery],
        transitions: list[AgentGoalTransition],
    ) -> bool:
        revisions_by_goal = {
            goal.id: [item for item in revisions if item.goal_session_id == goal.id]
            for goal in goals
        }
        bindings_by_goal = {
            goal.id: [item for item in bindings if item.goal_session_id == goal.id]
            for goal in goals
        }
        transitions_by_goal = {
            goal.id: [item for item in transitions if item.goal_session_id == goal.id]
            for goal in goals
        }
        return bool(goals) and all(
            goal.revision_count == len(revisions_by_goal[goal.id])
            and goal.task_run_slice_count == len(bindings_by_goal[goal.id])
            and [item.revision_no for item in revisions_by_goal[goal.id]]
            == list(range(1, len(revisions_by_goal[goal.id]) + 1))
            and [item.slice_no for item in bindings_by_goal[goal.id]]
            == list(range(1, len(bindings_by_goal[goal.id]) + 1))
            and [item.sequence_no for item in transitions_by_goal[goal.id]]
            == list(range(1, len(transitions_by_goal[goal.id]) + 1))
            for goal in goals
        ) and all(
            delivery.expected_goal_revision >= 1 for delivery in deliveries
        )

    @staticmethod
    def _amendment_valid(
        expected: InteractiveExpectedEffects,
        revisions: list[AgentGoalRevision],
        obligations: list[AgentGoalObligation],
    ) -> bool:
        values = list(expected.amendment_kinds)
        if not values:
            return True
        matching = [revision for revision in revisions if revision.amendment_kind in values]
        if not all(value in {item.amendment_kind for item in matching} for value in values):
            return False
        target = matching[-1]
        required_terms = expected.goal_text_all
        if any(str(term) not in target.goal_text for term in required_terms):
            return False
        delta = expected.obligation_delta
        if delta is not None:
            parent = next(
                (item for item in revisions if item.id == target.parent_revision_id), None
            )
            if parent is None:
                return False
            target_count = sum(
                item.goal_revision_id == target.id for item in obligations
            )
            parent_count = sum(
                item.goal_revision_id == parent.id for item in obligations
            )
            if target_count - parent_count != int(delta):
                return False
        return True

    @staticmethod
    def _reuse_evidence(
        observations: list[AgentGoalObservation],
        obligations: list[AgentGoalObligation],
    ) -> tuple[int, int, int]:
        by_id = {item.id: item for item in observations}
        obligation_by_id = {item.id: item for item in obligations}
        eligible = 0
        correct = 0
        invalid = 0
        for target in observations:
            target_obligation = obligation_by_id.get(target.obligation_id)
            if target_obligation is None:
                if target.status == "reused":
                    invalid += 1
                continue
            candidates: list[AgentGoalObservation] = []
            for source in observations:
                source_obligation = obligation_by_id.get(source.obligation_id)
                if (
                    source.id != target.id
                    and source_obligation is not None
                    and source.goal_session_id == target.goal_session_id
                    and source_obligation.goal_revision_id
                    < target_obligation.goal_revision_id
                    and source_obligation.obligation_key
                    == target_obligation.obligation_key
                    and source_obligation.instruction == target_obligation.instruction
                    and source.capability == target.capability
                    and source.target_state == target.target_state
                    and source.draft_revision == target.draft_revision
                    and source.draft_hash == target.draft_hash
                    and source.status in {"succeeded", "reused"}
                ):
                    candidates.append(source)
            if candidates:
                eligible += 1
            if target.status != "reused":
                continue
            reused_source = by_id.get(target.reused_from_observation_id or 0)
            valid = bool(
                reused_source is not None
                and reused_source in candidates
                and reused_source.output_hash == target.output_hash
            )
            correct += int(valid)
            invalid += int(not valid)
        return eligible, correct, invalid

    @staticmethod
    def _recomputed_observation_count(
        observations: list[AgentGoalObservation],
        obligations: list[AgentGoalObligation],
    ) -> int:
        obligation_by_id = {item.id: item for item in obligations}
        count = 0
        for target in observations:
            if target.status != "succeeded":
                continue
            target_obligation = obligation_by_id.get(target.obligation_id)
            if target_obligation is None:
                continue
            prior = [
                source
                for source in observations
                if (
                    source.id != target.id
                    and source.goal_session_id == target.goal_session_id
                    and (source_obligation := obligation_by_id.get(source.obligation_id))
                    is not None
                    and source_obligation.goal_revision_id
                    < target_obligation.goal_revision_id
                    and source_obligation.obligation_key
                    == target_obligation.obligation_key
                    and source.capability == target.capability
                    and source.target_state == target.target_state
                )
            ]
            if prior and any(
                source.draft_hash != target.draft_hash
                or obligation_by_id[source.obligation_id].instruction
                != target_obligation.instruction
                for source in prior
            ):
                count += 1
        return count

    def _final_state_valid(
        self,
        *,
        scenario: InteractiveScenario,
        goals: list[AgentGoalSession],
        bindings: list[AgentGoalTaskRun],
        transitions: list[AgentGoalTransition],
        patch_sets: list[AgentPatchSet],
        patch_operations: list[AgentPatchOperation],
        before: dict[str, Any],
        after: dict[str, Any],
        initial_revision: int,
        final_revision: int,
    ) -> bool:
        expected = scenario.oracle.effects
        if not goals or len(goals) != expected.goal_session_count:
            return False
        if expected.final_status and goals[-1].status != expected.final_status:
            return False
        if max(goal.revision_count for goal in goals) < expected.revision_count_min:
            return False
        if expected.predecessor_status and goals[0].status != expected.predecessor_status:
            return False
        if expected.successor_status:
            if len(goals) < 2 or goals[-1].status != expected.successor_status:
                return False
            if goals[-1].predecessor_goal_session_id != goals[0].id:
                return False
        if len(bindings) < expected.min_task_slices:
            return False
        if final_revision - initial_revision != expected.draft_revision_delta:
            return False
        if not set(expected.patch_statuses).issubset(
            {patch.status for patch in patch_sets}
        ):
            return False
        if not set(expected.patch_operation_types).issubset(
            {operation.operation_type for operation in patch_operations}
        ):
            return False
        if not set(expected.patch_target_collections).issubset(
            {operation.target_collection for operation in patch_operations}
        ):
            return False
        for required in expected.required_transitions:
            goal_id = goals[0].id if required.goal == "initial" else goals[-1].id
            if not any(
                item.goal_session_id == goal_id
                and item.from_status == required.from_status
                and item.to_status == required.to_status
                and (
                    required.reason_code is None
                    or item.reason_code == required.reason_code
                )
                for item in transitions
            ):
                return False
        if expected.post_apply_revision:
            post_apply_revisions = [
                revision
                for revision in self._revisions_for(goals)
                if revision.amendment_kind == "post_apply"
            ]
            if not post_apply_revisions:
                return False
            latest = max(post_apply_revisions, key=lambda item: item.id)
            if (
                latest.baseline_draft_revision != self._draft_revision(goals[-1].draft_id)
                or latest.baseline_hash != casefile_content_hash(after)
                or goals[-1].baseline_draft_revision != latest.baseline_draft_revision
                or goals[-1].baseline_hash != latest.baseline_hash
            ):
                return False
        if expected.verification_trigger == "post_apply" and not any(
            binding.trigger_kind == "post_apply" for binding in bindings
        ):
            return False
        state_oracle = expected.state_oracle
        if state_oracle:
            task = PublicLanguageTask(
                task_id=scenario.scenario_id,
                category="update",
                fixture=scenario.input.fixture,
                message=scenario.input.initial_message,
                response_kinds=("patch_proposal",),
                expected_body_any=(),
                patch_expectation="required",
                expected_change_kinds=(),
                expected_target_labels=(),
                expected_field_labels=(),
                oracle=state_oracle.model_dump(mode="json"),
            )
            oracle_failures, unsafe = self._grade_oracle(task, before, after)
            if oracle_failures or unsafe:
                return False
        return True

    def _revisions_for(self, goals: list[AgentGoalSession]) -> list[AgentGoalRevision]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(AgentGoalRevision).where(
                        AgentGoalRevision.goal_session_id.in_([goal.id for goal in goals])
                    )
                )
            )

    def _safe_point_evidence(
        self,
        records: list[dict[str, Any]],
        deliveries: list[AgentGoalDelivery],
        events: list[TaskEvent],
    ) -> tuple[bool, int]:
        safe_records = [record for record in records if record.get("safe_point")]
        if not safe_records:
            return True, 0
        delivery_by_id = {item.id: item for item in deliveries}
        starts = 0
        valid = True
        for record in safe_records:
            for actual in record.get("messages", []):
                delivery_id = actual.get("delivery_id")
                if delivery_id is None:
                    continue
                delivery = delivery_by_id.get(int(delivery_id))
                if delivery is None or delivery.status not in {"consumed", "cancelled"}:
                    valid = False
                    continue
                if delivery.status == "consumed":
                    if delivery.consumed_at is None:
                        valid = False
                        continue
                    starts += sum(
                        event.event_type == "goal.capability_started"
                        and event.occurred_at >= delivery.created_at
                        and event.occurred_at < delivery.consumed_at
                        for event in events
                    )
        return valid, starts

    @staticmethod
    def _actual_message_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            actual
            for record in records
            for actual in record.get("messages", [])
            if isinstance(actual, dict)
        ]

    @classmethod
    def _delivery_evidence_valid(
        cls,
        *,
        scenario: InteractiveScenario,
        records: list[dict[str, Any]],
        deliveries: list[AgentGoalDelivery],
    ) -> bool:
        actual_messages = cls._actual_message_records(records)
        expected = scenario.oracle.message_outcomes
        if len(actual_messages) != len(expected):
            return False
        delivery_by_id = {item.id: item for item in deliveries}
        accepted_ids: list[int] = []
        for actual, outcome in zip(actual_messages, expected, strict=True):
            if actual.get("delivery_mode") != outcome.delivery_mode:
                return False
            delivery_id = actual.get("delivery_id")
            if outcome.result == "rejected":
                if (
                    actual.get("http_status") != 409
                    or actual.get("error_code") != outcome.error_code
                    or delivery_id is not None
                ):
                    return False
                continue
            if actual.get("http_status") != 202 or delivery_id is None:
                return False
            delivery = delivery_by_id.get(int(delivery_id))
            if (
                delivery is None
                or delivery.mode != outcome.delivery_mode
                or delivery.status != outcome.final_delivery_status
            ):
                return False
            accepted_ids.append(delivery.id)
        if set(accepted_ids) != set(delivery_by_id):
            return False
        accepted_deliveries = [delivery_by_id[item] for item in accepted_ids]
        return all(
            left.message_sequence_no < right.message_sequence_no
            for left, right in zip(
                accepted_deliveries, accepted_deliveries[1:], strict=False
            )
        )

    @staticmethod
    def _infrastructure_failure(tasks: list[TaskRun], events: list[TaskEvent]) -> str | None:
        transport = {
            "provider_connection_error",
            "provider_rate_limited",
            "provider_server_error",
            "provider_timeout",
            "provider_4xx",
            "provider_authentication_failed",
            "transport_error",
        }
        for event in events:
            code = str(event.payload_jsonb.get("error_code") or "")
            if code in transport:
                return f"provider_transport:{code}"
        for task in tasks:
            if task.error_code in transport:
                return f"provider_transport:{task.error_code}"
        return None

    def _goal_status(self, goal_id: int) -> str:
        with self.session_factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            if goal is None:
                raise InteractiveExecutorError("interactive_goal_missing")
            return goal.status

    def _goal_baseline_revision(self, goal_id: int) -> int:
        with self.session_factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            if goal is None:
                raise InteractiveExecutorError("interactive_goal_missing")
            return goal.baseline_draft_revision

    def _draft_revision(self, draft_id: int) -> int:
        from casefile.data_postgres.models import Draft  # noqa: PLC0415

        with self.session_factory() as session:
            revision = session.scalar(select(Draft.revision).where(Draft.id == draft_id))
        if revision is None:
            raise InteractiveExecutorError("interactive_draft_missing")
        return int(revision)

    def _latest_goal_patch_id(self, goal_id: int) -> int | None:
        with self.session_factory() as session:
            value = session.scalar(
                select(AgentPatchSet.id)
                .join(
                    AgentGoalTaskRun,
                    AgentGoalTaskRun.task_run_id == AgentPatchSet.task_run_id,
                )
                .where(AgentGoalTaskRun.goal_session_id == goal_id)
                .order_by(AgentPatchSet.id.desc())
                .limit(1)
            )
        return int(value) if value is not None else None

    def _worker_config(self, actor_id: int) -> WorkerConfig:
        return WorkerConfig(
            worker_id=f"m38-interactive-{actor_id}",
            general_mutation_mode="suggest",
            general_mutation_create_enabled=True,
            general_mutation_delete_enabled=True,
            closure_repair_mode="suggest",
        )


__all__ = ["InteractiveExecutorError", "PostgresInteractiveGoalExecutor"]
