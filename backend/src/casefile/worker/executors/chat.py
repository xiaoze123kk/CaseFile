"""Worker-side CaseFile Chat executor and context lifecycle.

Owns task/session orchestration. Pure routing, context, validation, repair, and
patch rules remain in agent_runtime.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    AgentProvider,
    CaseFileChatRequest,
    CaseFileChatResult,
)
from casefile.agent_runtime.chat_intent import (
    INTENT_ROUTER_VERSION,
    RuleRoute,
    normalize_routing_hint,
    resolve_intent_mentions,
    resolve_rule_route,
    route_public_payload,
    task_understanding_for_rule,
    task_understanding_from_output,
)
from casefile.agent_runtime.chat_routing import (
    fallback_route,
    route_llm_task,
    routing_policy,
)
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_V4_VERSION,
    CHAT_CONTEXT_POLICY_V5_VERSION,
    CHAT_CONTEXT_POLICY_V6_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V4_VERSION,
    CHAT_CONTEXT_PROMPT_V5_VERSION,
    CHAT_CONTEXT_PROMPT_V6_VERSION,
    CHAT_CONTEXT_PROMPT_V9_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
    DEFAULT_THREAD_MEMORY_COMPACTOR,
    THREAD_MEMORY_STATE_KIND,
    ContextEngineError,
    EvidenceRef,
    ThreadCompactionRequest,
    build_chat_context_manifest,
    chat_input_payload_from_assembly,
    default_compactor_registry,
    empty_thread_memory_state,
    estimate_conservative_tokens,
    preservation_errors,
    thread_memory_state_from_jsonable,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_COMPONENT_ID,
    GENERAL_MUTATION_PROMPT_VERSION,
    GENERAL_MUTATION_SCHEMA_ID,
    CreateMutationCandidate,
    DeleteMutationCandidate,
    GeneralMutationPlannerRequest,
    general_mutation_explicit_system_field_reason,
    general_mutation_explicit_unknown_object_ids,
    general_mutation_request_budget_reason,
    general_mutation_request_dependency_reason,
)
from casefile.agent_runtime.models import (
    LEGACY_CONTEXT_POLICY_VERSION,
    ChatTaskUnderstanding,
    QueryRewriteResult,
    RouteDecision,
    RouteSpecificRewriteRequest,
    agent_state_to_jsonable,
    chat_routing_payload_as_dict,
)
from casefile.agent_runtime.prompt import (
    CASEFILE_CHAT_CONTEXT_COMPACTOR_VERSION,
    render_chat_executor_prompt,
)
from casefile.agent_runtime.providers import ProviderProtocolError
from casefile.agent_runtime.query_rewrite import (
    build_llm_rewrite,
    build_rule_rewrite,
    preservation_lint,
    route_specific_rewrite_strategy,
)
from casefile.application.agent_mutation import (
    GeneralMutationBindingError,
    bind_general_mutation_plan,
    general_mutation_impact_hash,
)
from casefile.application.v1_editing import (
    editable_fields_by_collection as chat_editable_fields_by_collection,
)
from casefile.application.workflow_service import WorkflowService
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import (
    AgentMessage,
    AgentPatchOperation,
    AgentPatchSet,
    AgentThreadContextState,
    TaskEvent,
    TaskRun,
)
from casefile.domain.verification_engine import VerificationEngine
from casefile.worker.support import (
    _json_hash,
    _merge_numeric_usage,
    _network_retries,
    _required_object,
    _required_string,
)

DEFAULT_CONTEXT_HARD_INPUT_TOKENS = 128_000


def _compaction_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _context_hard_input_tokens() -> int:
    """Runtime total-input hard cap; no policy or model may relax it."""

    return max(
        1,
        _compaction_env_int(
            "CASEFILE_CHAT_CONTEXT_HARD_INPUT_TOKENS",
            DEFAULT_CONTEXT_HARD_INPUT_TOKENS,
        ),
    )


def _thread_history_tokens(messages: list[dict[str, str]]) -> int:
    text = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return estimate_conservative_tokens(text)


def _thread_state_evidence_errors(
    session: Session,
    *,
    state_id: int,
    thread_id: int,
    evidence_refs: list[str],
    verified_source_message_ids: list[int],
) -> list[str]:
    """Validate every pointer in a persisted state against the database."""

    errors: list[str] = []
    for raw in evidence_refs:
        ref = EvidenceRef.parse(raw)
        if ref is None:
            errors.append(f"state {state_id}: invalid evidence reference {raw!r}")
            continue
        try:
            if ref.scheme == "thread":
                parts = ref.identifier.split("/message/")
                if len(parts) != 2 or int(parts[0]) != thread_id:
                    raise ValueError
                message_id = int(parts[1])
                if session.get(AgentMessage, message_id) is None:
                    raise ValueError
            elif ref.scheme == "taskrun":
                if session.get(TaskRun, int(ref.identifier)) is None:
                    raise ValueError
            elif ref.scheme == "patchset":
                if session.get(AgentPatchSet, int(ref.identifier)) is None:
                    raise ValueError
            else:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"state {state_id}: unresolvable evidence reference {raw!r}")
    for message_id in verified_source_message_ids:
        if session.get(AgentMessage, message_id) is None:
            errors.append(
                f"state {state_id}: verified fact source_message_id {message_id} not found"
            )
    return errors


def _thread_db_decisions(
    session: Session,
    *,
    thread_id: int,
    from_message_seq: int,
    to_message_seq: int,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(AgentPatchOperation, AgentMessage.sequence_no)
        .join(AgentPatchSet, AgentPatchOperation.patch_set_id == AgentPatchSet.id)
        .join(AgentMessage, AgentPatchSet.source_message_id == AgentMessage.id)
        .where(
            AgentPatchSet.thread_id == thread_id,
            AgentMessage.sequence_no.between(from_message_seq, to_message_seq),
            AgentPatchOperation.decision.in_(("accepted", "rejected")),
        )
        .order_by(AgentPatchOperation.id)
    ).all()
    return [
        {
            "decision": operation.decision,
            "object_id": str(operation.target_object_id),
            "field_path": operation.field_path,
            "reason": operation.reason,
            "patch_set_id": int(operation.patch_set_id),
            "thread_ref": f"thread://{thread_id}/message/{message_sequence_no}",
        }
        for operation, message_sequence_no in rows
    ]


@dataclass(frozen=True, slots=True)
class ReusedChatRouting:
    task_understanding: ChatTaskUnderstanding
    route: RouteDecision
    rewrite: QueryRewriteResult


def _resolve_chat_route(
    request: CaseFileChatRequest,
    *,
    budget: dict[str, Any] | None = None,
    provider: AgentProvider | None = None,
    previous: ReusedChatRouting | None = None,
    allow_general_mutation_create: bool = False,
    allow_general_mutation_delete: bool = False,
    allow_general_mutation_update: bool = False,
) -> CaseFileChatRequest:
    """R2 cascade: rule → LLM intent → confidence gate → rewrite."""

    if not request.routing_hint:
        return request
    if previous is not None:
        return replace(
            request,
            task_understanding=previous.task_understanding,
            route=previous.route,
            rewrite=previous.rewrite,
        )
    rule = resolve_rule_route(
        request,
        allow_general_mutation_create=allow_general_mutation_create,
        allow_general_mutation_delete=allow_general_mutation_delete,
        allow_general_mutation_update=allow_general_mutation_update,
    )
    if rule is not None:
        understanding = task_understanding_for_rule(rule)
        route = routing_policy(
            understanding,
            budget=budget,
            profile=rule.profile,
            rewrite_strategy=_rewrite_strategy_for_rule(rule),
            route_source=rule.route_source,
        )
        return replace(
            request,
            task_understanding=understanding,
            route=route,
            rewrite=build_rule_rewrite(understanding, request.message),
        )
    if provider is None:
        return _fallback_chat_request(request, reason_codes=("router_unavailable",))
    try:
        intent_result = provider.understand_intent(request)
        understanding = task_understanding_from_output(intent_result.candidate)
        understanding = resolve_intent_mentions(understanding, request)
        rewrite = build_llm_rewrite(
            understanding,
            request.message,
            intent_result.candidate.canonical_query,
        )
        route = route_llm_task(
            understanding,
            budget=budget,
            rewrite_strategy=rewrite.rewrite_decision,
        )
        selected_strategy = route_specific_rewrite_strategy(understanding)
        if selected_strategy in {"MULTI_QUERY", "DECOMPOSE"}:
            route = replace(route, rewrite_strategy=selected_strategy)
        rewrite = _post_route_rewrite(
            request,
            provider,
            understanding,
            route,
            rewrite,
        )
        return replace(
            request,
            task_understanding=understanding,
            route=route,
            rewrite=rewrite,
        )
    except Exception as error:
        reason_code = _router_failure_reason(error)
        return _fallback_chat_request(
            request,
            reason_codes=(reason_code,),
        )


def _fallback_chat_request(
    request: CaseFileChatRequest,
    *,
    reason_codes: tuple[str, ...],
) -> CaseFileChatRequest:
    route = fallback_route(reason_codes=reason_codes)
    understanding = _fallback_task_understanding(route)
    return replace(
        request,
        task_understanding=understanding,
        route=route,
        rewrite=build_llm_rewrite(understanding, request.message, request.message),
    )


def _post_route_rewrite(
    request: CaseFileChatRequest,
    provider: AgentProvider,
    understanding: ChatTaskUnderstanding,
    route: RouteDecision,
    conservative: QueryRewriteResult,
) -> QueryRewriteResult:
    if route.rewrite_strategy not in {"MULTI_QUERY", "DECOMPOSE"}:
        return conservative
    result = provider.rewrite_for_route(
        RouteSpecificRewriteRequest(
            task_run_id=request.task_run_id,
            prompt_version=request.prompt_version,
            original_query=request.message,
            normalized_query=conservative.normalized_query,
            conservative_canonical_query=conservative.canonical_query,
            primary_intent=understanding.primary_intent,
            sub_intents=understanding.sub_intents,
            constraints=understanding.constraints,
            rewrite_strategy=route.rewrite_strategy,
            route_profile=str(route.routes[0]["profile"]),
            input_hash=request.input_hash,
            model_id=request.model_id,
            api_key=request.api_key,
            max_turns=request.max_turns,
            emit=request.emit,
            network_retries=request.network_retries,
        )
    )
    candidate = result.candidate
    canonical = candidate.canonical_query.strip() or conservative.canonical_query
    checks = preservation_lint_for_rewrite(request.message, canonical)
    if not all(checks.values()):
        return conservative
    return QueryRewriteResult(
        original_query=request.message,
        normalized_query=conservative.normalized_query,
        canonical_query=canonical,
        retrieval_queries=tuple(candidate.retrieval_queries),
        rewrite_decision=candidate.rewrite_decision,
        preservation_checks=checks,
    )


def preservation_lint_for_rewrite(original: str, canonical: str) -> dict[str, Any]:
    return preservation_lint(original, canonical)


def _router_failure_reason(error: Exception) -> str:
    if isinstance(error, ProviderProtocolError):
        return "intent_router_provider_failure"
    return "intent_router_unexpected_failure"


def _rewrite_strategy_for_rule(rule: RuleRoute) -> str:
    if rule.reason_code == "rule_ui:issue_action":
        return "KEEP"
    return "CONTEXTUALIZE"


def _fallback_task_understanding(route: RouteDecision) -> ChatTaskUnderstanding:
    return ChatTaskUnderstanding(
        primary_intent="question",
        confidence=route.confidence,
        risk_level="low",
        ambiguous=True,
        reason_codes=route.reason_codes,
    )


def _chat_intent_event_payload(request: CaseFileChatRequest) -> dict[str, Any]:
    understanding = request.task_understanding
    if understanding is None:
        return {"router_version": INTENT_ROUTER_VERSION, "primary_intent": None}
    return {
        "router_version": INTENT_ROUTER_VERSION,
        "route_source": request.route.route_source if request.route is not None else None,
        "primary_intent": understanding.primary_intent,
        "sub_intents": list(understanding.sub_intents),
        "risk_level": understanding.risk_level,
        "confidence": understanding.confidence,
        "reason_codes": list(understanding.reason_codes),
        "state": agent_state_to_jsonable(understanding),
    }


def _chat_rewrite_event_payload(request: CaseFileChatRequest) -> dict[str, Any]:
    rewrite = request.rewrite
    route = request.route
    if rewrite is None:
        return {"router_version": INTENT_ROUTER_VERSION, "rewrite_decision": None}
    return {
        "router_version": INTENT_ROUTER_VERSION,
        "route_hash": None if route is None else route.route_hash,
        "rewrite_decision": rewrite.rewrite_decision,
        "retrieval_query_count": len(rewrite.retrieval_queries),
        "preservation_checks": rewrite.preservation_checks,
        "rewrite": agent_state_to_jsonable(rewrite),
    }


resolve_chat_route = _resolve_chat_route
chat_intent_event_payload = _chat_intent_event_payload
chat_rewrite_event_payload = _chat_rewrite_event_payload


class ChatTaskExecutorMixin:
    session_factory: sessionmaker[Session]
    config: Any

    def _emit(self, task_run_id: int, event_type: str, stage: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def _emit_after_completion(
        self,
        task_run_id: int,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    def _load_chat_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> CaseFileChatRequest:
        frozen_input = task.input_jsonb
        if _json_hash(frozen_input) != task.input_hash:
            raise RuntimeError("Frozen CaseFile chat payload does not match its input hash")
        casefile = _required_object(frozen_input, "casefile")
        message = _required_string(frozen_input, "message")
        raw_history = frozen_input.get("history")
        if not isinstance(raw_history, list):
            raise RuntimeError("Frozen CaseFile chat payload is missing history")
        history: list[dict[str, str]] = []
        for item in raw_history:
            if not isinstance(item, dict):
                raise RuntimeError("Frozen CaseFile chat history entry is invalid")
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                raise RuntimeError("Frozen CaseFile chat history entry is invalid")
            history.append({"role": role, "content": content})
        raw_validation = frozen_input.get("validation")
        validation: dict[str, Any] = {}
        validation_issues: tuple[dict[str, Any], ...] = ()
        if isinstance(raw_validation, dict):
            validation = raw_validation
            raw_issues = raw_validation.get("issues")
            if isinstance(raw_issues, list):
                validation_issues = tuple(item for item in raw_issues if isinstance(item, dict))
        focus = frozen_input.get("focus")
        if not isinstance(focus, dict):
            focus = {}
        routing_hint: dict[str, Any] = {}
        raw_routing_hint = frozen_input.get("routing_hint")
        if raw_routing_hint is not None:
            if not isinstance(raw_routing_hint, dict):
                raise RuntimeError("Frozen CaseFile chat routing_hint is invalid")
            if frozen_input.get("router_version") != INTENT_ROUTER_VERSION:
                raise RuntimeError("Frozen CaseFile chat router version is invalid")
            routing_hint = normalize_routing_hint(raw_routing_hint)
        raw_context_policy = frozen_input.get("context_policy_version")
        if raw_context_policy is not None and not isinstance(raw_context_policy, str):
            raise RuntimeError("Frozen CaseFile chat context policy version is invalid")
        context_policy_version = (
            raw_context_policy
            if isinstance(raw_context_policy, str) and raw_context_policy
            else LEGACY_CONTEXT_POLICY_VERSION
        )
        return CaseFileChatRequest(
            task_run_id=task.id,
            prompt_version=task.prompt_version,
            casefile=casefile,
            history=tuple(history),
            message=message,
            editable_fields_by_collection=chat_editable_fields_by_collection(),
            input_hash=task.input_hash,
            model_id=task.model_id,
            api_key=api_key,
            max_turns=int(task.budget_jsonb.get("max_turns", 12)),
            emit=lambda event_type, stage, payload: self._emit(task.id, event_type, stage, payload),
            validation_issues=validation_issues,
            validation=validation,
            focus=focus,
            routing_hint=routing_hint,
            network_retries=_network_retries(task),
            toolset_version=task.toolset_version,
            context_policy_version=context_policy_version,
            thread_id=task.agent_thread_id,
            thread_evidence_resolver=lambda evidence_id: self._resolve_thread_evidence(
                task.agent_thread_id,
                evidence_id,
            ),
        )

    def _execute_general_mutation(
        self,
        task: TaskRun,
        request: CaseFileChatRequest,
        provider: AgentProvider,
        api_key: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Plan and prove an edit request without changing the Draft."""

        mode = self.config.general_mutation_mode
        intent = (
            None
            if request.task_understanding is None
            else request.task_understanding.primary_intent
        )
        if mode == "off" or intent != "edit_request":
            return None, {}

        def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
            self._emit(task.id, event_type, stage, payload)

        emit(
            "agent.step.started",
            "general_mutation",
            {
                "component_id": GENERAL_MUTATION_COMPONENT_ID,
                "component_version": GENERAL_MUTATION_PROMPT_VERSION,
                "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                "input_hash": task.input_hash,
            },
        )
        request_budget_reason = general_mutation_request_budget_reason(request.message)
        if request_budget_reason is not None:
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": request_budget_reason,
                    "failure_layer": "request_budget",
                    "issues": [{"code": request_budget_reason}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {"reason_code": request_budget_reason},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        dependency_reason = general_mutation_request_dependency_reason(request.message)
        if dependency_reason is not None:
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": dependency_reason,
                    "failure_layer": "request_dependency",
                    "issues": [{"code": dependency_reason}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {"reason_code": dependency_reason},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        system_field_reason = general_mutation_explicit_system_field_reason(request.message)
        if system_field_reason is not None:
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": system_field_reason,
                    "failure_layer": "request_field_authority",
                    "issues": [{"code": system_field_reason}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {"reason_code": system_field_reason},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        explicit_unknown_ids = general_mutation_explicit_unknown_object_ids(
            request.message,
            request.casefile,
            request.editable_fields_by_collection,
        )
        if explicit_unknown_ids:
            reason_code = "general_mutation_explicit_object_unknown"
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": reason_code,
                    "failure_layer": "request_identity",
                    "issues": [{"code": reason_code}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {"reason_code": reason_code},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        try:
            planned = provider.plan_general_mutation(
                GeneralMutationPlannerRequest(
                    task_run_id=task.id,
                    model_id=task.model_id,
                    api_key=api_key,
                    casefile=request.casefile,
                    message=request.message,
                    input_hash=task.input_hash,
                    editable_fields_by_collection=request.editable_fields_by_collection,
                    emit=emit,
                    network_retries=_network_retries(task),
                )
            )
            bound = bind_general_mutation_plan(
                planned.candidate,
                request.casefile,
                task_run_id=task.id,
                draft_id=task.draft_id,
                base_revision=task.input_draft_revision,
            )
            simulation = VerificationEngine(profile="fast").simulate_mutation_set(
                request.casefile,
                bound.mutation_set,
            )
            impact_hash = general_mutation_impact_hash(simulation)
            artifact = planned.candidate.model_dump(mode="json")
            emit(
                "agent.step.completed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "output_hash": bound.plan_hash,
                    "usage": planned.usage,
                    "_artifact": artifact,
                },
            )
            emit(
                "general_mutation.planned",
                "general_mutation",
                {
                    "mode": mode,
                    "plan_hash": bound.plan_hash,
                    "operation_count": len(bound.operations),
                    "operation_types": [item.operation_type for item in bound.operations],
                    "collections": [item.target_collection for item in bound.operations],
                },
            )
            emit(
                "general_mutation.simulated",
                "general_mutation",
                {
                    "mode": mode,
                    "can_apply": simulation.can_apply,
                    "reason_code": simulation.reason_code,
                    "candidate_hash": simulation.candidate_hash,
                    "impact_hash": impact_hash,
                    "contains_delete": bound.contains_delete,
                },
            )
            if mode == "shadow":
                return {
                    "status": "shadow",
                    "bound": bound,
                    "simulation": simulation,
                    "impact_hash": impact_hash,
                }, planned.usage
            if (
                any(
                    isinstance(item, CreateMutationCandidate)
                    for item in planned.candidate.operations
                )
                and not self.config.general_mutation_create_enabled
            ):
                emit(
                    "general_mutation.blocked",
                    "general_mutation",
                    {"reason_code": "general_mutation_create_not_enabled"},
                )
                return {"status": "blocked"}, planned.usage
            if (
                any(
                    isinstance(item, DeleteMutationCandidate)
                    for item in planned.candidate.operations
                )
                and not self.config.general_mutation_delete_enabled
            ):
                emit(
                    "general_mutation.blocked",
                    "general_mutation",
                    {"reason_code": "general_mutation_delete_not_enabled"},
                )
                return {"status": "blocked"}, planned.usage
            if not simulation.can_apply and simulation.reason_code != "repair_required":
                emit(
                    "general_mutation.blocked",
                    "general_mutation",
                    {"reason_code": simulation.reason_code or "simulation_blocked"},
                )
                return ({"status": "blocked"} if mode == "suggest" else None), planned.usage
            return {
                "status": "ready",
                "bound": bound,
                "simulation": simulation,
                "impact_hash": impact_hash,
            }, planned.usage
        except ContractValidationError as error:
            reason_code = next(
                (
                    str(item["code"])
                    for item in error.errors
                    if str(item.get("code", "")).startswith("general_mutation_")
                ),
                "general_mutation_planner_failed",
            )
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": reason_code,
                    "failure_layer": "planner_contract",
                    "issues": [{"code": reason_code}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {"reason_code": reason_code},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        except GeneralMutationBindingError as error:
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": error.reason_code,
                    "failure_layer": "binder",
                    "issues": [{"code": error.reason_code}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.bind_failed",
                "general_mutation",
                {"reason_code": error.reason_code},
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}
        except Exception as error:  # Planner failures never fail the Chat answer.
            emit(
                "agent.step.failed",
                "general_mutation",
                {
                    "component_id": GENERAL_MUTATION_COMPONENT_ID,
                    "schema_id": GENERAL_MUTATION_SCHEMA_ID,
                    "error_code": "general_mutation_planner_failed",
                    "failure_layer": "planner_or_binder",
                    "issues": [{"code": type(error).__name__}],
                    "recoverable": False,
                },
            )
            emit(
                "general_mutation.blocked",
                "general_mutation",
                {
                    "reason_code": "general_mutation_planner_failed",
                    "error_type": type(error).__name__,
                },
            )
            return ({"status": "blocked"} if mode == "suggest" else None), {}

    def _load_previous_chat_routing(self, task_run_id: int) -> ReusedChatRouting | None:
        """Reuse the latest route decision on retry; never classify twice."""

        with self.session_factory() as session:
            route_event = session.scalar(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task_run_id,
                    TaskEvent.event_type == "route.decided",
                )
                .order_by(TaskEvent.sequence_no.desc())
                .limit(1)
            )
            if route_event is None:
                return None
            intent_event = session.scalar(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task_run_id,
                    TaskEvent.event_type == "intent.understood",
                )
                .order_by(TaskEvent.sequence_no.desc())
                .limit(1)
            )
            rewrite_event = session.scalar(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task_run_id,
                    TaskEvent.event_type == "query.rewritten",
                )
                .order_by(TaskEvent.sequence_no.desc())
                .limit(1)
            )
        if intent_event is None or rewrite_event is None:
            return None
        intent_payload = intent_event.payload_jsonb
        rewrite_payload = rewrite_event.payload_jsonb
        state = intent_payload.get("state")
        rewrite = rewrite_payload.get("rewrite")
        if not isinstance(state, dict) or not isinstance(rewrite, dict):
            return None
        return ReusedChatRouting(
            task_understanding=ChatTaskUnderstanding(**state),
            route=RouteDecision(**route_event.payload_jsonb),
            rewrite=QueryRewriteResult(**rewrite),
        )

    def _emit_chat_routing_events(
        self,
        task_run_id: int,
        request: CaseFileChatRequest,
    ) -> None:
        """Emit the R1 deterministic routing audit trail before the model call."""

        route = request.route
        if route is None:
            return
        self._emit(
            task_run_id,
            "intent.understood",
            "routing",
            _chat_intent_event_payload(request),
        )
        self._emit(
            task_run_id,
            "route.decided",
            "routing",
            route_public_payload(route),
        )
        self._emit(
            task_run_id,
            "query.rewritten",
            "routing",
            _chat_rewrite_event_payload(request),
        )

    def _resolve_thread_evidence(
        self,
        thread_id: int | None,
        evidence_id: str,
    ) -> dict[str, Any] | None:
        """Resolve one ``thread://{thread_id}/message/{seq}`` pointer read-only."""

        if thread_id is None:
            return None
        ref = EvidenceRef.parse(evidence_id)
        if ref is None or ref.scheme != "thread":
            return None
        parts = ref.identifier.split("/")
        try:
            if len(parts) == 3 and parts[1] == "message":
                ref_thread_id = int(parts[0])
                sequence_no = int(parts[2])
            elif len(parts) == 2 and parts[0] == "message":
                ref_thread_id = thread_id
                sequence_no = int(parts[1])
            else:
                return None
        except ValueError:
            return None
        if ref_thread_id != thread_id:
            return None
        with self.session_factory() as session:
            message = session.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.thread_id == thread_id,
                    AgentMessage.sequence_no == sequence_no,
                )
                .limit(1)
            )
        if message is None:
            return None
        return {
            "evidence_id": evidence_id,
            "message_id": int(message.id),
            "sequence_no": int(message.sequence_no),
            "role": str(message.role),
            "status": str(message.status),
            "content": str(message.content_text or ""),
        }

    def _load_chat_thread_memory_state(
        self,
        task: TaskRun,
    ) -> tuple[dict[str, Any], str | None]:
        """Resolve the frozen context state ref to its validated JSON payload."""

        empty = thread_memory_state_to_jsonable(empty_thread_memory_state())
        raw_ref = task.input_jsonb.get("context_state")
        if not isinstance(raw_ref, dict):
            return empty, None
        state_id = raw_ref.get("state_id")
        if not isinstance(state_id, int) or state_id <= 0:
            return empty, "thread_memory_state_ref_invalid"
        with self.session_factory() as session:
            state = session.get(AgentThreadContextState, state_id)
        if state is None or state.thread_id != task.agent_thread_id:
            return empty, "thread_memory_state_missing"
        raw_state = state.state_jsonb
        if not isinstance(raw_state, dict):
            return empty, "thread_memory_state_malformed"
        return dict(raw_state), None

    def _emit_chat_context_events(
        self,
        task_run_id: int,
        task: TaskRun,
        request: CaseFileChatRequest,
    ) -> CaseFileChatRequest:
        """Assemble context, publish its audit manifest, and bind the provider input.

        Legacy policies measure the exact string providers already render and
        leave the request untouched. v1 binds the v4 prompt payload; v2 binds
        Thread Memory with the v5 prompt; v3 additionally exposes the Phase 4
        Context Tools via the v7 prompt and embeds the dashboard for both; v4
        pairs the logic_audit full-snapshot routing with the v8 prompt; v5 keeps
        the v4 context shape and binds the v9 structured-audit prompt; v6 keeps
        the v5 context shape and binds the v10 hardened routing/evidence prompt.
        """

        policy_v2 = request.context_policy_version == CHAT_CONTEXT_POLICY_V2_VERSION
        policy_v3 = request.context_policy_version == CHAT_CONTEXT_POLICY_V3_VERSION
        policy_v4 = request.context_policy_version == CHAT_CONTEXT_POLICY_V4_VERSION
        policy_v5 = request.context_policy_version == CHAT_CONTEXT_POLICY_V5_VERSION
        policy_v6 = request.context_policy_version == CHAT_CONTEXT_POLICY_V6_VERSION
        thread_memory_policy = policy_v2 or policy_v3 or policy_v4 or policy_v5 or policy_v6
        legacy_policy = request.context_policy_version == LEGACY_CONTEXT_POLICY_VERSION
        executor_input: str | None = None
        if legacy_policy:
            _instructions, executor_input = render_chat_executor_prompt(request)
        extra_input: dict[str, Any] = {
            "editable_fields_by_collection": request.editable_fields_by_collection,
        }
        memory_warning: str | None = None
        if thread_memory_policy:
            thread_memory_state, memory_warning = self._load_chat_thread_memory_state(task)
            extra_input["thread_memory_state"] = thread_memory_state
        context_frozen_input = {
            **task.input_jsonb,
            "validation": dict(request.validation),
        }
        hard_input_tokens = _context_hard_input_tokens()
        try:
            result = build_chat_context_manifest(
                policy_version=request.context_policy_version,
                frozen_input=context_frozen_input,
                input_hash=task.input_hash,
                routing=chat_routing_payload_as_dict(request),
                prebuilt_input=executor_input,
                extra_input=extra_input,
                provider=task.provider,
                model_id=task.model_id,
                hard_input_tokens=hard_input_tokens,
            )
        except ContextEngineError as error:
            self._emit(
                task_run_id,
                "context.guardrail",
                "context",
                {
                    "policy_version": request.context_policy_version,
                    "reason_code": "hard_input_cap_exceeded",
                    "hard_input_tokens": hard_input_tokens,
                    "detail": str(error),
                },
            )
            raise
        for violation in result.dashboard.get("guardrail_violations", []):
            if not isinstance(violation, dict):
                continue
            self._emit(
                task_run_id,
                "context.guardrail",
                "context",
                {
                    "policy_version": request.context_policy_version,
                    "reason_code": str(violation.get("reason_code")),
                    "block_id": str(violation.get("block_id") or ""),
                    "detail": str(violation.get("detail")),
                },
            )
        if memory_warning is not None:
            self._emit(
                task_run_id,
                "context.guardrail",
                "context",
                {
                    "policy_version": request.context_policy_version,
                    "reason_code": memory_warning,
                    "detail": "Thread Memory state was unavailable; empty state used",
                },
            )
        if result.fallback is not None:
            self._emit(
                task_run_id,
                "context.guardrail",
                "context",
                {
                    "policy_version": request.context_policy_version,
                    "reason_code": result.fallback.code,
                    "detail": result.fallback.detail,
                },
            )
        self._emit(task_run_id, "context.built", "context", result.manifest.to_jsonable())
        if result.fallback is not None or legacy_policy:
            return request
        expected_prompt = (
            CHAT_CONTEXT_PROMPT_V9_VERSION
            if policy_v6
            else (
                CHAT_CONTEXT_PROMPT_V6_VERSION
                if policy_v5
                else (
                    CHAT_CONTEXT_PROMPT_V5_VERSION
                    if policy_v4
                    else (
                        CHAT_CONTEXT_PROMPT_V4_VERSION
                        if policy_v3
                        else (
                            CHAT_CONTEXT_PROMPT_V2_VERSION
                            if policy_v2
                            else CHAT_CONTEXT_PROMPT_VERSION
                        )
                    )
                )
            )
        )
        if request.prompt_version not in {
            expected_prompt,
            "casefile-chat-v13",
            "casefile-chat-v14",
            "casefile-chat-v15",
        }:
            raise RuntimeError(
                "Context policy "
                f"{request.context_policy_version!r} requires prompt version "
                f"{expected_prompt!r}; frozen={request.prompt_version!r}"
            )
        return replace(
            request,
            assembled_input=chat_input_payload_from_assembly(
                result.assembly,
                require_thread_memory=thread_memory_policy,
                dashboard=result.dashboard if thread_memory_policy else None,
            ),
        )

    def _maybe_compact_chat_thread(
        self,
        task: TaskRun,
        provider: AgentProvider,
        api_key: str,
        *,
        model_requested_compaction: bool = False,
    ) -> None:
        """Run the rolling compaction monitor after a completed reply.

        Triggered only for v2/v3 policies at a semantic boundary (the newest
        completed message is an assistant reply and no other thread task is
        active). A model request from ``request_thread_compaction`` bypasses
        the history-token/min-message thresholds but never bypasses the
        semantic boundary, range check, or idle-thread guard. The LLM compacts
        ``old_state + new raw turns``; merger and StateValidator run
        deterministically afterwards, and failures fall back to the previous
        state without failing the chat task.
        """

        def emit(
            event_type: str,
            stage: str,
            payload: dict[str, Any],
        ) -> None:
            self._emit_after_completion(task.id, event_type, stage, payload)

        policy_version = task.input_jsonb.get("context_policy_version")
        thread_id = task.agent_thread_id
        if (
            policy_version
            not in {
                CHAT_CONTEXT_POLICY_V2_VERSION,
                CHAT_CONTEXT_POLICY_V3_VERSION,
            }
            or thread_id is None
        ):
            emit(
                "context.compaction_skipped",
                "context",
                {
                    "reason_code": "not_applicable",
                    "policy_version": policy_version,
                    "thread_id": thread_id,
                },
            )
            return
        history_threshold = _compaction_env_int(
            "CASEFILE_CHAT_COMPACTION_HISTORY_TOKENS",
            8000,
        )
        min_new_messages = _compaction_env_int(
            "CASEFILE_CHAT_COMPACTION_MIN_MESSAGES",
            6,
        )
        try:
            with self.session_factory() as session:
                latest_state = session.scalar(
                    select(AgentThreadContextState)
                    .where(AgentThreadContextState.thread_id == thread_id)
                    .order_by(AgentThreadContextState.id.desc())
                    .limit(1)
                )
                completed_messages = list(
                    session.scalars(
                        select(AgentMessage)
                        .where(
                            AgentMessage.thread_id == thread_id,
                            AgentMessage.status == "completed",
                        )
                        .order_by(AgentMessage.sequence_no)
                    )
                )
                if not completed_messages or completed_messages[-1].role != "assistant":
                    emit(
                        "context.compaction_skipped",
                        "context",
                        {
                            "reason_code": "not_semantic_boundary",
                            "completed_messages": len(completed_messages),
                        },
                    )
                    return
                last_message_seq = int(completed_messages[-1].sequence_no)
                if (
                    latest_state is not None
                    and latest_state.to_message_seq is not None
                    and latest_state.to_message_seq >= last_message_seq
                ):
                    emit(
                        "context.compaction_skipped",
                        "context",
                        {"reason_code": "range_already_compacted"},
                    )
                    return
                from_message_seq = (
                    int(latest_state.to_message_seq) + 1
                    if latest_state is not None and latest_state.to_message_seq is not None
                    else 1
                )
                new_turns = [
                    {
                        "role": str(message.role),
                        "content": str(message.content_text),
                    }
                    for message in completed_messages
                    if message.sequence_no >= from_message_seq
                    and message.role in {"user", "assistant"}
                    and message.content_text
                ]
                if not new_turns:
                    emit(
                        "context.compaction_skipped",
                        "context",
                        {
                            "reason_code": "no_new_turns",
                            "from_message_seq": from_message_seq,
                            "to_message_seq": last_message_seq,
                        },
                    )
                    return
                if not model_requested_compaction:
                    if len(new_turns) < min_new_messages:
                        emit(
                            "context.compaction_skipped",
                            "context",
                            {
                                "reason_code": "below_min_messages",
                                "new_messages": len(new_turns),
                                "min_new_messages": min_new_messages,
                            },
                        )
                        return
                    history_tokens = _thread_history_tokens(new_turns)
                    if history_tokens < history_threshold:
                        emit(
                            "context.compaction_skipped",
                            "context",
                            {
                                "reason_code": "below_history_threshold",
                                "history_tokens": history_tokens,
                                "history_threshold": history_threshold,
                            },
                        )
                        return
                else:
                    emit(
                        "context.compaction_requested",
                        "context",
                        {
                            "requested_by": "model",
                            "new_messages": len(new_turns),
                            "from_message_seq": from_message_seq,
                            "to_message_seq": last_message_seq,
                        },
                    )
                other_active = session.scalar(
                    select(TaskRun.id)
                    .where(
                        TaskRun.agent_thread_id == thread_id,
                        TaskRun.status.in_(("queued", "running", "cancelling")),
                        TaskRun.id != task.id,
                    )
                    .limit(1)
                )
                if other_active is not None:
                    emit(
                        "context.compaction_skipped",
                        "context",
                        {
                            "reason_code": "other_thread_task_active",
                            "task_run_id": other_active,
                        },
                    )
                    return
                old_state = (
                    thread_memory_state_from_jsonable(latest_state.state_jsonb)
                    if latest_state is not None
                    else empty_thread_memory_state()
                )
                db_decisions = _thread_db_decisions(
                    session,
                    thread_id=thread_id,
                    from_message_seq=from_message_seq,
                    to_message_seq=last_message_seq,
                )
                compactor = default_compactor_registry()[DEFAULT_THREAD_MEMORY_COMPACTOR]
                input_data = compactor.build_input(
                    old_state=old_state,
                    new_turns=new_turns,
                    db_decisions=db_decisions,
                    from_message_seq=from_message_seq,
                    to_message_seq=last_message_seq,
                )
                compaction_request = ThreadCompactionRequest(
                    task_run_id=task.id,
                    prompt_version=CASEFILE_CHAT_CONTEXT_COMPACTOR_VERSION,
                    input_hash=str(input_data["input_hash"]),
                    input_data=input_data,
                    model_id=task.model_id,
                    api_key=api_key,
                    network_retries=_network_retries(task),
                    max_turns=1,
                    emit=lambda event_type, stage, payload: emit(
                        event_type,
                        stage,
                        payload,
                    ),
                )

            compactor = default_compactor_registry()[DEFAULT_THREAD_MEMORY_COMPACTOR]
            compacted = provider.compact_thread_memory(compaction_request)
            merged_state = compactor.merge(
                old_state,
                compacted.candidate,
                db_decisions=db_decisions,
                last_compacted_message_seq=last_message_seq,
            )
            validation_errors = [
                *compactor.validate(merged_state),
                *preservation_errors(old_state, merged_state),
            ]
            with self.session_factory() as session:
                validation_errors.extend(
                    _thread_state_evidence_errors(
                        session,
                        state_id=0,
                        thread_id=thread_id,
                        evidence_refs=[
                            *merged_state.evidence_refs,
                            *(item.thread_ref for item in merged_state.decisions),
                        ],
                        verified_source_message_ids=[
                            item.source_message_id for item in merged_state.verified_facts
                        ],
                    )
                )
            if validation_errors:
                emit(
                    "context.compaction_failed",
                    "context",
                    {
                        "reason_code": "thread_memory_validation_failed",
                        "errors": validation_errors,
                        "from_message_seq": from_message_seq,
                        "to_message_seq": last_message_seq,
                    },
                )
                return
            with self.session_factory() as session:
                state_row = AgentThreadContextState(
                    project_id=task.project_id,
                    thread_id=thread_id,
                    policy_version=policy_version,
                    state_kind=THREAD_MEMORY_STATE_KIND,
                    from_message_seq=from_message_seq,
                    to_message_seq=last_message_seq,
                    state_jsonb=thread_memory_state_to_jsonable(merged_state),
                    input_hash=str(input_data["input_hash"]),
                )
                session.add(state_row)
                session.commit()
                state_id = int(state_row.id)
            emit(
                "context.compacted",
                "context",
                {
                    "state_id": state_id,
                    "policy_version": policy_version,
                    "state_kind": THREAD_MEMORY_STATE_KIND,
                    "from_message_seq": from_message_seq,
                    "to_message_seq": last_message_seq,
                    "input_hash": str(input_data["input_hash"]),
                    "compactor": DEFAULT_THREAD_MEMORY_COMPACTOR,
                    "prompt_version": CASEFILE_CHAT_CONTEXT_COMPACTOR_VERSION,
                    "usage": compacted.usage,
                    "constraint_count": len(merged_state.constraints),
                    "verified_fact_count": len(merged_state.verified_facts),
                },
            )
        except Exception as error:  # compaction never fails the chat task
            emit(
                "context.compaction_failed",
                "context",
                {
                    "reason_code": "thread_memory_compaction_error",
                    "reason": type(error).__name__,
                    "detail": str(error),
                },
            )

    def _complete_chat(
        self,
        task_run_id: int,
        attempt_id: int,
        result: CaseFileChatResult,
        *,
        route: RouteDecision | None = None,
        repair_envelope: dict[str, Any] | None = None,
        repair_usage: dict[str, Any] | None = None,
        general_mutation_envelope: dict[str, Any] | None = None,
    ) -> None:
        suggestions: list[dict[str, Any]] = []
        for suggestion in result.candidate.suggestions:
            try:
                value = json.loads(suggestion.value_json)
            except json.JSONDecodeError as error:
                raise ProviderProtocolError(
                    "CaseFile chat suggestion value_json is invalid"
                ) from error
            suggestion_payload: dict[str, Any] = {
                "object_id": suggestion.object_id,
                "path": suggestion.path,
                "value": value,
                "reason": suggestion.reason,
            }
            finding_ref = getattr(suggestion, "finding_ref", None)
            if finding_ref is not None:
                suggestion_payload["finding_ref"] = finding_ref
            suggestions.append(suggestion_payload)
        audit_findings: list[dict[str, Any]] = []
        for finding in getattr(result.candidate, "audit_findings", []):
            audit_findings.append(
                {
                    "finding_id": finding.finding_id,
                    "kind": finding.kind,
                    "severity": finding.severity,
                    "title": finding.title,
                    "statement": finding.statement,
                    "needs_manual_review": finding.needs_manual_review,
                    "evidence_object_ids": list(finding.evidence_object_ids),
                    "evidence_event_ids": list(finding.evidence_event_ids),
                    "evidence_validation_issue_ids": list(finding.evidence_validation_issue_ids),
                }
            )
        with self.session_factory() as session:
            route_payload = (
                None if route is None else cast(dict[str, Any], agent_state_to_jsonable(route))
            )
            WorkflowService(session).complete_chat_task(
                task_run_id,
                attempt_id,
                answer=result.candidate.answer,
                referenced_object_ids=result.candidate.referenced_object_ids,
                referenced_event_ids=result.candidate.referenced_event_ids,
                referenced_validation_issue_ids=(result.candidate.referenced_validation_issue_ids),
                suggested_view=result.candidate.suggested_view,
                suggestions=suggestions,
                audit_findings=audit_findings,
                usage=_merge_numeric_usage(result.usage, repair_usage or {}),
                route=route_payload,
                tools=result.tools.as_dict(),
                repair_envelope=repair_envelope,
                general_mutation_envelope=general_mutation_envelope,
            )


__all__ = [
    "ChatTaskExecutorMixin",
    "chat_intent_event_payload",
    "chat_rewrite_event_payload",
    "resolve_chat_route",
]
