"""Provider-neutral Story Planner request, repair loop, and transcript contract."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from casefile_contracts import NovelPlanCandidate, StoryPlanStructuralPatch
from pydantic import ValidationError

from casefile.domain.narrative_compiler import (
    STORY_PLANNER_STRUCTURAL_REPAIR_VERSION,
    CompilerContractError,
)

STORY_PLANNER_PROMPT_VERSION = "story-planner-v3"
STORY_PLANNER_AGENT_VERSION = "compiler.story-planner.v1"
STORY_PLANNER_TOOLSET_VERSION = "compiler.no-tools.v1"
STORY_PLANNER_MAX_REPAIRS = 3
# DeepSeek V4's provider ceiling (384 Ki tokens), not a local short-output cap.
COMPILER_JSON_MAX_OUTPUT_TOKENS = 393_216


class CompilerProviderOutputError(CompilerContractError):
    """Retain bounded response evidence when a compiler provider cannot finish JSON."""

    def __init__(
        self, code: str, raw_output: str, usage: dict[str, Any], finish_reason: str
    ) -> None:
        super().__init__(code)
        self.raw_output = raw_output
        self.usage = usage
        self.finish_reason = finish_reason


@dataclass(frozen=True, slots=True)
class StoryPlannerRequest:
    task_run_id: int
    prompt_version: str
    planner_input: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str
    provider_input: dict[str, Any] | None = None
    provider_input_hash: str | None = None
    repair_errors: tuple[dict[str, Any], ...] = ()
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class StoryPlannerProviderResult:
    candidate: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


@dataclass(frozen=True, slots=True)
class StoryPlannerPatchRequest:
    task_run_id: int
    prompt_version: str
    candidate: dict[str, Any]
    structural_errors: tuple[dict[str, Any], ...]
    previous_patch_errors: tuple[dict[str, Any], ...]
    expected_scene_ids: tuple[str, ...]
    input_hash: str
    model_id: str
    api_key: str
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class StoryPlannerPatchProviderResult:
    patch: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


StoryPlannerCallRequest = StoryPlannerRequest | StoryPlannerPatchRequest


class StoryPlannerProvider(Protocol):
    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult: ...

    def patch_story(self, request: StoryPlannerPatchRequest) -> StoryPlannerPatchProviderResult: ...


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
    before_call: Callable[[int, StoryPlannerCallRequest], None] | None = None,
    after_round: Callable[[StoryPlannerRound], None] | None = None,
) -> StoryPlannerExecution:
    """Run one initial call plus bounded structural-only repair calls."""

    if max_repairs < 0 or max_repairs > STORY_PLANNER_MAX_REPAIRS:
        raise ValueError("Story Planner max_repairs must be between 0 and 3")
    rounds: list[StoryPlannerRound] = []
    candidate: dict[str, Any] | None = None
    repair_errors: tuple[dict[str, Any], ...] = ()
    previous_patch_errors: tuple[dict[str, Any], ...] = ()
    for call_no in range(1, max_repairs + 2):
        patch_scene_ids = (
            _purpose_patch_scene_ids(candidate, repair_errors) if candidate is not None else None
        )
        current: StoryPlannerCallRequest
        if patch_scene_ids is None:
            current = StoryPlannerRequest(
                task_run_id=request.task_run_id,
                prompt_version=request.prompt_version,
                planner_input=request.planner_input,
                input_hash=request.input_hash,
                model_id=request.model_id,
                api_key=request.api_key,
                provider_input=request.provider_input,
                provider_input_hash=request.provider_input_hash,
                repair_errors=repair_errors,
                max_turns=request.max_turns,
                network_retries=request.network_retries,
                emit=request.emit,
            )
        else:
            assert candidate is not None
            current = StoryPlannerPatchRequest(
                task_run_id=request.task_run_id,
                prompt_version=request.prompt_version,
                candidate=copy.deepcopy(candidate),
                structural_errors=repair_errors,
                previous_patch_errors=previous_patch_errors,
                expected_scene_ids=patch_scene_ids,
                input_hash=request.input_hash,
                model_id=request.model_id,
                api_key=request.api_key,
                max_turns=1,
                network_retries=request.network_retries,
                emit=request.emit,
            )
        if before_call is not None:
            before_call(call_no, current)
        started = perf_counter()
        patch_errors: tuple[dict[str, Any], ...] = ()
        if isinstance(current, StoryPlannerPatchRequest):
            patch_result = provider.patch_story(current)
            proposed, patch_errors = _apply_purpose_patch(
                current.candidate,
                patch_result.patch,
                expected_scene_ids=current.expected_scene_ids,
                previous_error_count=len(repair_errors),
            )
            if not patch_errors:
                candidate = proposed
            usage = patch_result.usage
            raw_output = patch_result.raw_output
        else:
            result = provider.plan_story(current)
            candidate = result.candidate
            usage = result.usage
            raw_output = result.raw_output
        latency_ms = (perf_counter() - started) * 1000
        assert candidate is not None
        if _contains_runtime_identity(candidate):
            round_result = StoryPlannerRound(
                call_no=call_no,
                candidate=candidate,
                usage=usage,
                structural_errors=(),
                raw_output=raw_output,
                latency_ms=latency_ms,
            )
            rounds.append(round_result)
            if after_round is not None:
                after_round(round_result)
            error = CompilerContractError("compiler_story_plan_runtime_identity_forbidden")
            error.rounds = tuple(rounds)  # type: ignore[attr-defined]
            raise error
        candidate_errors = _structural_errors(candidate)
        errors = (*candidate_errors, *patch_errors)
        round_result = StoryPlannerRound(
            call_no=call_no,
            candidate=candidate,
            usage=usage,
            structural_errors=errors,
            raw_output=raw_output,
            latency_ms=latency_ms,
        )
        rounds.append(round_result)
        if after_round is not None:
            after_round(round_result)
        if not candidate_errors and not patch_errors:
            return StoryPlannerExecution(
                candidate=NovelPlanCandidate.model_validate(candidate).model_dump(mode="json"),
                rounds=tuple(rounds),
            )
        repair_errors = candidate_errors
        previous_patch_errors = patch_errors
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


def _purpose_patch_scene_ids(
    candidate: dict[str, Any] | None,
    errors: tuple[dict[str, Any], ...],
) -> tuple[str, ...] | None:
    if candidate is None or not errors:
        return None
    scenes = candidate.get("scenes")
    if not isinstance(scenes, list):
        return None
    scene_ids: list[str] = []
    for error in errors:
        match = re.fullmatch(r"/scenes/(\d+)/purpose", str(error.get("path", "")))
        if match is None:
            return None
        index = int(match.group(1))
        if index >= len(scenes) or not isinstance(scenes[index], dict):
            return None
        scene_id = scenes[index].get("scene_id")
        if not isinstance(scene_id, str):
            return None
        if scene_id not in scene_ids:
            scene_ids.append(scene_id)
    return tuple(scene_ids)


def _apply_purpose_patch(
    candidate: dict[str, Any],
    patch: dict[str, Any],
    *,
    expected_scene_ids: tuple[str, ...],
    previous_error_count: int,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    try:
        parsed = StoryPlanStructuralPatch.model_validate(patch).model_dump(mode="json")
    except ValidationError as error:
        return candidate, tuple(
            {
                "code": "story_planner_structural_patch_invalid",
                "path": "/" + "/".join(str(part) for part in item["loc"]),
                "message": str(item["msg"]),
            }
            for item in error.errors(include_url=False, include_context=False)
        )
    scene_ids = [str(item["scene_id"]) for item in parsed["patches"]]
    if len(scene_ids) != len(set(scene_ids)):
        return candidate, (_patch_error("story_planner_structural_patch_duplicate_scene"),)
    if set(scene_ids) != set(expected_scene_ids):
        return candidate, (_patch_error("story_planner_structural_patch_scope_mismatch"),)

    proposed = copy.deepcopy(candidate)
    scenes = {
        str(scene.get("scene_id")): scene
        for scene in proposed.get("scenes", [])
        if isinstance(scene, dict)
    }
    for item in parsed["patches"]:
        scene = scenes.get(str(item["scene_id"]))
        if scene is None:
            return candidate, (_patch_error("story_planner_structural_patch_scene_missing"),)
        scene["purpose"] = item["purpose"]
    remaining = _structural_errors(proposed)
    if len(remaining) >= previous_error_count:
        return candidate, (_patch_error("story_planner_structural_patch_no_progress"),)
    return proposed, ()


def _patch_error(code: str) -> dict[str, Any]:
    return {"code": code, "path": "/patches", "message": code}


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
    "STORY_PLANNER_STRUCTURAL_REPAIR_VERSION",
    "STORY_PLANNER_TOOLSET_VERSION",
    "StoryPlannerExecution",
    "StoryPlannerPatchProviderResult",
    "StoryPlannerPatchRequest",
    "StoryPlannerProvider",
    "StoryPlannerProviderResult",
    "StoryPlannerRequest",
    "StoryPlannerRound",
    "execute_story_planner",
]
