"""Worker handler that composes the CaseFile Chat execution adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from casefile.agent_runtime.chat_execution import (
    ChatExecutionRunner,
    prepare_chat_request_artifacts,
)
from casefile.agent_runtime.chat_intent import route_public_payload
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.chat import ChatTaskExecutor, resolve_chat_route


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


__all__ = ["ChatHandler"]
