"""Pure boundary checks for novel compilation; no literary decisions or I/O.

The Worker owns model calls, artifact writes, cancellation and repair scheduling.
Bindings preserve the existing runtime's validation order and error codes.
"""

from __future__ import annotations

from typing import Any

from casefile.agent_runtime.runtime_hooks import (
    HookBinding,
    HookEvent,
    HookInput,
    HookResult,
    SyncHookDispatcher,
)
from casefile.domain.narrative_compiler import CompilerContractError, canonical_json_sha256
from casefile_contracts import NovelCandidate


def _runtime_binding(event: HookInput) -> HookResult:
    # Lazy import keeps runtime identity independent of hook implementation imports.
    from casefile.agent_runtime.prose_runtime import matches_prose_runtime

    manifest = event.payload["manifest"]
    if (
        not manifest.get("prose_renderer_shadow")
        or event.payload["compile_mode"] != "preview"
        or not matches_prose_runtime(
            manifest.get("prose_runtime", {}),
            manifest["profile"]["frozen_payload"]["structure"]["target_scenes"],
            manifest.get("prose_mode", "full_polish"),
        )
    ):
        raise CompilerContractError("compiler_prose_runtime_binding_mismatch")
    return HookResult()


def _upstream_artifacts(event: HookInput) -> HookResult:
    for artifact in event.payload["artifacts"]:
        if canonical_json_sha256(artifact["content"]) != artifact["hash"]:
            raise CompilerContractError("compiler_prose_upstream_hash_mismatch")
    return HookResult()


def _scene_plan_schema(event: HookInput) -> HookResult:
    if event.payload["plan"].get("schema_id") != "compiler.scene-plan.v2":
        raise CompilerContractError("compiler_prose_scene_plan_v2_required")
    return HookResult()


def _continuity_binding(event: HookInput) -> HookResult:
    from casefile.agent_runtime.prose_continuity import ContinuityReview

    if event.payload["actual_binding"] != event.payload["expected_binding"]:
        raise ValueError("continuity_response_binding_invalid")
    review = ContinuityReview.model_validate(event.payload["candidate"])
    allowed = set(event.payload["allowed_scene_ids"])
    if any(set(issue.scene_ids) - allowed for issue in review.issues):
        raise ValueError("continuity_scene_ref_invalid")
    # A valid 'blocked' literary verdict remains advisory, as before.
    return HookResult()


def _scene_repair_preservation(event: HookInput) -> HookResult:
    context = event.payload["repair_context"]
    if context:
        failed_scene = context["error"].get("scene_id")
        previous = context["candidate"]["scenes"]
        for old, new in zip(previous, event.payload["proposal"]["scenes"], strict=True):
            if old["scene_id"] != failed_scene and old != new:
                raise CompilerContractError("compiler_scene_repair_preservation_failed")
    return HookResult()


def _novel_candidate(event: HookInput) -> HookResult:
    NovelCandidate.model_validate(event.payload["candidate"])
    return HookResult()


NOVEL_COMPILE_HOOKS = (
    HookBinding("novel_runtime_binding", "1", HookEvent.BEFORE_STAGE, stage="prose_prepare"),
    HookBinding("novel_upstream_hashes", "1", HookEvent.AFTER_ARTIFACT, stage="prose_upstream"),
    HookBinding("novel_scene_schema", "1", HookEvent.AFTER_ARTIFACT, stage="prose_plan"),
    HookBinding("novel_continuity_protocol", "1", HookEvent.AFTER_ARTIFACT, stage="continuity"),
    HookBinding("novel_repair_preservation", "1", HookEvent.AFTER_ARTIFACT, stage="scene_repair"),
    HookBinding("novel_candidate", "1", HookEvent.AFTER_ARTIFACT, stage="novel_candidate"),
)


def check_novel_boundary(
    stage: str,
    payload: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
) -> None:
    bindings = tuple(binding for binding in NOVEL_COMPILE_HOOKS if binding.stage == stage)
    if not bindings:
        raise ValueError(f"Unknown novel hook boundary: {stage}")
    dispatcher = SyncHookDispatcher(
        {
            ("novel_runtime_binding", "1"): _runtime_binding,
            ("novel_upstream_hashes", "1"): _upstream_artifacts,
            ("novel_scene_schema", "1"): _scene_plan_schema,
            ("novel_continuity_protocol", "1"): _continuity_binding,
            ("novel_repair_preservation", "1"): _scene_repair_preservation,
            ("novel_candidate", "1"): _novel_candidate,
        }
    )
    trace = [] if records is None else records
    try:
        dispatcher.dispatch(
            bindings,
            HookInput(bindings[0].event, stage, payload=payload),
            records=trace,
        )
    except Exception as error:
        error.__dict__["hook_records"] = list(trace)
        raise
