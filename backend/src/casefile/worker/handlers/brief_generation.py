"""Worker handler for frozen Brief-to-Draft generation tasks."""

from __future__ import annotations

from dataclasses import replace

from casefile.agent_runtime import (
    CANDIDATE_STRATEGY_VERSION,
    CandidateStrategy,
    GenerationRequest,
    GenerationResult,
)
from casefile.contracts import ContractValidationError, public_validation_issues, validate_casefile
from casefile.data_postgres.models import Brief, BriefVersion
from casefile.data_postgres.repositories import ProjectRepository
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.failures import network_retries as _network_retries
from casefile.worker.generation_reuse import (
    previous_attempt_repair_feedback as _previous_attempt_repair_feedback,
)
from casefile.worker.generation_reuse import (
    reusable_component_steps as _reusable_component_steps,
)
from casefile.worker.input_contracts import (
    json_hash as _json_hash,
)
from casefile.worker.input_contracts import (
    optional_string as _optional_string,
)
from casefile.worker.input_contracts import (
    required_integer as _required_integer,
)
from casefile.worker.input_contracts import (
    required_object as _required_object,
)
from casefile.worker.input_contracts import (
    required_string as _required_string,
)


class BriefGenerationHandler:
    task_types = frozenset({"brief_to_draft"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, completion: CompletionExecutor) -> None:
        self._completion = completion

    def execute(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.require_provider()
        task = context.task
        request = self._load_request(context, api_key)
        result: GenerationResult | None = None
        repair_limit = (
            0
            if task.prompt_version == "brief-to-draft-v7"
            else int(task.budget_jsonb.get("structural_repair_attempts", 5))
        )
        feedback = request.repair_feedback
        feedback_history: list[dict[str, object]] = list(feedback)
        for repair_no in range(repair_limit + 1):
            if repair_no:
                context.emit(
                    task.id,
                    "model.repair_started",
                    "repairing",
                    {"repair_no": repair_no, "max_repairs": repair_limit},
                )
            try:
                result = provider.generate(replace(request, repair_feedback=feedback))
                context.state.candidate = result.candidate
                validate_casefile(result.candidate)
                break
            except ContractValidationError as error:
                public_issues = public_validation_issues(error.errors)
                context.state.validation_errors.append(
                    {"repair_no": repair_no, "issues": public_issues}
                )
                feedback_history.append({"repair_no": repair_no, "issues": error.errors})
                feedback = tuple(feedback_history[-3:])
                context.emit(
                    task.id,
                    "validation.failed",
                    "validating",
                    {
                        "repair_no": repair_no,
                        "issue_count": len(error.errors),
                        "issues": public_issues,
                    },
                )
                if repair_no >= repair_limit:
                    raise
        if result is None or context.state.candidate is None:
            raise RuntimeError("Provider returned no candidate")
        context.state.usage = result.usage
        self._completion._complete_generation_candidate(
            task.id,
            context.attempt_id,
            context.state.candidate,
            result,
            context.state.validation_errors,
        )

    def _load_request(self, context: TaskExecutionContext, api_key: str) -> GenerationRequest:
        task = context.task
        with context.session_factory() as session, session.begin():
            owned = ProjectRepository(session).get_owned(task.actor_user_id, task.project_id)
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if owned is None or brief_version is None:
                raise RuntimeError("Frozen generation dependencies are missing")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen BriefVersion hash no longer matches TaskRun input")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen TaskRun Brief payload does not match its input hash")
            frozen_version = _required_object(task.input_jsonb, "version")
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            try:
                candidate_strategy = CandidateStrategy(raw_strategy)
            except ValueError as error:
                raise RuntimeError("Frozen candidate strategy is invalid") from error
            candidate_strategy_version = task.input_jsonb.get(
                "candidate_strategy_version",
                CANDIDATE_STRATEGY_VERSION,
            )
            if candidate_strategy_version != CANDIDATE_STRATEGY_VERSION:
                raise RuntimeError("Frozen candidate strategy version is invalid")
            return GenerationRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                schema_version=str(task.input_jsonb.get("schema_version", "1.0")),
                casefile_id=_required_string(task.input_jsonb, "casefile_id"),
                brief_id=_required_string(task.input_jsonb, "brief_public_id"),
                brief_version=_required_integer(task.input_jsonb, "brief_version_no"),
                version_id=_required_string(frozen_version, "version_id"),
                version_no=_required_integer(frozen_version, "version_no"),
                parent_version_id=_optional_string(frozen_version, "parent_version_id"),
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: context.emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
                candidate_strategy=candidate_strategy,
                candidate_strategy_version=candidate_strategy_version,
                reusable_steps=_reusable_component_steps(session, task),
                repair_feedback=_previous_attempt_repair_feedback(session, task),
                agent_version=task.agent_version,
                toolset_version=task.toolset_version,
            )


__all__ = ["BriefGenerationHandler"]
