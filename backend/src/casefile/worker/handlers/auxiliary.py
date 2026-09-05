"""Worker handlers for small Brief-related Provider tasks."""

from __future__ import annotations

from typing import Literal, cast

from casefile.agent_runtime import (
    BriefAnchorExtractRequest,
    BriefPolishRequest,
    BriefStrategyOptionsRequest,
    PolishMode,
)
from casefile.data_postgres.models import BriefVersion
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
from casefile.worker.input_contracts import (
    text_hash as _text_hash,
)
from casefile.worker.provider_resolution import required_provider_binding


class AuxiliaryBriefHandler:
    task_types = frozenset({"brief_polish", "brief_anchor_extract", "brief_strategy_options"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, completion: CompletionExecutor) -> None:
        self._completion = completion

    def execute(self, context: TaskExecutionContext) -> None:
        handlers = {
            "brief_polish": self._execute_polish,
            "brief_anchor_extract": self._execute_anchor_extract,
            "brief_strategy_options": self._execute_strategy_options,
        }
        handlers[context.task.task_type](context)

    def _execute_polish(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        source_text = _required_string(task.input_jsonb, "source_text")
        if _text_hash(source_text) != task.input_hash:
            raise RuntimeError("Frozen SourceRecord payload does not match its input hash")
        polish_mode = _required_string(task.input_jsonb, "polish_mode")
        if polish_mode not in {"proofread", "rewrite", "narrative_enhance"}:
            raise RuntimeError("Frozen polish mode is invalid")
        result = provider.polish(
            BriefPolishRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                source_text=source_text,
                polish_mode=cast(PolishMode, polish_mode),
                input_hash=task.input_hash,
                model_id=required_provider_binding(task)[1],
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: context.emit(
                    task, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
            )
        )
        context.state.candidate = result.candidate.model_dump(mode="json")
        context.state.usage = result.usage
        self._completion._complete_polish(task.id, context.attempt_id, result)

    def _execute_anchor_extract(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        frozen_brief = _required_object(task.input_jsonb, "brief")
        if _json_hash(frozen_brief) != task.input_hash:
            raise RuntimeError("Frozen Brief payload does not match its input hash")
        mode = task.input_jsonb.get("mode", "extract")
        if mode not in {"extract", "suggest_author_answer"}:
            raise RuntimeError("Frozen Brief anchor extraction mode is invalid")
        result = provider.extract_anchors(
            BriefAnchorExtractRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                input_hash=task.input_hash,
                model_id=required_provider_binding(task)[1],
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: context.emit(
                    task, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
                mode=cast(Literal["extract", "suggest_author_answer"], mode),
            )
        )
        context.state.candidate = result.candidate.model_dump(mode="json", exclude_none=True)
        context.state.usage = result.usage
        self._completion._complete_anchor_extract(task.id, context.attempt_id, result)

    def _execute_strategy_options(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        with context.session_factory() as session, session.begin():
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if brief_version is None:
                raise RuntimeError("Frozen strategy BriefVersion is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen strategy BriefVersion hash changed")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen strategy Brief payload does not match its hash")
        request = BriefStrategyOptionsRequest(
            task_run_id=task.id,
            prompt_version=task.prompt_version,
            brief=frozen_brief,
            input_hash=task.input_hash,
            model_id=required_provider_binding(task)[1],
            api_key=api_key,
            max_turns=int(task.budget_jsonb.get("max_turns", 12)),
            emit=lambda event_type, stage, payload: context.emit(
                task, event_type, stage, payload
            ),
            network_retries=_network_retries(task),
        )
        result = provider.strategy_options(request)
        context.state.candidate = result.candidate.model_dump(mode="json")
        context.state.usage = result.usage
        self._completion._complete_strategy_options(task.id, context.attempt_id, result)


__all__ = ["AuxiliaryBriefHandler"]
