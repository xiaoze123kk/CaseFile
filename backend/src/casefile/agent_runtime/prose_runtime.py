"""Frozen Shadow runtime identity and optional component observation ports."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal

from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.plan_execute import (
    ExecutionPlan,
    PlanCallRecord,
    PlanCheckin,
    PlanContext,
    PlanningSummary,
    PlanReconciliationReport,
    SceneProsePlanOutput,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
    PROSE_EVIDENCE_CATALOG_POLICY_HASH,
    PROSE_JUDGE_CANDIDATE_SCHEMA_HASH,
    PROSE_JUDGE_REQUEST_PROTOCOL,
)
from casefile.agent_runtime.prose_polisher import (
    PROSE_POLISHER_COMPONENT_HASH,
    PROSE_POLISHER_MAX_OUTPUT_TOKENS,
)
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_MAX_OUTPUT_TOKENS,
)
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_CANDIDATE_SCHEMA,
    PROSE_REWRITER_COMPONENT_HASH,
    PROSE_REWRITER_MAX_OUTPUT_TOKENS,
    PROSE_REWRITER_PLAN_PROMPT_VERSION,
    PROSE_REWRITER_PLAN_SCHEMA,
    PROSE_REWRITER_PROMPT_VERSION,
)
from casefile.agent_runtime.prose_skills import assemble_skill
from casefile.agent_runtime.prose_writer import (
    PROSE_WRITER_CANDIDATE_SCHEMA,
    PROSE_WRITER_COMPONENT_HASH,
    PROSE_WRITER_MAX_OUTPUT_TOKENS,
    PROSE_WRITER_PLAN_PROMPT_VERSION,
    PROSE_WRITER_PLAN_SCHEMA,
    PROSE_WRITER_PROMPT_VERSION,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.domain.narrative_compiler.prose_checklist import PROSE_CHECKLIST_POLICY_HASH
from casefile_contracts import (
    CompileManifest,
    NovelCandidate,
    NovelProfileV2,
    ProseConsensusReport,
    ProseJudgeChecklist,
    ProseJudgeReport,
    ProseQualityReport,
    ProseRevisionDecision,
    ProseRevisionDecisionCandidate,
    SceneRender,
)

PROSE_RUNTIME_VERSION = "prose-shadow-runtime-v12"
PLAN_EXECUTE_PROSE_RUNTIME_VERSION = "prose-shadow-runtime-v13"
ComponentObserver = Callable[[str, Any], None]


def ignore_component(_name: str, _execution: Any) -> None:
    """Default observer keeps component benchmarks independent of persistence."""


ProseMode = Literal["quick_draft", "full_polish"]


def prose_runtime_binding(
    scene_count: int | None = None,
    prose_mode: ProseMode = "full_polish",
    *,
    runtime_version: str = PROSE_RUNTIME_VERSION,
) -> dict[str, Any]:
    """Freeze executable policies and prompt contents without credentials."""
    if prose_mode not in {"quick_draft", "full_polish"}:
        raise ValueError("compiler_prose_mode_invalid")
    if runtime_version not in {
        "prose-shadow-runtime-v11",
        PROSE_RUNTIME_VERSION,
        PLAN_EXECUTE_PROSE_RUNTIME_VERSION,
    }:
        raise ValueError("compiler_prose_runtime_version_unsupported")
    legacy = runtime_version == "prose-shadow-runtime-v11"
    plan_execute = runtime_version == PLAN_EXECUTE_PROSE_RUNTIME_VERSION
    quick = prose_mode == "quick_draft"
    schema_models: list[type[Any]] = [
        NovelProfileV2,
        ProseJudgeChecklist,
        SceneRender,
        ProseJudgeReport,
        ProseConsensusReport,
        ProseQualityReport,
        ProseRevisionDecision,
        ProseRevisionDecisionCandidate,
        CompileManifest,
        NovelCandidate,
    ]
    if plan_execute:
        schema_models.extend(
            [
                ExecutionPlan,
                PlanContext,
                PlanCheckin,
                PlanCallRecord,
                PlanReconciliationReport,
                PlanningSummary,
                SceneProsePlanOutput,
            ]
        )
    versions = {
        "prose_continuity": "prose-continuity-v1",
        "prose_writer": (
            "prose-writer-v4"
            if legacy
            else PROSE_WRITER_PLAN_PROMPT_VERSION
            if plan_execute
            else PROSE_WRITER_PROMPT_VERSION
        ),
        "prose_fidelity_judge": load_prompt("prose_fidelity_judge").version,
        "prose_adversarial_judge": load_prompt("prose_adversarial_judge").version,
        "prose_coherence_judge": load_prompt("prose_coherence_judge").version,
        "prose_arbiter": load_prompt("prose_arbiter").version,
        "prose_rewriter": (
            "prose-rewriter-v7"
            if legacy
            else PROSE_REWRITER_PLAN_PROMPT_VERSION
            if plan_execute
            else PROSE_REWRITER_PROMPT_VERSION
        ),
        "prose_revision": "prose-revision-v3",
        **({"prose_plan_reconciliation": "prose-plan-reconciliation-v1"} if plan_execute else {}),
        "prose_quality_critic": "prose-quality-critic-v1",
        "prose_quality_pairwise": "prose-quality-pairwise-v1",
        "prose_polisher": "prose-polisher-v5",
    }
    binding = {
        "version": runtime_version,
        "prose_mode": prose_mode,
        "scene_count": scene_count,
        "max_logical_calls": (
            None
            if scene_count is None
            else (2 if quick else 23) * scene_count + (1 if plan_execute else 0)
        ),
        "schema_hashes": {
            model.__name__: canonical_json_sha256(model.model_json_schema())
            for model in schema_models
        },
        "provider": "deepseek",
        "generation_model": DEEPSEEK_MODEL_ID,
        "quality_model": DEEPSEEK_MODEL_ID,
        "semantic_policy": FIDELITY_ONLY_POLICY.descriptor(),
        "semantic_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "preservation_policy": FIDELITY_ONLY_POLICY.descriptor(),
        "preservation_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "prompts": {
            name: {"version": version, "hash": load_prompt(name, version).system_prompt_sha256}
            for name, version in versions.items()
        },
        "components": {
            "writer": PROSE_WRITER_COMPONENT_HASH,
            "rewrite": PROSE_REWRITER_COMPONENT_HASH,
            "polisher": PROSE_POLISHER_COMPONENT_HASH,
            "quality": PROSE_QUALITY_COMPONENT_HASH,
            "checklist": PROSE_CHECKLIST_POLICY_HASH,
            "judge": canonical_json_sha256(
                {
                    "protocol": PROSE_JUDGE_REQUEST_PROTOCOL,
                    "candidate_schema": PROSE_JUDGE_CANDIDATE_SCHEMA_HASH,
                    "evidence_policy": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
                }
            ),
        },
        "generation_parameters": {
            "writer_max_output_tokens": PROSE_WRITER_MAX_OUTPUT_TOKENS,
            "rewrite_max_output_tokens": PROSE_REWRITER_MAX_OUTPUT_TOKENS,
            "polisher_max_output_tokens": PROSE_POLISHER_MAX_OUTPUT_TOKENS,
            "quality_max_output_tokens": PROSE_QUALITY_MAX_OUTPUT_TOKENS,
            "judge_max_output_tokens": PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
            "max_turns": 1,
            "temperature": 0,
            "thinking_enabled": False,
        },
        "limits": {
            "continuity_reviews_per_scene": 0 if quick else 1,
            "continuity_counts_toward_judge_budget": True,
            "scene_checkpoint_policy": "accepted-prefix-v1",
            "generation_repairs_per_call": 1,
            "generation_policy": "prose-generation-repair-v5",
            "generation_length_policy": "hard-range-before-judging",
            "judge_calls_per_scene": 0 if quick else 3,
            "protocol_repairs_per_call": 1,
            "judge_evidence_per_check": 64,
            "rewrite_rounds": 0 if quick else 2,
            "revision_decisions_per_scene": 0 if quick else 3,
            "delivery_mode": "product",
            "no_progress_policy": "semantic-exhaustion-no-transport-resume",
            "arbiter_per_round": 1,
            "judge_network_retries": 0,
            "other_network_retries": 0,
            "logical_calls_per_scene": 2 if quick else 23,
            **(
                {"plan_reconciliation_calls": 1, "nag_threshold": 2}
                if plan_execute
                else {}
            ),
            "cost_limit": None,
        },
    }
    if not legacy:
        binding["skills"] = {
            name: assemble_skill(
                SimpleNamespace(
                    prompt_version=versions[name],
                    system_prompt=load_prompt(name, versions[name]).system_prompt,
                ),
                schema,
            )[1]
            for name, schema in (
                (
                    "prose_writer",
                    PROSE_WRITER_PLAN_SCHEMA
                    if plan_execute
                    else PROSE_WRITER_CANDIDATE_SCHEMA,
                ),
                (
                    "prose_rewriter",
                    PROSE_REWRITER_PLAN_SCHEMA
                    if plan_execute
                    else PROSE_REWRITER_CANDIDATE_SCHEMA,
                ),
            )
        }
    return binding


def matches_prose_runtime(
    binding: dict[str, Any], scene_count: int | None, prose_mode: ProseMode = "full_polish"
) -> bool:
    try:
        return binding == prose_runtime_binding(
            scene_count, prose_mode, runtime_version=binding.get("version", "")
        )
    except ValueError:
        return False


def prose_runtime_hash() -> str:
    return canonical_json_sha256(prose_runtime_binding())
