"""Provider-neutral Story Planner request, repair loop, and transcript contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from pydantic import ValidationError

from casefile.domain.narrative_compiler import CompilerContractError
from casefile_contracts import NovelPlanCandidate

STORY_PLANNER_PROMPT_VERSION = "story-planner-v3"
STORY_PLANNER_AGENT_VERSION = "compiler.story-planner.v1"
STORY_PLANNER_TOOLSET_VERSION = "compiler.no-tools.v1"
STORY_PLANNER_MAX_REPAIRS = 3


@dataclass(frozen=True, slots=True)
class StoryPlannerRequest:
    task_run_id: int
    prompt_version: str
    planner_input: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str
    repair_errors: tuple[dict[str, Any], ...] = ()
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class StoryPlannerProviderResult:
    candidate: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


class StoryPlannerProvider(Protocol):
    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult: ...


@dataclass(frozen=True, slots=True)
class StoryPlannerRound:
    call_no: int
    candidate: dict[str, Any] | None
    usage: dict[str, Any]
    structural_errors: tuple[dict[str, Any], ...]
    raw_output: str | None
    latency_ms: float


@dataclass(frozen=True, slots=True)
class StoryPlannerExecution:
    candidate: dict[str, Any]
    rounds: tuple[StoryPlannerRound, ...]


def execute_story_planner(
    provider: StoryPlannerProvider,
    request: StoryPlannerRequest,
    *,
    max_repairs: int = STORY_PLANNER_MAX_REPAIRS,
    before_call: Callable[[int, StoryPlannerRequest], None] | None = None,
    after_round: Callable[[StoryPlannerRound], None] | None = None,
) -> StoryPlannerExecution:
    """Run one initial call plus bounded structural-only repair calls."""

    if max_repairs < 0 or max_repairs > STORY_PLANNER_MAX_REPAIRS:
        raise ValueError("Story Planner max_repairs must be between 0 and 3")
    rounds: list[StoryPlannerRound] = []
    repair_errors: tuple[dict[str, Any], ...] = ()
    for call_no in range(1, max_repairs + 2):
        current = StoryPlannerRequest(
            task_run_id=request.task_run_id,
            prompt_version=request.prompt_version,
            planner_input=request.planner_input,
            input_hash=request.input_hash,
            model_id=request.model_id,
            api_key=request.api_key,
            repair_errors=repair_errors,
            max_turns=request.max_turns,
            network_retries=request.network_retries,
            emit=request.emit,
        )
        if before_call is not None:
            before_call(call_no, current)
        started = perf_counter()
        result = provider.plan_story(current)
        latency_ms = (perf_counter() - started) * 1000
        if _contains_runtime_identity(result.candidate):
            round_result = StoryPlannerRound(
                call_no=call_no,
                candidate=result.candidate,
                usage=result.usage,
                structural_errors=(),
                raw_output=result.raw_output,
                latency_ms=latency_ms,
            )
            rounds.append(round_result)
            if after_round is not None:
                after_round(round_result)
            error = CompilerContractError("compiler_story_plan_runtime_identity_forbidden")
            error.rounds = tuple(rounds)  # type: ignore[attr-defined]
            raise error
        errors = _structural_errors(result.candidate)
        round_result = StoryPlannerRound(
            call_no=call_no,
            candidate=result.candidate,
            usage=result.usage,
            structural_errors=errors,
            raw_output=result.raw_output,
            latency_ms=latency_ms,
        )
        rounds.append(round_result)
        if after_round is not None:
            after_round(round_result)
        if not errors:
            return StoryPlannerExecution(
                candidate=NovelPlanCandidate.model_validate(result.candidate).model_dump(
                    mode="json"
                ),
                rounds=tuple(rounds),
            )
        repair_errors = errors
    error = CompilerContractError("compiler_story_planner_structural_repair_exhausted")
    error.rounds = tuple(rounds)  # type: ignore[attr-defined]
    raise error


def _structural_errors(candidate: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    try:
        NovelPlanCandidate.model_validate(candidate)
    except ValidationError as error:
        return tuple(
            {
                "code": "story_planner_candidate_schema_invalid",
                "path": "/" + "/".join(str(part) for part in item["loc"]),
                "message": str(item["msg"]),
            }
            for item in error.errors(include_url=False, include_context=False)
        )
    return ()


def _contains_runtime_identity(value: Any) -> bool:
    runtime_keys = {
        "compile_run_id",
        "task_run_id",
        "agent_step_run_id",
        "user_id",
        "database_id",
    }
    if isinstance(value, dict):
        return bool(runtime_keys.intersection(value)) or any(
            _contains_runtime_identity(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_runtime_identity(item) for item in value)
    return False


__all__ = [
    "STORY_PLANNER_AGENT_VERSION",
    "STORY_PLANNER_MAX_REPAIRS",
    "STORY_PLANNER_PROMPT_VERSION",
    "STORY_PLANNER_TOOLSET_VERSION",
    "StoryPlannerExecution",
    "StoryPlannerProvider",
    "StoryPlannerProviderResult",
    "StoryPlannerRequest",
    "StoryPlannerRound",
    "execute_story_planner",
]
