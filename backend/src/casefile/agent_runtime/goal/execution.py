"""Persistence-free bounded Goal execution loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from casefile.agent_runtime.chat_execution import coordinate_chat_candidate_validation
from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
    GoalCompletionDecision,
    GoalObservation,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.goal.policy import (
    GoalBudget,
    GoalPolicyError,
    complete_goal,
    stable_hash,
    validate_decision,
)
from casefile.agent_runtime.goal.provider import (
    GoalDecisionRequest,
    GoalFinalizerRequest,
)
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult, ToolMetrics


class GoalExecutionError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.error_code = code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class GoalCapabilityResult:
    summary: str
    object_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    input_hash: str = ""
    output_hash: str = ""
    route_hash: str | None = None
    ledger_hash: str | None = None
    candidate_hash: str | None = None
    mutation_proof_ref: str | None = None
    verification_proof_refs: tuple[str, ...] = ()
    mutation_proof: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tools: ToolMetrics = field(default_factory=ToolMetrics)
    provider_operations: int = 1


class GoalProvider(Protocol):
    def decide_goal(self, request: GoalDecisionRequest) -> Any: ...

    def finalize_goal(self, request: GoalFinalizerRequest) -> CaseFileChatResult: ...


GoalCapabilityExecutor = Callable[[InvokeCapabilityAction, int], GoalCapabilityResult]
CancellationProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class GoalExecutionResult:
    result: CaseFileChatResult
    observations: tuple[GoalObservation, ...]
    completion: GoalCompletionDecision
    mutation_proof: dict[str, Any] | None
    usage: dict[str, Any]
    tools: ToolMetrics
    decision_calls: int


class GoalExecutionRunner:
    def __init__(self, provider: GoalProvider) -> None:
        self.provider = provider

    def run(
        self,
        request: CaseFileChatRequest,
        goal: FrozenGoal,
        *,
        budget: GoalBudget,
        execute_capability: GoalCapabilityExecutor,
        is_cancelled: CancellationProbe = lambda: False,
    ) -> GoalExecutionResult:
        observations: list[GoalObservation] = []
        usage_records: list[dict[str, Any]] = []
        tools = ToolMetrics()
        completion_feedback: GoalCompletionDecision | None = None
        completion_retries = 0
        decision_calls = 0
        provider_operations = 0
        total_observation_chars = 0
        seen_actions: set[str] = set()
        mutation_proof: dict[str, Any] | None = None

        while True:
            if is_cancelled():
                raise GoalExecutionError("cancelled")
            if decision_calls >= budget.max_decision_calls:
                raise GoalExecutionError("goal_budget_exhausted")
            if provider_operations >= budget.max_provider_operations:
                raise GoalExecutionError("goal_budget_exhausted")
            decision_calls += 1
            provider_operations += 1
            decision_input_hash = stable_hash(
                {
                    "goal": goal.obligations_hash,
                    "observations": [item.output_hash for item in observations],
                    "completion_feedback": (
                        None if completion_feedback is None else completion_feedback.state_hash
                    ),
                }
            )
            request.emit(
                "agent.step.started",
                "goal_deciding",
                {
                    "component_id": "goal_controller",
                    "parent_component_id": None,
                    "component_version": request.prompt_version,
                    "schema_id": "casefile-chat-goal-decision-v1",
                    "input_hash": decision_input_hash,
                    "upstream_hashes": {
                        "obligations": goal.obligations_hash,
                        **{
                            f"observation_{index}": item.output_hash
                            for index, item in enumerate(observations, start=1)
                        },
                    },
                },
            )
            decided = self.provider.decide_goal(
                GoalDecisionRequest(
                    chat=request,
                    goal=goal,
                    observations=tuple(observations),
                    budget=budget,
                    completion_feedback=completion_feedback,
                )
            )
            usage_records.append(dict(decided.usage))
            decision_output = decided.candidate.model_dump(mode="json")
            decision_event = (
                "agent.step.reused"
                if decided.reused_from_step_run_id is not None
                else "agent.step.completed"
            )
            request.emit(
                decision_event,
                "goal_deciding",
                {
                    "component_id": "goal_controller",
                    "schema_id": "casefile-chat-goal-decision-v1",
                    "output_hash": stable_hash(decision_output),
                    "usage": decided.usage,
                    "_artifact": decision_output,
                    **(
                        {"resumed_from_step_run_id": decided.reused_from_step_run_id}
                        if decided.reused_from_step_run_id is not None
                        else {}
                    ),
                },
            )
            try:
                validate_decision(goal, decided.candidate, tuple(observations))
            except GoalPolicyError as error:
                raise GoalExecutionError(error.code) from error
            action = decided.candidate.action
            if action.action == "finish":
                completion = complete_goal(
                    goal,
                    tuple(observations),
                    expected_obligations_hash=goal.obligations_hash,
                )
                if completion.allowed:
                    break
                if completion_retries >= budget.max_completion_retries:
                    raise GoalExecutionError(
                        "goal_completion_blocked",
                        details={
                            "missing_obligation_ids": completion.missing_obligation_ids,
                            "reason_codes": completion.reason_codes,
                        },
                    )
                completion_retries += 1
                completion_feedback = completion
                continue
            if len(observations) >= budget.max_capability_actions:
                raise GoalExecutionError("goal_budget_exhausted")
            action_hash = stable_hash(action.model_dump(mode="json"))
            if action_hash in seen_actions:
                raise GoalExecutionError("goal_no_progress")
            seen_actions.add(action_hash)
            if is_cancelled():
                raise GoalExecutionError("cancelled")
            capability = execute_capability(action, len(observations) + 1)
            provider_operations += capability.provider_operations
            if provider_operations > budget.max_provider_operations:
                raise GoalExecutionError("goal_budget_exhausted")
            if capability.tools.calls + tools.calls > budget.max_total_tool_calls:
                raise GoalExecutionError("goal_budget_exhausted")
            if len(capability.summary) > budget.max_observation_chars:
                raise GoalExecutionError("goal_capability_blocked")
            total_observation_chars += len(capability.summary)
            if total_observation_chars > budget.max_total_observation_chars:
                raise GoalExecutionError("goal_budget_exhausted")
            observation = GoalObservation(
                observation_id=f"obs_{len(observations) + 1}",
                capability=action.capability,
                obligation_ids=action.obligation_ids,
                target_state=action.target_state,
                status="completed",
                summary=capability.summary,
                object_refs=list(capability.object_refs),
                evidence_refs=list(capability.evidence_refs),
                action_hash=action_hash,
                input_hash=capability.input_hash or stable_hash([request.input_hash, action_hash]),
                output_hash=capability.output_hash or stable_hash(capability.summary),
                route_hash=capability.route_hash,
                ledger_hash=capability.ledger_hash,
                candidate_hash=capability.candidate_hash,
                mutation_proof_ref=capability.mutation_proof_ref,
                verification_proof_refs=list(capability.verification_proof_refs),
                tool_calls=capability.tools.calls,
                provider_operations=capability.provider_operations,
            )
            self._validate_observation(action, observation)
            observations.append(observation)
            usage_records.append(dict(capability.usage))
            _merge_tools(tools, capability.tools)
            if capability.mutation_proof is not None:
                if mutation_proof is not None:
                    raise GoalExecutionError("goal_capability_blocked")
                mutation_proof = capability.mutation_proof

        if provider_operations >= budget.max_provider_operations:
            raise GoalExecutionError("goal_budget_exhausted")
        provider_operations += 1
        finalizer_input_hash = stable_hash(
            {
                "goal": goal.obligations_hash,
                "completion": completion.state_hash,
                "mutation_proof": mutation_proof,
            }
        )
        request.emit(
            "agent.step.started",
            "goal_finalizing",
            {
                "component_id": "goal_finalizer",
                "component_version": request.prompt_version,
                "schema_id": "casefile-chat-output-v2",
                "input_hash": finalizer_input_hash,
                "upstream_hashes": {
                    "completion": completion.state_hash,
                    "obligations": goal.obligations_hash,
                },
            },
        )
        finalized = self.provider.finalize_goal(
            GoalFinalizerRequest(
                chat=request,
                goal=goal,
                observations=tuple(observations),
                completion=completion,
                mutation_proof=mutation_proof,
            )
        )
        usage_records.append(dict(finalized.usage))
        finalized = coordinate_chat_candidate_validation(request, finalized)
        finalizer_artifact = finalized.candidate.model_dump(mode="json")
        request.emit(
            (
                "agent.step.reused"
                if finalized.reused_from_step_run_id is not None
                else "agent.step.completed"
            ),
            "goal_finalizing",
            {
                "component_id": "goal_finalizer",
                "schema_id": "casefile-chat-output-v2",
                "output_hash": stable_hash(finalizer_artifact),
                "usage": finalized.usage,
                "_artifact": finalizer_artifact,
                **(
                    {"resumed_from_step_run_id": finalized.reused_from_step_run_id}
                    if finalized.reused_from_step_run_id is not None
                    else {}
                ),
            },
        )
        return GoalExecutionResult(
            result=finalized,
            observations=tuple(observations),
            completion=completion,
            mutation_proof=mutation_proof,
            usage=_merge_usage(usage_records),
            tools=tools,
            decision_calls=decision_calls,
        )

    @staticmethod
    def _validate_observation(
        action: InvokeCapabilityAction,
        observation: GoalObservation,
    ) -> None:
        if observation.capability != action.capability:
            raise GoalExecutionError("goal_capability_blocked")
        if observation.obligation_ids != action.obligation_ids:
            raise GoalExecutionError("goal_capability_blocked")
        if observation.target_state != action.target_state:
            raise GoalExecutionError("goal_capability_blocked")
        if action.target_state == "candidate" and observation.candidate_hash is None:
            raise GoalExecutionError("goal_capability_blocked")
        if action.capability == "propose_mutation" and (
            observation.mutation_proof_ref is None or observation.candidate_hash is None
        ):
            raise GoalExecutionError("goal_capability_blocked")


def _merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
            else:
                merged[key] = value
    return merged


def _merge_tools(target: ToolMetrics, source: ToolMetrics) -> None:
    target.calls += source.calls
    target.valid_calls += source.valid_calls
    target.successful_calls += source.successful_calls
    target.adopted_results += source.adopted_results
    target.planned_object_ids.update(source.planned_object_ids)


__all__ = [
    "GoalCapabilityResult",
    "GoalExecutionError",
    "GoalExecutionResult",
    "GoalExecutionRunner",
]
