"""Provider-neutral, bounded N4.4 Scene Semantic Fill orchestration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from pydantic import ValidationError

from casefile.domain.narrative_compiler import CompilerContractError, canonical_json_sha256
from casefile_contracts import SceneSemanticFillProposal

SCENE_COMPILER_PIPELINE_VERSION = "compiler.scene-compiler.shadow.v1"
SCENE_COMPILER_PROMPT_BUNDLE_VERSION = "scene-compiler-shadow-v1"
SCENE_SEMANTIC_FILL_PROMPT_VERSION = "scene-compiler-semantic-fill-v1"
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
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


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


@dataclass(frozen=True, slots=True)
class SceneCompilerExecution:
    proposals: tuple[dict[str, Any], ...]
    stages: tuple[SceneFillStage, ...]
    final_state_hash: str


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
) -> SceneCompilerExecution:
    """Fill deterministic chapter-local batches and chain every exact input hash."""

    inbound_hash = initial_state_hash or canonical_json_sha256(
        {"scene_compiler_state": "empty.v1"}
    )
    stages: list[SceneFillStage] = []
    proposals: list[dict[str, Any]] = []
    for batch in model_view["batches"]:
        batch_id = str(batch["batch_id"])
        batch_ordinal = int(batch["ordinal"])
        input_hash = _batch_input_hash(component_hash, batch, inbound_hash)
        recovered = (
            None if recover_stage is None else recover_stage(batch_id, input_hash)
        )
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
                max_turns=1,
                network_retries=network_retries,
            )
            started = perf_counter()
            result = provider.fill_scene_batch(request)
            latency_ms = (perf_counter() - started) * 1000
            raw_proposal = result.proposal
        else:
            raw_proposal = recovered
        proposal = validate_scene_semantic_fill(raw_proposal, batch_view=batch)
        outbound_hash = canonical_json_sha256(
            {
                "inbound_state_hash": inbound_hash,
                "batch_id": batch_id,
                "proposal": proposal,
            }
        )
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
        )
        stages.append(stage)
        proposals.append(proposal)
        if after_stage is not None:
            after_stage(stage)
        inbound_hash = outbound_hash
    return SceneCompilerExecution(tuple(proposals), tuple(stages), inbound_hash)


def validate_scene_semantic_fill(
    proposal: SceneSemanticFillProposal | dict[str, Any],
    *,
    batch_view: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = (
            proposal.model_dump(mode="json")
            if isinstance(proposal, SceneSemanticFillProposal)
            else SceneSemanticFillProposal.model_validate(proposal).model_dump(mode="json")
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_fill_contract_invalid") from error
    if value["batch_id"] != batch_view["batch_id"]:
        raise CompilerContractError("compiler_scene_fill_batch_mismatch")
    expected_scene_ids = list(batch_view["scene_ids"])
    actual_scene_ids = [scene["scene_id"] for scene in value["scenes"]]
    if actual_scene_ids != expected_scene_ids:
        raise CompilerContractError("compiler_scene_fill_scene_coverage_invalid")
    constraints = {scene["scene_id"]: scene for scene in batch_view["scenes"]}
    catalog = {_ref_key(item["object_ref"]) for item in batch_view["object_catalog"]}
    for scene_fill in value["scenes"]:
        _validate_scene_fill(scene_fill, constraints[scene_fill["scene_id"]], catalog)
    return value


def _validate_scene_fill(
    fill: dict[str, Any], constraint: dict[str, Any], catalog: set[str]
) -> None:
    expected_obligations = {
        item["obligation_key"]: item for item in constraint["obligations"]
    }
    seen_obligations: Counter[str] = Counter()
    seen_local_keys: set[str] = set()
    participants = {_ref_key(ref) for ref in constraint["participant_refs"]}
    allowed_basis = {_ref_key(ref) for ref in constraint["basis_refs"]}
    allowed_basis.update(
        _ref_key(ref)
        for obligation in constraint["obligations"]
        for ref in obligation["basis_refs"]
    )
    for beat in fill["beats"]:
        local_key = beat["local_key"]
        if local_key in seen_local_keys:
            raise CompilerContractError("compiler_scene_fill_local_key_duplicate")
        for dependency in beat["depends_on"]:
            if dependency not in seen_local_keys:
                raise CompilerContractError("compiler_scene_fill_dependency_invalid")
        actor_keys = {_ref_key(ref) for ref in beat["actor_refs"]}
        if not actor_keys <= participants:
            raise CompilerContractError("compiler_scene_fill_actor_invalid")
        all_refs = [*beat["actor_refs"], *beat["target_refs"], *beat["basis_refs"]]
        for transition in beat["knowledge_transitions"]:
            all_refs.extend(
                [transition["subject_ref"], transition["object_ref"], *transition["basis_refs"]]
            )
        for assertion in beat["location_assertions"]:
            all_refs.extend(
                [
                    assertion["subject_ref"],
                    assertion["location_ref"],
                    *assertion["story_time_refs"],
                    *assertion["basis_refs"],
                ]
            )
        if {_ref_key(ref) for ref in all_refs} - catalog:
            raise CompilerContractError("compiler_scene_fill_reference_invalid")
        if not {_ref_key(ref) for ref in beat["basis_refs"]} <= allowed_basis:
            raise CompilerContractError("compiler_scene_fill_provenance_invalid")
        fulfilled = beat["fulfills_obligation_keys"]
        if not fulfilled and not beat["depends_on"]:
            raise CompilerContractError("compiler_scene_fill_unanchored_beat")
        for obligation_key in fulfilled:
            obligation = expected_obligations.get(obligation_key)
            if obligation is None:
                raise CompilerContractError("compiler_scene_fill_obligation_unknown")
            if obligation["kind"] != beat["kind"]:
                raise CompilerContractError("compiler_scene_fill_obligation_kind_mismatch")
            seen_obligations[obligation_key] += 1
        seen_local_keys.add(local_key)
    if seen_obligations != Counter({key: 1 for key in expected_obligations}):
        raise CompilerContractError("compiler_scene_fill_obligation_coverage_invalid")


def _batch_input_hash(
    component_hash: str, batch: dict[str, Any], inbound_state_hash: str
) -> str:
    return canonical_json_sha256(
        {
            "component_hash": component_hash,
            "stage": "scene_semantic_fill",
            "batch": batch,
            "inbound_state_hash": inbound_state_hash,
        }
    )


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


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
    "execute_scene_semantic_fill",
    "validate_scene_semantic_fill",
]
