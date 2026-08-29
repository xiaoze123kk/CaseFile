"""Worker handler that composes the CaseFile Chat execution adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from casefile.agent_runtime.chat_execution import (
    ChatExecutionRunner,
    prepare_chat_request_artifacts,
)
from casefile.agent_runtime.chat_intent import (
    general_mutation_abstention_reason,
    route_public_payload,
)
from casefile.agent_runtime.chat_routing import routing_policy
from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
    GoalDecisionOutput,
    GoalExecutionCheckpoint,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.goal.execution import (
    GoalCapabilityResult,
    GoalCheckpointResult,
    GoalExecutionError,
    GoalExecutionRunner,
)
from casefile.agent_runtime.goal.filter import goal_candidate_filter
from casefile.agent_runtime.goal.policy import (
    GoalRuntimeConfig,
    apply_goal_amendment,
    freeze_goal,
    goal_capability_message,
    qualify_goal,
    stable_hash,
)
from casefile.agent_runtime.goal.provider import (
    GoalAmendmentRequest,
    GoalDecisionRequest,
    GoalDecisionResult,
    GoalFinalizerRequest,
    GoalUnderstandingRequest,
    GoalUnderstandingResult,
)
from casefile.agent_runtime.models import (
    CaseFileChatCandidateV2,
    CaseFileChatResult,
    ChatTaskUnderstanding,
    QueryRewriteResult,
    RouteDecision,
    ToolMetrics,
)
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.chat import ChatTaskExecutor, resolve_chat_route
from casefile.worker.failures import TaskCancellationRequested


class _RecoveringGoalProvider:
    def __init__(self, provider: Any, chat: ChatTaskExecutor, task_run_id: int) -> None:
        self.provider = provider
        self.chat = chat
        self.task_run_id = task_run_id

    def decide_goal(self, request: GoalDecisionRequest) -> GoalDecisionResult:
        upstream = {
            "obligations": request.goal.obligations_hash,
            **{
                f"observation_{index}": item.output_hash
                for index, item in enumerate(request.observations, start=1)
            },
        }
        input_hash = stable_hash(
            {
                "goal": request.goal.obligations_hash,
                "observations": [item.output_hash for item in request.observations],
                "completion_feedback": (
                    None
                    if request.completion_feedback is None
                    else request.completion_feedback.state_hash
                ),
            }
        )
        reusable = self.chat._load_reusable_goal_step(
            self.task_run_id, "goal_controller", input_hash, upstream
        )
        if reusable is not None:
            candidate = GoalDecisionOutput.model_validate(reusable["output"])
            if stable_hash(candidate.model_dump(mode="json")) == reusable["output_hash"]:
                return GoalDecisionResult(
                    candidate=candidate,
                    usage={},
                    reused_from_step_run_id=reusable["step_run_id"],
                )
        return cast(GoalDecisionResult, self.provider.decide_goal(request))

    def finalize_goal(self, request: GoalFinalizerRequest) -> CaseFileChatResult:
        input_hash = stable_hash(
            {
                "goal": request.goal.obligations_hash,
                "completion": request.completion.state_hash,
                "mutation_proof": request.mutation_proof,
            }
        )
        upstream = {
            "completion": request.completion.state_hash,
            "obligations": request.goal.obligations_hash,
        }
        reusable = self.chat._load_reusable_goal_step(
            self.task_run_id, "goal_finalizer", input_hash, upstream
        )
        if reusable is not None:
            candidate = CaseFileChatCandidateV2.model_validate(reusable["output"])
            if stable_hash(candidate.model_dump(mode="json")) == reusable["output_hash"]:
                return CaseFileChatResult(
                    candidate=candidate,
                    usage={},
                    reused_from_step_run_id=reusable["step_run_id"],
                )
        return cast(CaseFileChatResult, self.provider.finalize_goal(request))


class ChatHandler:
    task_types = frozenset({"casefile_chat"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, chat: ChatTaskExecutor, complete_chat: Callable[..., None]) -> None:
        self._chat = chat
        self._complete_chat = complete_chat

    def execute(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        request = self._chat._load_chat_request(task, api_key)
        if self._try_goal(context, request, provider, api_key):
            return
        self._execute_single(context, request, provider, api_key)

    def _execute_single(
        self,
        context: TaskExecutionContext,
        request: Any,
        provider: Any,
        api_key: str,
    ) -> None:
        task = context.task
        previous_routing = self._chat._load_previous_chat_routing(task.id)
        request = resolve_chat_route(
            request,
            budget=task.budget_jsonb,
            provider=provider,
            previous=previous_routing,
            allow_general_mutation_create=(
                context.chat_config.general_mutation_mode != "off"
                and context.chat_config.general_mutation_create_enabled
            ),
            allow_general_mutation_delete=(
                context.chat_config.general_mutation_mode != "off"
                and context.chat_config.general_mutation_delete_enabled
            ),
            allow_general_mutation_update=(context.chat_config.general_mutation_mode != "off"),
        )
        request = prepare_chat_request_artifacts(
            request,
            general_mutation_authoritative=(context.chat_config.general_mutation_mode != "off"),
        )
        if request.route is not None:
            if previous_routing is None:
                self._chat._emit_chat_routing_events(task.id, request)
                if request.route.route_source == "fallback":
                    context.emit(
                        task.id,
                        "router.fallback",
                        "routing",
                        route_public_payload(request.route),
                    )
            if (
                request.task_understanding is not None
                and request.task_understanding.primary_intent == "logic_audit"
            ):
                verification_trigger = str(task.input_jsonb.get("verification_trigger", "chat"))
                context.emit(
                    task.id,
                    "verification.started",
                    "verification",
                    {
                        "trigger": verification_trigger,
                        "profile": "balanced",
                        "draft_revision": task.input_draft_revision,
                        "input_hash": task.input_hash,
                    },
                )
        request = self._chat._emit_chat_context_events(task.id, task, request)

        def complete_chat(result: Any) -> None:
            general_mutation_envelope, repair_envelope, repair_usage = (
                self._chat._resolve_mutation_and_repair(
                    task,
                    request,
                    result,
                    provider,
                    api_key,
                )
            )
            self._complete_chat(
                task.id,
                context.attempt_id,
                result,
                route=request.route,
                repair_envelope=repair_envelope,
                repair_usage=repair_usage,
                general_mutation_envelope=general_mutation_envelope,
            )

        execution = ChatExecutionRunner(provider).run(
            request,
            complete=complete_chat,
            artifacts_prepared=True,
        )
        result = execution.result
        context.state.candidate = result.candidate.model_dump(mode="json")
        context.state.usage = execution.usage
        self._chat._maybe_compact_chat_thread(
            task,
            provider,
            api_key,
            model_requested_compaction=(
                int(getattr(result.tools, "requested_thread_compaction", 0)) > 0
            ),
        )

    def _try_goal(
        self,
        context: TaskExecutionContext,
        request: Any,
        provider: Any,
        api_key: str,
    ) -> bool:
        task = context.task
        raw_runtime = task.input_jsonb.get("goal_runtime")
        if not isinstance(raw_runtime, dict):
            return False
        runtime = GoalRuntimeConfig.model_validate(raw_runtime)
        checkpoint: GoalExecutionCheckpoint | None = None
        raw_checkpoint = task.input_jsonb.get("goal_checkpoint")
        raw_frozen_goal = task.input_jsonb.get("frozen_goal")
        raw_pending_delivery = task.input_jsonb.get("pending_goal_delivery")
        if isinstance(raw_pending_delivery, dict):
            if runtime.mode != "active" or not isinstance(raw_frozen_goal, dict):
                raise GoalExecutionError("goal_amendment_invalid")
            previous_goal = FrozenGoal.model_validate(raw_frozen_goal)
            amendment = provider.amend_goal(
                GoalAmendmentRequest(chat=request, current_goal=previous_goal)
            )
            frozen = apply_goal_amendment(
                previous_goal,
                amendment.candidate,
                request.message,
                budget=runtime.budget,
            )
            self._chat._initialize_waiting_goal_amendment(
                task.id,
                delivery_id=int(raw_pending_delivery["delivery_id"]),
                amended_goal=frozen,
                amendment_kind=amendment.candidate.amendment_kind,
            )
            checkpoint = GoalExecutionCheckpoint(
                obligations_hash=frozen.obligations_hash
            )
            context.emit(
                task.id,
                "goal.amended",
                "goal",
                {
                    "amendment_kind": amendment.candidate.amendment_kind,
                    "obligation_count": len(frozen.obligations),
                },
            )
        elif isinstance(raw_checkpoint, dict):
            if runtime.mode != "active" or not isinstance(raw_frozen_goal, dict):
                raise GoalExecutionError("goal_checkpoint_invalid")
            checkpoint = GoalExecutionCheckpoint.model_validate(raw_checkpoint)
            frozen = FrozenGoal.model_validate(raw_frozen_goal)
            context.emit(
                task.id,
                "goal.resumed",
                "goal",
                {
                    "checkpoint_version": checkpoint.version,
                    "observation_count": len(checkpoint.observations),
                },
            )
        else:
            entrypoint = str(request.routing_hint.get("entrypoint") or "free_text")
            candidate = goal_candidate_filter(request.message, routing_entrypoint=entrypoint)
            if not candidate.candidate:
                return False
            context.emit(
                task.id,
                "agent.step.started",
                "goal_understanding",
                {
                    "component_id": "goal_interpreter",
                    "component_version": task.prompt_version,
                    "schema_id": "casefile-chat-goal-understanding-v1",
                    "input_hash": task.input_hash,
                },
            )
            try:
                if self._chat._goal_cancelled(task.id):
                    raise TaskCancellationRequested
                reusable = self._chat._load_reusable_goal_step(
                    task.id, "goal_interpreter", task.input_hash, {}
                )
                if reusable is not None:
                    reused_output = GoalUnderstandingOutput.model_validate(reusable["output"])
                    if (
                        stable_hash(reused_output.model_dump(mode="json"))
                        != reusable["output_hash"]
                    ):
                        reusable = None
                if reusable is not None:
                    interpreted = GoalUnderstandingResult(candidate=reused_output, usage={})
                    context.emit(
                        task.id,
                        "agent.step.reused",
                        "goal_understanding",
                        {
                            "component_id": "goal_interpreter",
                            "schema_id": "casefile-chat-goal-understanding-v1",
                            "output_hash": reusable["output_hash"],
                            "resumed_from_step_run_id": reusable["step_run_id"],
                            "_artifact": reused_output.model_dump(mode="json"),
                        },
                    )
                else:
                    interpreted = provider.understand_goal(GoalUnderstandingRequest(chat=request))
                frozen = freeze_goal(interpreted.candidate, request.message)
                qualification = qualify_goal(
                    interpreted.candidate,
                    frozen,
                    budget=runtime.budget,
                )
            except TaskCancellationRequested:
                raise
            except Exception as error:
                context.emit(
                    task.id,
                    "agent.step.failed",
                    "goal_understanding",
                    {
                        "component_id": "goal_interpreter",
                        "schema_id": "casefile-chat-goal-understanding-v1",
                        "error_code": "goal_interpreter_failed",
                        "failure_layer": "qualification",
                        "issues": [{"code": type(error).__name__}],
                        "recoverable": True,
                    },
                )
                context.emit(
                    task.id,
                    "goal.qualification_failed",
                    "routing",
                    {
                        "reason_code": "goal_interpreter_failed",
                        "error_class": type(error).__name__,
                    },
                )
                return False
            if reusable is None:
                artifact = interpreted.candidate.model_dump(mode="json")
                context.emit(
                    task.id,
                    "agent.step.completed",
                    "goal_understanding",
                    {
                        "component_id": "goal_interpreter",
                        "component_version": task.prompt_version,
                        "schema_id": "casefile-chat-goal-understanding-v1",
                        "input_hash": task.input_hash,
                        "output_hash": stable_hash(artifact),
                        "usage": interpreted.usage,
                        "_artifact": artifact,
                    },
                )
            if not qualification.qualified:
                context.emit(
                    task.id,
                    "goal.qualification_failed",
                    "routing",
                    {
                        "reason_codes": list(qualification.reason_codes),
                        "obligation_count": len(frozen.obligations),
                    },
                )
                if (
                    runtime.mode == "active"
                    and isinstance(task.input_jsonb.get("goal_session"), dict)
                    and (
                        "goal_ambiguous" in qualification.reason_codes
                        or "goal_missing_info" in qualification.reason_codes
                    )
                ):
                    self._chat._initialize_goal_task(task.id, frozen)
                    self._chat._pause_goal_for_clarification(
                        task.id,
                        context.attempt_id,
                        missing_info=list(interpreted.candidate.missing_info),
                        usage=interpreted.usage,
                    )
                    context.state.candidate = {"waiting_clarification": True}
                    context.state.usage = interpreted.usage
                    return True
                return False
            if (
                any(item.kind == "mutation_proposal" for item in frozen.obligations)
                and context.chat_config.general_mutation_mode != "suggest"
            ):
                context.emit(
                    task.id,
                    "goal.qualification_failed",
                    "routing",
                    {
                        "reason_codes": ["goal_mutation_effect_unavailable"],
                        "obligation_count": len(frozen.obligations),
                    },
                )
                return False
            mutation_preflight_reason = self._mutation_preflight_reason(
                request,
                frozen,
                budget=task.budget_jsonb,
            )
            if mutation_preflight_reason is not None:
                context.emit(
                    task.id,
                    "goal.qualification_failed",
                    "routing",
                    {
                        "reason_codes": [mutation_preflight_reason],
                        "obligation_count": len(frozen.obligations),
                    },
                )
                return False
            if runtime.mode == "shadow":
                context.emit(
                    task.id,
                    "goal.shadow_evaluated",
                    "routing",
                    {
                        "qualified": True,
                        "obligation_count": len(frozen.obligations),
                        "has_mutation": any(
                            item.kind == "mutation_proposal" for item in frozen.obligations
                        ),
                    },
                )
                return False
            if isinstance(task.input_jsonb.get("goal_session"), dict):
                self._chat._initialize_goal_task(task.id, frozen)
            context.emit(
                task.id,
                "goal.started",
                "goal",
                {
                    "runtime_version": runtime.runtime_version,
                    "policy_version": runtime.policy_version,
                    "capability_registry_version": runtime.capability_registry_version,
                    "obligation_count": len(frozen.obligations),
                },
            )
        candidate_document: dict[str, Any] | None = None
        candidate_state_hash: str | None = None
        mutation_envelope: dict[str, Any] | None = None
        repair_envelope: dict[str, Any] | None = None

        def execute_capability(
            action: InvokeCapabilityAction,
            action_no: int,
        ) -> GoalCapabilityResult:
            nonlocal candidate_document, candidate_state_hash
            nonlocal mutation_envelope, repair_envelope
            if self._chat._goal_cancelled(task.id):
                raise TaskCancellationRequested
            context.emit(
                task.id,
                "goal.capability_started",
                "goal",
                {
                    "action_no": action_no,
                    "capability": action.capability,
                    "target_state": action.target_state,
                },
            )
            capability_component = f"goal_capability_{action_no}"
            capability_input_hash = stable_hash(
                [task.input_hash, action.model_dump(mode="json"), action_no]
            )
            context.emit(
                task.id,
                "agent.step.started",
                "goal_capability",
                {
                    "component_id": capability_component,
                    "parent_component_id": "goal_controller",
                    "component_version": runtime.capability_registry_version,
                    "schema_id": "casefile-chat-goal-observation-v1",
                    "input_hash": capability_input_hash,
                    "upstream_hashes": {"obligations": frozen.obligations_hash},
                },
            )
            capability_request = self._capability_request(
                request,
                frozen,
                action,
                candidate_document=candidate_document,
                budget=task.budget_jsonb,
            )
            reusable_capability = (
                None
                if action.capability == "propose_mutation"
                else self._chat._load_reusable_goal_step(
                    task.id,
                    capability_component,
                    capability_input_hash,
                    {"obligations": frozen.obligations_hash},
                )
            )
            if reusable_capability is not None:
                artifact = reusable_capability["output"]
                expected_candidate_hash = (
                    None if action.target_state == "baseline" else candidate_state_hash
                )
                if (
                    artifact.get("capability") == action.capability
                    and artifact.get("obligation_ids") == action.obligation_ids
                    and artifact.get("target_state") == action.target_state
                    and artifact.get("candidate_hash") == expected_candidate_hash
                    and artifact.get("output_hash") == reusable_capability["output_hash"]
                ):
                    reused_result = GoalCapabilityResult(
                        summary=str(artifact["summary"]),
                        object_refs=tuple(artifact.get("object_refs") or ()),
                        evidence_refs=tuple(artifact.get("evidence_refs") or ()),
                        input_hash=capability_input_hash,
                        output_hash=str(artifact["output_hash"]),
                        route_hash=artifact.get("route_hash"),
                        ledger_hash=artifact.get("ledger_hash"),
                        candidate_hash=artifact.get("candidate_hash"),
                        verification_proof_refs=tuple(
                            artifact.get("verification_proof_refs") or ()
                        ),
                        usage=dict(reusable_capability.get("usage") or {}),
                        tools=ToolMetrics(),
                        provider_operations=0,
                    )
                    context.emit(
                        task.id,
                        "agent.step.reused",
                        "goal_capability",
                        {
                            "component_id": capability_component,
                            "schema_id": "casefile-chat-goal-observation-v1",
                            "output_hash": reused_result.output_hash,
                            "resumed_from_step_run_id": reusable_capability["step_run_id"],
                            "_artifact": artifact,
                        },
                    )
                    context.emit(
                        task.id,
                        "goal.capability_completed",
                        "goal",
                        {
                            "action_no": action_no,
                            "capability": action.capability,
                            "target_state": action.target_state,
                            "reused": True,
                        },
                    )
                    return reused_result
            if action.capability == "propose_mutation":
                envelope, repair, usage = self._chat._execute_goal_mutation(
                    task,
                    capability_request,
                    provider,
                    api_key,
                )
                if envelope is None or envelope.get("status") != "ready":
                    raise GoalExecutionError("goal_capability_blocked")
                simulation = envelope["simulation"]
                raw_candidate = simulation.document
                candidate_document = (
                    raw_candidate.model_dump(mode="json")
                    if hasattr(raw_candidate, "model_dump")
                    else dict(raw_candidate)
                )
                candidate_state_hash = simulation.candidate_hash
                mutation_envelope = envelope
                repair_envelope = repair
                proof = {
                    "status": "ready",
                    "plan_hash": envelope["bound"].plan_hash,
                    "candidate_hash": simulation.candidate_hash,
                    "impact_hash": envelope["impact_hash"],
                    "can_apply": simulation.can_apply,
                }
                result = GoalCapabilityResult(
                    summary="已形成经过绑定、模拟和闭合检查的待审阅修改建议。",
                    input_hash=stable_hash([task.input_hash, action.model_dump(mode="json")]),
                    output_hash=envelope["bound"].plan_hash,
                    candidate_hash=simulation.candidate_hash,
                    mutation_proof_ref=f"general_mutation:{envelope['bound'].plan_hash}",
                    verification_proof_refs=(f"simulation:{envelope['impact_hash']}",),
                    mutation_proof=proof,
                    usage=usage,
                    provider_operations=2 if repair is not None else 1,
                )
            else:
                context.emit(
                    task.id,
                    "agent.model_call.started",
                    "goal_capability",
                    {
                        "component_id": capability_component,
                        "schema_id": "casefile-chat-evidence-v1",
                        "attempt_no": 1,
                        "protocol": "provider_evidence_agent",
                        "model_id": request.model_id,
                        "input_hash": capability_input_hash,
                    },
                )
                evidence = provider.collect_chat_evidence(capability_request)
                ledger = evidence.ledger or {}
                ledger_hash = str(ledger.get("ledger_hash") or "") or None
                evidence_output_hash = ledger_hash or stable_hash(evidence.evidence_summary)
                context.emit(
                    task.id,
                    "agent.model_call.completed",
                    "goal_capability",
                    {
                        "component_id": capability_component,
                        "schema_id": "casefile-chat-evidence-v1",
                        "attempt_no": 1,
                        "protocol": "provider_evidence_agent",
                        "output_hash": evidence_output_hash,
                        "output_size_bytes": len(evidence.evidence_summary.encode("utf-8")),
                        "usage": evidence.usage,
                    },
                )
                result = GoalCapabilityResult(
                    summary=evidence.evidence_summary or "已完成冻结卷宗证据核对。",
                    object_refs=_goal_ledger_refs(ledger, "retrieved_object_ids"),
                    evidence_refs=_goal_ledger_refs(ledger, "retrieved_evidence_ids"),
                    input_hash=stable_hash(
                        [task.input_hash, action.model_dump(mode="json"), action_no]
                    ),
                    output_hash=evidence_output_hash,
                    route_hash=capability_request.route.route_hash,
                    ledger_hash=ledger_hash,
                    candidate_hash=(
                        None
                        if action.target_state == "baseline"
                        else candidate_state_hash
                    ),
                    usage=evidence.usage,
                    tools=evidence.tools,
                    provider_operations=1,
                )
            context.emit(
                task.id,
                "agent.step.completed",
                "goal_capability",
                {
                    "component_id": capability_component,
                    "schema_id": "casefile-chat-goal-observation-v1",
                    "output_hash": result.output_hash,
                    "usage": result.usage,
                    "_artifact": {
                        "capability": action.capability,
                        "obligation_ids": action.obligation_ids,
                        "target_state": action.target_state,
                        "summary": result.summary,
                        "object_refs": list(result.object_refs),
                        "evidence_refs": list(result.evidence_refs),
                        "output_hash": result.output_hash,
                        "route_hash": result.route_hash,
                        "ledger_hash": result.ledger_hash,
                        "candidate_hash": result.candidate_hash,
                        "mutation_proof_ref": result.mutation_proof_ref,
                        "verification_proof_refs": list(result.verification_proof_refs),
                    },
                },
            )
            context.emit(
                task.id,
                "goal.capability_completed",
                "goal",
                {
                    "action_no": action_no,
                    "capability": action.capability,
                    "target_state": action.target_state,
                    "output_hash": result.output_hash,
                },
            )
            return result

        def cancelled() -> bool:
            if self._chat._goal_cancelled(task.id):
                raise TaskCancellationRequested
            return False

        def should_interrupt(_safe_point: str) -> bool:
            # M3.8-04 will materialize mutation observations with PatchSet
            # identity. Until then a Planner/Binder result stays in its slice.
            return mutation_envelope is None and self._chat._goal_control_pending(task.id)

        try:
            execution = GoalExecutionRunner(
                _RecoveringGoalProvider(provider, self._chat, task.id)
            ).run(
                request,
                frozen,
                budget=runtime.budget,
                execute_capability=execute_capability,
                is_cancelled=cancelled,
                should_interrupt=should_interrupt,
                checkpoint=checkpoint,
                initial_provider_operations=0 if checkpoint is not None else 1,
            )
        except GoalExecutionError as error:
            context.emit(
                task.id,
                "goal.failed",
                "goal",
                {"reason_code": error.code},
            )
            raise
        if isinstance(execution, GoalCheckpointResult):
            control = self._chat._next_goal_control(task.id)
            if control is None:
                raise GoalExecutionError("goal_delivery_missing")
            control_request = replace(
                request,
                message=str(control["message"]),
                input_hash=stable_hash(
                    [request.input_hash, control["delivery_id"], control["message"]]
                ),
            )
            if control["mode"] == "replace":
                replacement = provider.understand_goal(
                    GoalUnderstandingRequest(chat=control_request)
                )
                replacement_goal = freeze_goal(
                    replacement.candidate,
                    control_request.message,
                )
                replacement_qualification = qualify_goal(
                    replacement.candidate,
                    replacement_goal,
                    budget=runtime.budget,
                )
                if not replacement_qualification.qualified:
                    raise GoalExecutionError("goal_replacement_invalid")
                continuation_run_id = self._chat._replace_goal_task(
                    task.id,
                    context.attempt_id,
                    frozen_goal=frozen,
                    checkpoint=execution.checkpoint,
                    delivery_id=int(control["delivery_id"]),
                    replacement_goal=replacement_goal,
                    safe_point=execution.safe_point,
                    usage=execution.usage,
                    tools=execution.tools.as_dict(),
                )
                context.state.candidate = {
                    "checkpointed": True,
                    "replaced": True,
                    "continuation_run_id": continuation_run_id,
                }
                context.state.usage = execution.usage
                return True
            if control["mode"] != "steer":
                raise GoalExecutionError("goal_delivery_invalid")
            amendment = provider.amend_goal(
                GoalAmendmentRequest(chat=control_request, current_goal=frozen)
            )
            amended_goal = apply_goal_amendment(
                frozen,
                amendment.candidate,
                control_request.message,
                budget=runtime.budget,
            )
            continuation_run_id = self._chat._checkpoint_goal_task(
                task.id,
                context.attempt_id,
                frozen_goal=frozen,
                checkpoint=execution.checkpoint,
                safe_point=execution.safe_point,
                usage=execution.usage,
                tools=execution.tools.as_dict(),
                delivery_id=int(control["delivery_id"]),
                amended_goal=amended_goal,
                amendment_kind=amendment.candidate.amendment_kind,
            )
            context.state.candidate = {
                "checkpointed": True,
                "continuation_run_id": continuation_run_id,
            }
            context.state.usage = execution.usage
            return True
        context.emit(
            task.id,
            "goal.completed",
            "goal",
            {
                "state_hash": execution.completion.state_hash,
                "observation_count": len(execution.observations),
                "decision_calls": execution.decision_calls,
            },
        )
        self._complete_chat(
            task.id,
            context.attempt_id,
            execution.result,
            route=self._goal_completion_route(frozen, budget=task.budget_jsonb),
            repair_envelope=repair_envelope,
            repair_usage=None,
            general_mutation_envelope=mutation_envelope,
            frozen_goal=frozen,
            goal_checkpoint=GoalExecutionCheckpoint(
                obligations_hash=frozen.obligations_hash,
                observations=list(execution.observations),
                completion=execution.completion,
                mutation_proof=execution.mutation_proof,
            ),
        )
        context.state.candidate = execution.result.candidate.model_dump(mode="json")
        context.state.usage = execution.usage
        self._chat._maybe_compact_chat_thread(
            task,
            provider,
            api_key,
            model_requested_compaction=(
                int(getattr(execution.tools, "requested_thread_compaction", 0)) > 0
            ),
        )
        return True

    @staticmethod
    def _goal_completion_route(
        frozen: FrozenGoal,
        *,
        budget: dict[str, Any],
    ) -> RouteDecision:
        kinds = {item.kind for item in frozen.obligations}
        primary_intent = (
            "edit_request"
            if "mutation_proposal" in kinds
            else "logic_audit"
            if "audit" in kinds
            else "analysis"
        )
        understanding = ChatTaskUnderstanding(
            primary_intent=primary_intent,
            sub_intents=("goal:completed",),
            complexity="high",
            multi_step=True,
            confidence=1.0,
            reason_codes=("goal_completion",),
        )
        return routing_policy(understanding, budget=budget)

    @staticmethod
    def _mutation_preflight_reason(
        request: Any,
        frozen: FrozenGoal,
        *,
        budget: dict[str, Any],
    ) -> str | None:
        for obligation in frozen.obligations:
            if obligation.kind != "mutation_proposal":
                continue
            action = InvokeCapabilityAction(
                capability="propose_mutation",
                obligation_ids=[obligation.obligation_id],
                target_state=obligation.target_state,
            )
            capability_request = ChatHandler._capability_request(
                request,
                frozen,
                action,
                candidate_document=None,
                budget=budget,
            )
            reason = general_mutation_abstention_reason(capability_request)
            if reason is not None:
                return reason
        return None

    @staticmethod
    def _capability_request(
        request: Any,
        frozen: FrozenGoal,
        action: InvokeCapabilityAction,
        *,
        candidate_document: dict[str, Any] | None,
        budget: dict[str, Any],
    ) -> Any:
        if action.target_state == "candidate" and candidate_document is None:
            raise GoalExecutionError("goal_capability_blocked")
        primary_intent = {
            "analyze": "analysis",
            "audit": "logic_audit",
            "propose_mutation": "edit_request",
        }[action.capability]
        understanding = ChatTaskUnderstanding(
            primary_intent=primary_intent,
            sub_intents=(f"goal:{action.capability}",),
            complexity="high",
            multi_step=False,
            confidence=1.0,
            reason_codes=("goal_capability",),
        )
        route = routing_policy(understanding, budget=budget)
        scoped_message = goal_capability_message(frozen, action)
        rewritten = QueryRewriteResult(
            original_query=scoped_message,
            normalized_query=scoped_message,
            canonical_query=scoped_message,
        )
        return prepare_chat_request_artifacts(
            replace(
                request,
                message=scoped_message,
                casefile=(
                    request.casefile if action.target_state == "baseline" else candidate_document
                ),
                task_understanding=understanding,
                route=route,
                rewrite=rewritten,
                assembled_input=None,
                frozen_tool_ledger=None,
                safe_patch_registry=None,
                previous_candidate=None,
                repair_plan=None,
                target_locked_repair=None,
            ),
            general_mutation_authoritative=True,
        )


def _goal_ledger_refs(ledger: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = ledger.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(
        dict.fromkeys(
            value.strip() for value in raw if isinstance(value, str) and value.strip()
        )
    )[:50]


__all__ = ["ChatHandler"]
