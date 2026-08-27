"""Worker handler for reverse parse tasks."""

from __future__ import annotations

from copy import deepcopy

from casefile.agent_runtime import ReverseParseRequest
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.failures import network_retries as _network_retries
from casefile.worker.input_contracts import json_hash as _json_hash
from casefile.worker.provider_resolution import required_provider_binding


class ReverseParseHandler:
    task_types = frozenset({"reverse_parse"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, completion: CompletionExecutor) -> None:
        self._completion = completion

    def execute(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        if _json_hash(task.input_jsonb) != task.input_hash:
            raise RuntimeError("Frozen reverse parse payload does not match its input hash")
        blocks = task.input_jsonb.get("blocks", [])
        if not isinstance(blocks, list):
            raise RuntimeError("Frozen reverse parse blocks must be an array")
        result = provider.reverse_parse(
            ReverseParseRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                blocks=deepcopy(blocks),
                input_hash=task.input_hash,
                model_id=required_provider_binding(task)[1],
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: context.emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
            )
        )
        context.state.candidate = result.candidate.model_dump(mode="json")
        context.state.usage = result.usage
        self._completion._complete_reverse_parse(task.id, context.attempt_id, result)


__all__ = ["ReverseParseHandler"]
