"""Frozen Shadow runtime identity and optional component observation ports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from casefile_contracts import (
    CompileManifest,
    NovelCandidate,
    NovelProfileV2,
    ProseConsensusReport,
    ProseJudgeChecklist,
    ProseJudgeReport,
    ProseQualityReport,
    SceneRender,
)

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    FULL_COUNCIL_POLICY,
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
    PROSE_REWRITER_COMPONENT_HASH,
    PROSE_REWRITER_MAX_OUTPUT_TOKENS,
)
from casefile.agent_runtime.prose_writer import (
    PROSE_WRITER_COMPONENT_HASH,
    PROSE_WRITER_MAX_OUTPUT_TOKENS,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.domain.narrative_compiler.prose_checklist import PROSE_CHECKLIST_POLICY_HASH

PROSE_RUNTIME_VERSION = "prose-shadow-runtime-v1"
ComponentObserver = Callable[[str, Any], None]


def ignore_component(_name: str, _execution: Any) -> None:
    """Default observer keeps component benchmarks independent of persistence."""


def prose_runtime_binding(scene_count: int | None = None) -> dict[str, Any]:
    """Freeze executable policies and prompt contents without credentials."""
    versions = {
        "prose_writer": "prose-writer-v1",
        "prose_fidelity_judge": "prose-fidelity-judge-v6",
        "prose_adversarial_judge": "prose-adversarial-judge-v5",
        "prose_coherence_judge": "prose-coherence-judge-v5",
        "prose_arbiter": "prose-arbiter-v5",
        "prose_rewriter": "prose-rewriter-v3",
        "prose_quality_critic": "prose-quality-critic-v1",
        "prose_quality_pairwise": "prose-quality-pairwise-v1",
        "prose_polisher": "prose-polisher-v2",
    }
    return {
        "version": PROSE_RUNTIME_VERSION,
        "scene_count": scene_count,
        "max_logical_calls": None if scene_count is None else 23 * scene_count,
        "schema_hashes": {
            model.__name__: canonical_json_sha256(model.model_json_schema())
            for model in (
                NovelProfileV2,
                ProseJudgeChecklist,
                SceneRender,
                ProseJudgeReport,
                ProseConsensusReport,
                ProseQualityReport,
                CompileManifest,
                NovelCandidate,
            )
        },
        "provider": "deepseek",
        "generation_model": "deepseek-v4-pro",
        "quality_model": "deepseek-v4-flash",
        "semantic_policy": FIDELITY_ONLY_POLICY.descriptor(),
        "semantic_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "preservation_policy": FULL_COUNCIL_POLICY.descriptor(),
        "preservation_policy_hash": FULL_COUNCIL_POLICY.policy_hash,
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
            "rewrite_rounds": 2,
            "arbiter_per_round": 1,
            "judge_network_retries": 1,
            "other_network_retries": 0,
            "logical_calls_per_scene": 23,
            "cost_limit": None,
        },
    }


def prose_runtime_hash() -> str:
    return canonical_json_sha256(prose_runtime_binding())
