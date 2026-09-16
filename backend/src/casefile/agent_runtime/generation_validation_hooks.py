"""Static adapters from generation lifecycle events to existing pure validators."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.brief_to_draft_v8 import quality, validation
from casefile.agent_runtime.brief_to_draft_v12 import contracts as temporal
from casefile.agent_runtime.generation_hooks import (
    HookDispatcher,
    HookEvent,
    HookInput,
    HookResult,
)
from casefile.agent_runtime.models import GenerationRequest

Validator = Callable[..., list[dict[str, Any]]]
VALIDATORS: dict[str, Validator] = {
    "final_candidate_issues": quality.final_candidate_issues,
    "_blueprint_path_plan_issues": validation._blueprint_path_plan_issues,
    "_v16_blueprint_relationship_coverage_issues": (
        validation._v16_blueprint_relationship_coverage_issues
    ),
    "_blueprint_creator_chinese_issues": quality._blueprint_creator_chinese_issues,
    "temporal_plan_issues": temporal.temporal_plan_issues,
    "_evidence_assessment_issues": validation._evidence_assessment_issues,
    "_v11_story_issues": validation._v11_story_issues,
    "temporal_story_issues": temporal.temporal_story_issues,
    "_v15_story_person_name_issues": validation._v15_story_person_name_issues,
    "_v16_story_relationship_coverage_issues": validation._v16_story_relationship_coverage_issues,
    "_brief_quality_requirement_issues": quality._brief_quality_requirement_issues,
    "_creator_chinese_issues": quality._creator_chinese_issues,
}


def _adapter(validator: Validator) -> Callable[[HookInput], Any]:
    async def run(event: HookInput) -> HookResult:
        return HookResult(
            issues=tuple(validator(*event.payload["args"], **event.payload["kwargs"]))
        )

    return run


async def _prerequisites(event: HookInput) -> HookResult:
    required = {
        "context_pack": (),
        "blueprint_planner": ("context_pack",),
        "temporal_plan": ("context_pack", "blueprint"),
        "domain_draft": ("context_pack", "blueprint", "temporal_plan"),
        "resolution_governance": ("blueprint", "evidence"),
        "compile_quality_gate": ("blueprint", "story", "evidence", "governance"),
        "plan_reconciliation": ("blueprint", "story", "evidence", "governance"),
    }
    missing = [name for name in required[event.stage] if not event.payload.get(name)]
    return HookResult(
        block_reason=f"Missing stage artifacts: {','.join(missing)}" if missing else None
    )


async def _observe(event: HookInput) -> HookResult:
    return HookResult(observations=(f"{event.stage}:{event.event.value}",))


DISPATCHER = HookDispatcher(
    MappingProxyType(
        {
            **{(name, "1"): _adapter(validator) for name, validator in VALIDATORS.items()},
            ("prerequisites", "1"): _prerequisites,
            ("observe_stage", "1"): _observe,
        }
    )
)


async def validate_generation_artifact(
    request: GenerationRequest,
    validator_id: str,
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    if request.prompt_version not in {"brief-to-draft-v17", "brief-to-draft-v18"}:
        return VALIDATORS[validator_id](*args, **kwargs)
    records: list[dict[str, Any]] = []
    try:
        result = await DISPATCHER.dispatch(
            resolve_pipeline_spec(request.prompt_version).hook_bindings,
            HookInput(
                HookEvent.AFTER_ARTIFACT,
                "validation",
                validator_id,
                {"args": args, "kwargs": kwargs},
            ),
            records=records,
        )
        return list(result.issues)
    finally:
        request.emit(
            "agent.hooks.executed",
            "validation",
            {"component_id": "hook_" + validator_id.lstrip("_"), "_execution": {"hooks": records}},
        )


async def run_stage_hooks(request: GenerationRequest, stage: str, event: str, context: Any) -> None:
    if request.prompt_version not in {"brief-to-draft-v17", "brief-to-draft-v18"}:
        return
    records: list[dict[str, Any]] = []
    try:
        await DISPATCHER.dispatch(
            resolve_pipeline_spec(request.prompt_version).hook_bindings,
            HookInput(
                HookEvent(event),
                stage,
                payload={
                    name: getattr(context, name) is not None
                    for name in (
                        "context_pack",
                        "blueprint",
                        "temporal_plan",
                        "story",
                        "evidence",
                        "governance",
                    )
                },
            ),
            records=records,
        )
    finally:
        request.emit(
            "agent.hooks.executed",
            stage,
            {"component_id": "hook_" + stage, "_execution": {"hooks": records}},
        )
