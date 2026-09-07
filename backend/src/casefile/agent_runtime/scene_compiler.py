"""Provider-neutral, bounded N4.4 Scene Semantic Fill orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Protocol

from casefile.domain.narrative_compiler import (
    CompilerContractError,
    advance_scene_compiler_runtime_state,
    build_scene_compiler_runtime_state,
    canonical_json_sha256,
    project_scene_compiler_inbound_state,
    scene_compiler_runtime_state_hash,
    validate_scene_semantic_fill,
)

SCENE_COMPILER_PIPELINE_VERSION = "compiler.scene-compiler.shadow.v2"
SCENE_COMPILER_PROMPT_BUNDLE_VERSION = "scene-compiler-shadow-v1"
SCENE_SEMANTIC_FILL_PROMPT_VERSION = "scene-compiler-semantic-fill-v7"
SCENE_SEMANTIC_FILL_SCHEMA_ID = "compiler.scene-semantic-fill.v1"


@dataclass(frozen=True, slots=True)
class SceneFillBatchRequest:
    task_run_id: int
    prompt_version: str
    batch_view: dict[str, Any]
    inbound_state_hash: str
    input_hash: str
    model_id: str
    api_key: str
    inbound_state: dict[str, Any] | None = None
    repair_context: dict[str, Any] | None = None
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None
    on_response: Callable[[str, dict[str, Any], str], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class SceneFillBatchResult:
    proposal: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


class SceneCompilerProvider(Protocol):
    def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult: ...


@dataclass(frozen=True, slots=True)
class SceneFillStage:
    batch_id: str
    batch_ordinal: int
    input_hash: str
    inbound_state_hash: str
    output: dict[str, Any]
    outbound_state_hash: str
    usage: dict[str, Any]
    latency_ms: float
    raw_output: str | None
    recovered: bool = False
    call_no: int | None = None


@dataclass(frozen=True, slots=True)
class SceneCompilerExecution:
    proposals: tuple[dict[str, Any], ...]
    stages: tuple[SceneFillStage, ...]
    final_state_hash: str


@dataclass(frozen=True, slots=True)
class SceneFillFailure:
    batch_ordinal: int
    reason_code: str
    evidence: dict[str, Any]
    result: SceneFillBatchResult
    latency_ms: float


def execute_scene_semantic_fill(
    provider: SceneCompilerProvider,
    *,
    task_run_id: int,
    model_view: dict[str, Any],
    component_hash: str,
    model_id: str,
    api_key: str,
    network_retries: int = 0,
    initial_state_hash: str | None = None,
    recover_stage: Callable[[str, str], dict[str, Any] | None] | None = None,
    before_stage: Callable[[str, int, str, str, str], None] | None = None,
    after_stage: Callable[[SceneFillStage], None] | None = None,
    on_failure: Callable[[SceneFillFailure], None] | None = None,
    max_repairs: int = 0,
) -> SceneCompilerExecution:
    """Fill deterministic chapter-local batches and chain every exact input hash."""
    if max_repairs not in (0, 1):
        raise CompilerContractError("compiler_scene_repair_budget_invalid")

    runtime_state = build_scene_compiler_runtime_state(model_view)
    inbound_hash = scene_compiler_runtime_state_hash(runtime_state)
    if initial_state_hash is not None and initial_state_hash != inbound_hash:
        raise CompilerContractError("compiler_scene_initial_state_hash_mismatch")
    stages: list[SceneFillStage] = []
    proposals: list[dict[str, Any]] = []
    for batch in model_view["batches"]:
        batch_id = str(batch["batch_id"])
        batch_ordinal = int(batch["ordinal"])
        inbound_state = project_scene_compiler_inbound_state(runtime_state, batch_view=batch)
        input_hash = _batch_input_hash(component_hash, batch, inbound_hash, inbound_state)
        recovered = None if recover_stage is None else recover_stage(batch_id, input_hash)
        result: SceneFillBatchResult | None = None
        latency_ms = 0.0
        if recovered is None:
            if before_stage is not None:
                before_stage(
                    batch_id,
                    batch_ordinal,
                    input_hash,
                    SCENE_SEMANTIC_FILL_PROMPT_VERSION,
                    SCENE_SEMANTIC_FILL_SCHEMA_ID,
                )
            request = SceneFillBatchRequest(
                task_run_id=task_run_id,
                prompt_version=SCENE_SEMANTIC_FILL_PROMPT_VERSION,
                batch_view=batch,
                inbound_state_hash=inbound_hash,
                input_hash=input_hash,
                model_id=model_id,
                api_key=api_key,
                inbound_state=inbound_state,
                max_turns=1,
                network_retries=network_retries,
            )
            started = perf_counter()
            result = provider.fill_scene_batch(request)
            latency_ms = (perf_counter() - started) * 1000
            raw_proposal = result.proposal
        else:
            raw_proposal = recovered
        call_no = batch_ordinal
        for repair_no in range(min(max_repairs, 1) + 1):
            try:
                proposal = validate_scene_semantic_fill(raw_proposal, batch_view=batch)
                if repair_no and request.repair_context:
                    failed_scene = request.repair_context["error"].get("scene_id")
                    previous = request.repair_context["candidate"]["scenes"]
                    for old, new in zip(previous, proposal["scenes"], strict=True):
                        if old["scene_id"] != failed_scene and old != new:
                            raise CompilerContractError("compiler_scene_repair_preservation_failed")
                runtime_state = advance_scene_compiler_runtime_state(
                    runtime_state,
                    batch_view=batch,
                    semantic_fill=proposal,
                )
                break
            except CompilerContractError as error:
                evidence = {"batch_id": batch_id, **getattr(error, "evidence", {})}
                if result is not None and on_failure is not None:
                    on_failure(
                        SceneFillFailure(
                            call_no,
                            error.reason_code,
                            evidence,
                            result,
                            latency_ms,
                        )
                    )
                if repair_no >= min(max_repairs, 1) or result is None or "scene_id" not in evidence:
                    raise
                context = {
                    "candidate": raw_proposal,
                    "error": {
                        "code": error.reason_code,
                        **evidence,
                    },
                }
                repair_hash = canonical_json_sha256({"input_hash": input_hash, "repair": context})
                call_no = batch_ordinal + 10000
                if before_stage is not None:
                    before_stage(
                        batch_id,
                        call_no,
                        repair_hash,
                        SCENE_SEMANTIC_FILL_PROMPT_VERSION,
                        SCENE_SEMANTIC_FILL_SCHEMA_ID,
                    )
                request = replace(request, input_hash=repair_hash, repair_context=context)
                started = perf_counter()
                result = provider.fill_scene_batch(request)
                latency_ms = (perf_counter() - started) * 1000
                raw_proposal = result.proposal
        outbound_hash = scene_compiler_runtime_state_hash(runtime_state)
        stage = SceneFillStage(
            batch_id=batch_id,
            batch_ordinal=batch_ordinal,
            input_hash=input_hash,
            inbound_state_hash=inbound_hash,
            output=proposal,
            outbound_state_hash=outbound_hash,
            usage={} if result is None else result.usage,
            latency_ms=latency_ms,
            raw_output=None if result is None else result.raw_output,
            recovered=result is None,
            call_no=call_no,
        )
        stages.append(stage)
        proposals.append(proposal)
        if after_stage is not None:
            after_stage(stage)
        inbound_hash = outbound_hash
    return SceneCompilerExecution(tuple(proposals), tuple(stages), inbound_hash)


def _batch_input_hash(
    component_hash: str,
    batch: dict[str, Any],
    inbound_state_hash: str,
    inbound_state: dict[str, Any],
) -> str:
    return canonical_json_sha256(
        {
            "component_hash": component_hash,
            "stage": "scene_semantic_fill",
            "batch": batch,
            "inbound_state_hash": inbound_state_hash,
            "inbound_state": inbound_state,
        }
    )


__all__ = [
    "SCENE_COMPILER_PIPELINE_VERSION",
    "SCENE_COMPILER_PROMPT_BUNDLE_VERSION",
    "SCENE_SEMANTIC_FILL_PROMPT_VERSION",
    "SCENE_SEMANTIC_FILL_SCHEMA_ID",
    "SceneCompilerExecution",
    "SceneCompilerProvider",
    "SceneFillBatchRequest",
    "SceneFillBatchResult",
    "SceneFillStage",
    "SceneFillFailure",
    "execute_scene_semantic_fill",
    "validate_scene_semantic_fill",
]
