"""Worker handlers for Brief Intake question and synthesis tasks."""

from __future__ import annotations

from copy import deepcopy

from casefile.agent_runtime import BriefIntakeQuestionsRequest, BriefIntakeSynthesizeRequest
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.failures import network_retries as _network_retries
from casefile.worker.input_contracts import (
    json_hash as _json_hash,
)
from casefile.worker.input_contracts import (
    required_object as _required_object,
)
from casefile.worker.input_contracts import (
    required_string as _required_string,
)


class BriefIntakeHandler:
    task_types = frozenset({"brief_intake_questions", "brief_intake_synthesize"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, completion: CompletionExecutor) -> None:
        self._completion = completion

    def execute(self, context: TaskExecutionContext) -> None:
        if context.task.task_type == "brief_intake_questions":
            self._execute_questions(context)
            return
        self._execute_synthesis(context)

    def _execute_questions(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        if _json_hash(task.input_jsonb) != task.input_hash:
            raise RuntimeError("Frozen Brief Intake question payload does not match its input hash")
        frozen_source = _required_object(task.input_jsonb, "source")
        mode = task.input_jsonb.get("mode", "initial")
        if mode not in ("initial", "additional"):
            raise RuntimeError("Frozen Brief Intake question mode is invalid")
        existing_questions = task.input_jsonb.get("existing_questions", [])
        if not isinstance(existing_questions, list):
            raise RuntimeError("Frozen Brief Intake existing questions must be an array")
        result = provider.intake_questions(
            BriefIntakeQuestionsRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                source_text=_required_string(frozen_source, "content_text"),
                existing_questions=deepcopy(existing_questions),
                mode=mode,
                input_hash=task.input_hash,
                model_id=task.model_id,
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
        self._completion._complete_intake_questions(task.id, context.attempt_id, result)

    def _execute_synthesis(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        if _json_hash(task.input_jsonb) != task.input_hash:
            raise RuntimeError(
                "Frozen Brief Intake synthesis payload does not match its input hash"
            )
        result = provider.synthesize_intake(
            BriefIntakeSynthesizeRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                input_data=task.input_jsonb,
                input_hash=task.input_hash,
                model_id=task.model_id,
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
        self._completion._complete_intake_synthesize(task.id, context.attempt_id, result)


__all__ = ["BriefIntakeHandler"]
