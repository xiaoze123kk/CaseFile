"""Opt-in four-call Quality experiment; never a production default."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    PROSE_EVIDENCE_CATALOG_POLICY_HASH,
    build_server_evidence_catalog,
)
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA,
    MirroredQualityExecution,
    PositionIdentity,
    ProseQualityCriticProvider,
    ProseQualityInfrastructureError,
    ProseQualityProtocolError,
    ProseQualityProviderResult,
    ProseQualityRequest,
    execute_quality_request,
    quality_pairwise_report_from_candidate,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    canonical_json_sha256,
    resolve_mirrored_quality,
    validate_quality_pair_inputs,
    validate_semantic_acceptance,
)

ASSESSMENT_PROMPT: Final = "prose-quality-critic-v2"
COMPARISON_PROMPT: Final = "prose-quality-pairwise-v3"
DIAGNOSTIC_PROTOCOL: Final = "prose-quality-independent-assessment-v1"
DIAGNOSTIC_PARAMETERS: Final = {
    "model_id": PROSE_QUALITY_MODEL_ID,
    "temperature": 0,
    "thinking_enabled": False,
    "max_output_tokens": 8192,
    "max_turns": 1,
    "network_retries": 0,
}


class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dimension: Literal[
        "pov_voice_consistency",
        "scene_specificity",
        "dialogue_narration_naturalness",
        "dramatic_progression_pacing",
        "readability_editability",
    ]
    severity: Literal["none", "low", "medium", "high"]
    observation: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(max_length=20)


class SingleRenderAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_id: Literal["compiler.prose-quality-single-assessment.v1"]
    dimensions: list[DimensionAssessment] = Field(min_length=5, max_length=5)


ASSESSMENT_SCHEMA: Final = SingleRenderAssessment.model_json_schema()


def diagnostic_component() -> dict[str, Any]:
    """Freeze schemas, prompts, parameters, evidence and comparison policy."""
    value = {
        "component_version": "prose-quality-diagnostic-runtime-v1",
        "protocol": DIAGNOSTIC_PROTOCOL,
        "parameters": DIAGNOSTIC_PARAMETERS,
        "assessment_schema_hash": canonical_json_sha256(ASSESSMENT_SCHEMA),
        "comparison_schema_hash": canonical_json_sha256(PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA),
        "assessment_prompt_hash": load_prompt(
            "prose_quality_critic", ASSESSMENT_PROMPT
        ).system_prompt_sha256,
        "comparison_prompt_hash": load_prompt(
            "prose_quality_pairwise", COMPARISON_PROMPT
        ).system_prompt_sha256,
        "assessment_prompt_version": ASSESSMENT_PROMPT,
        "comparison_prompt_version": COMPARISON_PROMPT,
        "evidence_policy_hash": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
        "selection": "two-position-polished-win-no-dimension-regression-v1",
        "upstream": "two-independent-single-renders-reused-within-trial-only",
    }
    return {**value, "component_hash": canonical_json_sha256(value)}


def anonymous_quality_render(render: dict[str, Any]) -> dict[str, Any]:
    """Give blocks request-local IDs before producing any evidence catalog."""
    return {
        "blocks": [
            {"block_id": f"block_{index:03d}", "text": block["text"]}
            for index, block in enumerate(render["blocks"], start=1)
        ]
    }


def validate_single_assessment(
    candidate: dict[str, Any] | None,
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require exact five-dimension coverage and references to this render only."""
    try:
        result = SingleRenderAssessment.model_validate(candidate).model_dump(mode="json")
    except ValidationError as error:
        raise ProseQualityProtocolError("quality_diagnostic_assessment_invalid") from error
    if [item["dimension"] for item in result["dimensions"]] != list(QUALITY_DIMENSIONS):
        raise ProseQualityProtocolError("quality_diagnostic_dimension_coverage_invalid")
    allowed = {item["evidence_id"] for item in catalog}
    for item in result["dimensions"]:
        ids = item["evidence_ids"]
        if (
            len(ids) != len(set(ids))
            or not set(ids) <= allowed
            or (item["severity"] != "none" and not ids)
            or not item["observation"].strip()
        ):
            raise ProseQualityProtocolError("quality_diagnostic_evidence_invalid")
    return result


def build_diagnostic_request(
    *,
    kind: Literal["assessment", "pairwise"],
    payload: dict[str, Any],
    binding: dict[str, Any],
    api_key: str,
    position_mapping: dict[str, PositionIdentity] | None = None,
) -> ProseQualityRequest:
    """Keep identity bindings server-side; send only anonymous task data."""
    single = kind == "assessment"
    prompt = load_prompt(
        "prose_quality_critic" if single else "prose_quality_pairwise",
        ASSESSMENT_PROMPT if single else COMPARISON_PROMPT,
    )
    schema = ASSESSMENT_SCHEMA if single else PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA
    input_hash = canonical_json_sha256(payload)
    component_hash = diagnostic_component()["component_hash"]
    bound_hash = canonical_json_sha256(
        {
            **binding,
            "component_hash": component_hash,
            "input_hash": input_hash,
            "schema_hash": canonical_json_sha256(schema),
            "position_mapping": position_mapping,
        }
    )
    fingerprint = canonical_json_sha256(
        {
            "protocol": DIAGNOSTIC_PROTOCOL,
            "component_hash": component_hash,
            "component_input_hash": bound_hash,
            "prompt_hash": prompt.system_prompt_sha256,
            "prompt_version": prompt.version,
            "kind": kind,
            **DIAGNOSTIC_PARAMETERS,
        }
    )
    return ProseQualityRequest(
        request_kind=kind,
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        component_input_hash=bound_hash,
        request_fingerprint=fingerprint,
        position_mapping=position_mapping,
        candidate_schema=schema,
    )


def execute_diagnostic_quality(
    provider: ProseQualityCriticProvider,
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    original_consensus: dict[str, Any],
    preservation_consensus: dict[str, Any],
    profile: dict[str, Any],
    api_key: str,
) -> MirroredQualityExecution:
    """Assess each side in isolation, then perform two blind comparisons."""
    calls: list[ProseQualityProviderResult] = []
    reports: list[dict[str, Any]] = []
    try:
        original, polished = validate_quality_pair_inputs(
            checklist=checklist,
            original_render=original_render,
            polished_render=polished_render,
            profile=profile,
            preservation_consensus=preservation_consensus,
        )
        validate_semantic_acceptance(
            original_consensus, checklist=checklist, render=original, profile=profile
        )
        by_identity = {"original": original, "polished": polished}
        views = {
            identity: anonymous_quality_render(render) for identity, render in by_identity.items()
        }
        catalogs = {
            identity: build_server_evidence_catalog(view) for identity, view in views.items()
        }
        assessments = {}
        binding = {
            "render_hashes": [canonical_json_sha256(original), canonical_json_sha256(polished)],
            "profile_hash": canonical_json_sha256(profile),
            "checklist_hash": canonical_json_sha256(checklist),
            "consensus_hashes": [
                canonical_json_sha256(original_consensus),
                canonical_json_sha256(preservation_consensus),
            ],
        }
        for identity in ("original", "polished"):
            request = build_diagnostic_request(
                kind="assessment",
                api_key=api_key,
                binding={
                    **binding,
                    "assessed_render_hash": canonical_json_sha256(by_identity[identity]),
                },
                payload={
                    "untrusted_data": {"profile": profile["prose"], "render": views[identity]},
                    "server_evidence_catalog": catalogs[identity],
                    "quality_dimensions": list(QUALITY_DIMENSIONS),
                    "output_schema_id": "compiler.prose-quality-single-assessment.v1",
                },
            )
            call = execute_quality_request(provider, request, None)
            calls.append(call)
            assessments[identity] = validate_single_assessment(call.candidate, catalogs[identity])
        mappings: tuple[dict[str, PositionIdentity], ...] = (
            {"a": "original", "b": "polished"},
            {"a": "polished", "b": "original"},
        )
        for mapping in mappings:
            request = build_diagnostic_request(
                kind="pairwise",
                api_key=api_key,
                position_mapping=mapping,
                binding={
                    **binding,
                    "assessment_hashes": [
                        canonical_json_sha256(assessments[key]) for key in ("original", "polished")
                    ],
                },
                payload={
                    "untrusted_data": {
                        "profile": profile["prose"],
                        **{
                            side: {
                                "render": views[identity],
                                "assessment": assessments[identity],
                                "evidence_catalog": catalogs[identity],
                            }
                            for side, identity in mapping.items()
                        },
                    },
                    "quality_dimensions": list(QUALITY_DIMENSIONS),
                    "output_schema_id": "compiler.prose-quality-pairwise-candidate.v1",
                },
            )
            call = execute_quality_request(provider, request, None)
            calls.append(call)
            reports.append(
                quality_pairwise_report_from_candidate(
                    call,
                    checklist=checklist,
                    original_render=original,
                    polished_render=polished,
                    profile=profile,
                    preservation_consensus=preservation_consensus,
                    position_mapping=mapping,
                )
            )
        return MirroredQualityExecution(
            "completed", tuple(reports), tuple(calls), resolve_mirrored_quality(*reports)
        )
    except ProseQualityInfrastructureError as error:
        return MirroredQualityExecution(
            "inconclusive", tuple(reports), tuple(calls), None, error.failed_call, str(error)
        )
    except (CompilerContractError, ProseQualityProtocolError) as error:
        return MirroredQualityExecution(
            "protocol_failed", tuple(reports), tuple(calls), None, error_code=str(error)
        )
