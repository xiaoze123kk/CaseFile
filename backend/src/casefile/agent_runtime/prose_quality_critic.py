"""Provider-neutral N4.5 Quality Critic and mirrored pairwise runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol

from casefile_contracts import ProseQualityReport
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    PROSE_EVIDENCE_CATALOG_POLICY_HASH,
    PROSE_EVIDENCE_CATALOG_VERSION,
    build_server_evidence_catalog,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    MirroredQualityDecision,
    canonical_json_sha256,
    resolve_mirrored_quality,
    validate_quality_findings_report,
    validate_quality_pair_inputs,
    validate_quality_pairwise_report,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_QUALITY_MODEL_ID: Final = "deepseek-v4-flash"
PROSE_QUALITY_FINDINGS_PROMPT_VERSION: Final = "prose-quality-critic-v1"
PROSE_QUALITY_PAIRWISE_PROMPT_VERSION: Final = "prose-quality-pairwise-v1"
PROSE_QUALITY_REQUEST_PROTOCOL: Final = "prose-quality-json-object-v1"
PROSE_QUALITY_COMPONENT_VERSION: Final = "prose-quality-critic-runtime-v1"
PROSE_QUALITY_MAX_TURNS: Final = 1
PROSE_QUALITY_NETWORK_RETRIES: Final = 0
PROSE_QUALITY_TEMPERATURE: Final = 0
PROSE_QUALITY_MAX_OUTPUT_TOKENS: Final = 8192
PROSE_QUALITY_THINKING_ENABLED: Final = False

QualityRequestKind = Literal["findings", "pairwise"]
PositionIdentity = Literal["original", "polished"]


class _QualityFindingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "pov_voice_consistency",
        "scene_specificity",
        "dialogue_narration_naturalness",
        "dramatic_progression_pacing",
        "readability_editability",
    ]
    severity: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=1000)


class _QualityFindingsCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: Literal["compiler.prose-quality-findings-candidate.v1"]
    findings: list[_QualityFindingCandidate]


class _DimensionPreferenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "pov_voice_consistency",
        "scene_specificity",
        "dialogue_narration_naturalness",
        "dramatic_progression_pacing",
        "readability_editability",
    ]
    preference: Literal["a", "b", "tie"]


class _QualityPairwiseCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: Literal["compiler.prose-quality-pairwise-candidate.v1"]
    overall_preference: Literal["a", "b", "tie"]
    dimension_preferences: list[_DimensionPreferenceCandidate] = Field(
        min_length=5, max_length=5
    )


PROSE_QUALITY_REPORT_SCHEMA_HASH: Final = canonical_json_sha256(
    ProseQualityReport.model_json_schema()
)
PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA: Final = (
    _QualityFindingsCandidate.model_json_schema()
)
PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA
)
PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA: Final = (
    _QualityPairwiseCandidate.model_json_schema()
)
PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA
)
PROSE_QUALITY_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_QUALITY_COMPONENT_VERSION,
        "request_protocol": PROSE_QUALITY_REQUEST_PROTOCOL,
        "findings_candidate_schema_hash": PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA_HASH,
        "pairwise_candidate_schema_hash": PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA_HASH,
        "report_schema_hash": PROSE_QUALITY_REPORT_SCHEMA_HASH,
        "evidence_catalog_version": PROSE_EVIDENCE_CATALOG_VERSION,
        "evidence_catalog_policy_hash": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "mirrored_selection": "two-position-polished-win-no-dimension-regression-v1",
    }
)


class ProseQualityError(RuntimeError):
    """Base error for Quality Critic execution."""


class ProseQualityProtocolError(ProseQualityError):
    """The request, response, evidence, or recovery binding was invalid."""


class ProseQualityInfrastructureError(ProseQualityError):
    """A Provider failure made the quality result inconclusive."""

    def __init__(
        self, message: str, *, failed_call: ProseQualityFailedCall | None = None
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProseQualityTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProseQualityRequest:
    request_kind: QualityRequestKind
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    position_mapping: dict[str, PositionIdentity] | None
    max_turns: int = PROSE_QUALITY_MAX_TURNS
    network_retries: int = PROSE_QUALITY_NETWORK_RETRIES
    temperature: int = PROSE_QUALITY_TEMPERATURE
    max_output_tokens: int = PROSE_QUALITY_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_QUALITY_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseQualityProviderResult:
    candidate: dict[str, Any] | None
    raw_response: str
    usage: dict[str, int]
    latency_ms: int
    request_kind: QualityRequestKind
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    output_hash: str
    model_id: str
    prompt_version: str
    request_payload: dict[str, Any]
    transport_attempts: tuple[ProseQualityTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProseQualityFailedCall:
    request_kind: QualityRequestKind
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    error_code: str
    transport_attempts: tuple[ProseQualityTransportAttempt, ...]


class ProseQualityCriticProvider(Protocol):
    def assess_quality(
        self, request: ProseQualityRequest
    ) -> ProseQualityProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProseQualityExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    report: dict[str, Any] | None
    call: ProseQualityProviderResult | None
    failed_call: ProseQualityFailedCall | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MirroredQualityExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    reports: tuple[dict[str, Any], ...]
    calls: tuple[ProseQualityProviderResult, ...]
    decision: MirroredQualityDecision | None
    failed_call: ProseQualityFailedCall | None = None
    error_code: str | None = None


class DeepSeekProseQualityCriticProvider:
    """One-turn DeepSeek JSON-object adapter with hidden retries disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def assess_quality(self, request: ProseQualityRequest) -> ProseQualityProviderResult:
        if not request.api_key:
            raise ProseQualityInfrastructureError("prose_quality_api_key_missing")
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_quality_provider_failed:{type(error).__name__}"
            attempt = ProseQualityTransportAttempt(
                1,
                "failed",
                max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code,
                False,
                None,
            )
            raise ProseQualityInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        usage = _response_usage(response)
        attempt = ProseQualityTransportAttempt(
            1,
            "completed",
            max(0, round((perf_counter() - attempt_started) * 1000)),
            None,
            True,
            usage if response.usage is not None else None,
        )
        raw = ""
        candidate: dict[str, Any] | None = None
        if len(response.choices) == 1 and response.choices[0].message.content:
            raw = response.choices[0].message.content or ""
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            candidate = parsed if isinstance(parsed, dict) else None
        return ProseQualityProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage=usage,
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            request_kind=request.request_kind,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            component_input_hash=request.component_input_hash,
            output_hash=sha256(raw.encode("utf-8")).hexdigest(),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
            transport_attempts=(attempt,),
        )

    def _create_completion(self, request: ProseQualityRequest) -> Any:
        schema = (
            PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA
            if request.request_kind == "findings"
            else PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA
        )
        client = OpenAI(api_key=request.api_key, base_url=self.base_url, max_retries=0)
        try:
            return client.chat.completions.create(
                model=request.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": request.system_prompt
                        + "\n\n必须严格遵守以下 JSON Schema：\n"
                        + json.dumps(
                            schema,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            request.input_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        finally:
            client.close()


class FakeProseQualityCriticProvider:
    """Deterministic queued Quality candidates for zero-network tests."""

    def __init__(
        self,
        *,
        findings_candidates: tuple[dict[str, Any], ...] = (),
        pairwise_candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._findings = deque(findings_candidates)
        self._pairwise = deque(pairwise_candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0

    def assess_quality(self, request: ProseQualityRequest) -> ProseQualityProviderResult:
        self.call_count += 1
        if self.call_count == self._failure_at_call:
            raise ProseQualityInfrastructureError("prose_quality_fake_infrastructure")
        queue = self._findings if request.request_kind == "findings" else self._pairwise
        candidate = queue.popleft() if queue else None
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if candidate else ""
        usage = _zero_usage()
        return ProseQualityProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage=usage,
            latency_ms=0,
            request_kind=request.request_kind,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            component_input_hash=request.component_input_hash,
            output_hash=sha256(raw.encode("utf-8")).hexdigest(),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
            transport_attempts=(
                ProseQualityTransportAttempt(1, "completed", 0, None, True, usage),
            ),
        )


def execute_quality_findings(
    provider: ProseQualityCriticProvider,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    model_id: str,
    api_key: str,
    recover_call: Callable[[str], ProseQualityProviderResult | None] | None = None,
) -> ProseQualityExecution:
    """Run one findings call after exact semantic-acceptance validation."""

    call: ProseQualityProviderResult | None = None
    try:
        request, render_json, catalog = _build_findings_request(
            checklist=checklist,
            render=render,
            profile=profile,
            semantic_consensus=semantic_consensus,
            model_id=model_id,
            api_key=api_key,
        )
        call = _execute_call(provider, request, recover_call)
        report = _findings_report_from_candidate(
            call,
            checklist=checklist,
            render=render_json,
            profile=profile,
            semantic_consensus=semantic_consensus,
            evidence_catalog=catalog,
        )
    except ProseQualityInfrastructureError as error:
        return ProseQualityExecution(
            "inconclusive", None, call, error.failed_call, str(error)
        )
    except (CompilerContractError, ProseQualityProtocolError) as error:
        return ProseQualityExecution("protocol_failed", None, call, error_code=str(error))
    return ProseQualityExecution("completed", report, call)


def execute_mirrored_pairwise_quality(
    provider: ProseQualityCriticProvider,
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    preservation_consensus: dict[str, Any],
    model_id: str,
    api_key: str,
    recover_call: Callable[[str], ProseQualityProviderResult | None] | None = None,
) -> MirroredQualityExecution:
    """Run exactly two opposite-position blind comparisons and select safely."""

    reports: list[dict[str, Any]] = []
    calls: list[ProseQualityProviderResult] = []
    mappings: tuple[dict[str, PositionIdentity], ...] = (
        {"a": "original", "b": "polished"},
        {"a": "polished", "b": "original"},
    )
    try:
        for mapping in mappings:
            request, original, polished = _build_pairwise_request(
                checklist=checklist,
                original_render=original_render,
                polished_render=polished_render,
                profile=profile,
                preservation_consensus=preservation_consensus,
                position_mapping=mapping,
                model_id=model_id,
                api_key=api_key,
            )
            call = _execute_call(provider, request, recover_call)
            calls.append(call)
            reports.append(
                _pairwise_report_from_candidate(
                    call,
                    checklist=checklist,
                    original_render=original,
                    polished_render=polished,
                    profile=profile,
                    preservation_consensus=preservation_consensus,
                    position_mapping=mapping,
                )
            )
        decision = resolve_mirrored_quality(reports[0], reports[1])
    except ProseQualityInfrastructureError as error:
        return MirroredQualityExecution(
            "inconclusive", tuple(reports), tuple(calls), None, error.failed_call, str(error)
        )
    except (CompilerContractError, ProseQualityProtocolError) as error:
        return MirroredQualityExecution(
            "protocol_failed", tuple(reports), tuple(calls), None, error_code=str(error)
        )
    return MirroredQualityExecution(
        "completed", tuple(reports), tuple(calls), decision
    )


def _build_findings_request(
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    model_id: str,
    api_key: str,
) -> tuple[ProseQualityRequest, dict[str, Any], list[dict[str, Any]]]:
    _validate_model(model_id)
    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile
    ).model_dump(mode="json")
    consensus_json = validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render_json,
        profile=profile,
    ).model_dump(mode="json")
    catalog = build_server_evidence_catalog(render_json)
    prompt = load_prompt("prose_quality_critic", PROSE_QUALITY_FINDINGS_PROMPT_VERSION)
    binding = {
        "component_id": "prose_quality_critic",
        "component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "request_kind": "findings",
        "scene_id": render_json["scene_id"],
        "render_hash": canonical_json_sha256(render_json),
        "semantic_consensus_hash": canonical_json_sha256(consensus_json),
        "profile_hash": canonical_json_sha256(profile),
        "candidate_schema_hash": PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA_HASH,
        "report_schema_hash": PROSE_QUALITY_REPORT_SCHEMA_HASH,
        "evidence_catalog_version": PROSE_EVIDENCE_CATALOG_VERSION,
        "evidence_catalog_policy_hash": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
        "prompt_hash": prompt.system_prompt_sha256,
        "model_id": model_id,
    }
    component_input_hash = canonical_json_sha256(binding)
    payload = {
        "server_bindings": {**binding, "component_input_hash": component_input_hash},
        "server_evidence_catalog": catalog,
        "untrusted_data": {
            "profile": profile,
            "checklist": checklist,
            "render": render_json,
        },
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "output_schema_id": "compiler.prose-quality-findings-candidate.v1",
    }
    return (
        _request(
            "findings",
            prompt.system_prompt,
            prompt.version,
            prompt.system_prompt_sha256,
            payload,
            component_input_hash,
            model_id,
            api_key,
            None,
        ),
        render_json,
        catalog,
    )


def _build_pairwise_request(
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    preservation_consensus: dict[str, Any],
    position_mapping: dict[str, PositionIdentity],
    model_id: str,
    api_key: str,
) -> tuple[ProseQualityRequest, dict[str, Any], dict[str, Any]]:
    _validate_model(model_id)
    original, polished = validate_quality_pair_inputs(
        checklist=checklist,
        original_render=original_render,
        polished_render=polished_render,
        profile=profile,
        preservation_consensus=preservation_consensus,
    )
    original_hash = canonical_json_sha256(original)
    polished_hash = canonical_json_sha256(polished)
    prompt = load_prompt("prose_quality_pairwise", PROSE_QUALITY_PAIRWISE_PROMPT_VERSION)
    binding = {
        "component_id": "prose_quality_critic",
        "component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "request_kind": "pairwise",
        "source_render_hashes": [original_hash, polished_hash],
        "preservation_consensus_hash": canonical_json_sha256(preservation_consensus),
        "candidate_schema_hash": PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA_HASH,
        "report_schema_hash": PROSE_QUALITY_REPORT_SCHEMA_HASH,
        "prompt_hash": prompt.system_prompt_sha256,
        "model_id": model_id,
        "position_mapping": position_mapping,
    }
    component_input_hash = canonical_json_sha256(binding)
    by_identity = {"original": original, "polished": polished}
    payload = {
        "server_bindings": {
            "component_id": "prose_quality_critic",
            "component_hash": PROSE_QUALITY_COMPONENT_HASH,
            "request_kind": "pairwise",
            "component_input_hash": component_input_hash,
            "candidate_schema_hash": PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA_HASH,
            "report_schema_hash": PROSE_QUALITY_REPORT_SCHEMA_HASH,
            "prompt_hash": prompt.system_prompt_sha256,
            "model_id": model_id,
        },
        "untrusted_data": {
            "profile": profile["prose"],
            "a": _anonymous_render(by_identity[position_mapping["a"]]),
            "b": _anonymous_render(by_identity[position_mapping["b"]]),
        },
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "output_schema_id": "compiler.prose-quality-pairwise-candidate.v1",
    }
    return (
        _request(
            "pairwise",
            prompt.system_prompt,
            prompt.version,
            prompt.system_prompt_sha256,
            payload,
            component_input_hash,
            model_id,
            api_key,
            position_mapping,
        ),
        original,
        polished,
    )


def _request(
    kind: QualityRequestKind,
    system_prompt: str,
    prompt_version: str,
    prompt_hash: str,
    payload: dict[str, Any],
    component_input_hash: str,
    model_id: str,
    api_key: str,
    position_mapping: dict[str, PositionIdentity] | None,
) -> ProseQualityRequest:
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_QUALITY_REQUEST_PROTOCOL,
            "component_hash": PROSE_QUALITY_COMPONENT_HASH,
            "request_kind": kind,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_QUALITY_MAX_TURNS,
            "network_retries": PROSE_QUALITY_NETWORK_RETRIES,
            "temperature": PROSE_QUALITY_TEMPERATURE,
            "max_output_tokens": PROSE_QUALITY_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_QUALITY_THINKING_ENABLED,
        }
    )
    return ProseQualityRequest(
        kind,
        model_id,
        api_key,
        system_prompt,
        prompt_version,
        prompt_hash,
        payload,
        input_hash,
        component_input_hash,
        fingerprint,
        position_mapping,
    )


def _findings_report_from_candidate(
    result: ProseQualityProviderResult,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    evidence_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    if result.candidate is None:
        raise ProseQualityProtocolError("prose_quality_empty_or_invalid_json")
    try:
        candidate = _QualityFindingsCandidate.model_validate(
            result.candidate
        ).model_dump(mode="json")
    except ValidationError as error:
        raise ProseQualityProtocolError("prose_quality_findings_candidate_invalid") from error
    catalog = {item["evidence_id"]: item for item in evidence_catalog}
    findings: list[dict[str, Any]] = []
    for item in candidate["findings"]:
        evidence_ids = item["evidence_ids"]
        if len(evidence_ids) != len(set(evidence_ids)) or any(
            evidence_id not in catalog for evidence_id in evidence_ids
        ):
            raise ProseQualityProtocolError("prose_quality_evidence_catalog_mismatch")
        findings.append(
            {
                "dimension": item["dimension"],
                "severity": item["severity"],
                "evidence": [
                    {
                        key: value
                        for key, value in catalog[evidence_id].items()
                        if key != "evidence_id"
                    }
                    for evidence_id in evidence_ids
                ],
                "description": item["description"],
            }
        )
    report = {
        "schema_id": "compiler.prose-quality-report.v1",
        "report_kind": "findings",
        "scene_id": render["scene_id"],
        "source_render_hashes": [canonical_json_sha256(render)],
        "position_mapping": None,
        "findings": findings,
        "overall_preference": None,
        "dimension_preferences": [],
    }
    return validate_quality_findings_report(
        report,
        checklist=checklist,
        render=render,
        profile=profile,
        semantic_consensus=semantic_consensus,
    ).model_dump(mode="json")


def _pairwise_report_from_candidate(
    result: ProseQualityProviderResult,
    *,
    checklist: dict[str, Any],
    original_render: dict[str, Any],
    polished_render: dict[str, Any],
    profile: dict[str, Any],
    preservation_consensus: dict[str, Any],
    position_mapping: dict[str, PositionIdentity],
) -> dict[str, Any]:
    if result.candidate is None:
        raise ProseQualityProtocolError("prose_quality_empty_or_invalid_json")
    try:
        candidate = _QualityPairwiseCandidate.model_validate(
            result.candidate
        ).model_dump(mode="json")
    except ValidationError as error:
        raise ProseQualityProtocolError("prose_quality_pairwise_candidate_invalid") from error
    if [item["dimension"] for item in candidate["dimension_preferences"]] != list(
        QUALITY_DIMENSIONS
    ):
        raise ProseQualityProtocolError("prose_quality_dimension_coverage_mismatch")
    report = {
        "schema_id": "compiler.prose-quality-report.v1",
        "report_kind": "pairwise",
        "scene_id": original_render["scene_id"],
        "source_render_hashes": [
            canonical_json_sha256(original_render),
            canonical_json_sha256(polished_render),
        ],
        "position_mapping": position_mapping,
        "findings": [],
        "overall_preference": candidate["overall_preference"],
        "dimension_preferences": candidate["dimension_preferences"],
    }
    return validate_quality_pairwise_report(
        report,
        checklist=checklist,
        original_render=original_render,
        polished_render=polished_render,
        profile=profile,
        preservation_consensus=preservation_consensus,
        position_mapping=position_mapping,
    ).model_dump(mode="json")


def _execute_call(
    provider: ProseQualityCriticProvider,
    request: ProseQualityRequest,
    recover_call: Callable[[str], ProseQualityProviderResult | None] | None,
) -> ProseQualityProviderResult:
    recovered = recover_call(request.request_fingerprint) if recover_call else None
    result = (
        replace(recovered, recovered=True)
        if recovered is not None
        else provider.assess_quality(request)
    )
    if (
        result.request_kind != request.request_kind
        or result.request_fingerprint != request.request_fingerprint
        or result.prompt_hash != request.prompt_hash
        or result.input_hash != request.input_hash
        or result.component_input_hash != request.component_input_hash
        or result.model_id != request.model_id
        or result.prompt_version != request.prompt_version
        or canonical_json_sha256(result.request_payload) != request.input_hash
    ):
        raise ProseQualityProtocolError("prose_quality_recovery_fingerprint_mismatch")
    return result


def _failed_call_from_request(
    request: ProseQualityRequest,
    error_code: str,
    attempts: tuple[ProseQualityTransportAttempt, ...],
) -> ProseQualityFailedCall:
    return ProseQualityFailedCall(
        request.request_kind,
        request.request_fingerprint,
        request.prompt_hash,
        request.input_hash,
        request.component_input_hash,
        request.model_id,
        request.prompt_version,
        error_code,
        attempts,
    )


def _anonymous_render(render: dict[str, Any]) -> dict[str, Any]:
    return {"blocks": [{"text": item["text"]} for item in render["blocks"]]}


def _validate_model(model_id: str) -> None:
    if model_id != PROSE_QUALITY_MODEL_ID:
        raise ProseQualityProtocolError("prose_quality_model_id_not_frozen")


def _response_usage(response: Any) -> dict[str, int]:
    value = response.usage
    return {
        "requests": 1,
        "input_tokens": int(getattr(value, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(value, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(value, "total_tokens", 0) or 0),
        "cached_tokens": int(getattr(value, "prompt_cache_hit_tokens", 0) or 0),
        "reasoning_tokens": 0,
    }


def _zero_usage() -> dict[str, int]:
    return {
        "requests": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }


__all__ = [
    "DeepSeekProseQualityCriticProvider",
    "FakeProseQualityCriticProvider",
    "MirroredQualityExecution",
    "PROSE_QUALITY_COMPONENT_HASH",
    "PROSE_QUALITY_FINDINGS_CANDIDATE_SCHEMA_HASH",
    "PROSE_QUALITY_FINDINGS_PROMPT_VERSION",
    "PROSE_QUALITY_MODEL_ID",
    "PROSE_QUALITY_PAIRWISE_CANDIDATE_SCHEMA_HASH",
    "PROSE_QUALITY_PAIRWISE_PROMPT_VERSION",
    "PROSE_QUALITY_REQUEST_PROTOCOL",
    "ProseQualityCriticProvider",
    "ProseQualityExecution",
    "ProseQualityFailedCall",
    "ProseQualityInfrastructureError",
    "ProseQualityProtocolError",
    "ProseQualityProviderResult",
    "ProseQualityRequest",
    "ProseQualityTransportAttempt",
    "execute_mirrored_pairwise_quality",
    "execute_quality_findings",
]
