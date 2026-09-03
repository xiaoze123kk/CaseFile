"""Provider-neutral anonymous pointwise Quality Assessment runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol

from casefile_contracts import ProseQualityAssessment, ProseQualityAssessmentCandidate
from openai import OpenAI
from pydantic import ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    PROSE_EVIDENCE_CATALOG_POLICY_HASH,
    PROSE_EVIDENCE_CATALOG_VERSION,
    build_server_evidence_catalog,
)
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    CompilerContractError,
    canonical_json_sha256,
    validate_novel_profile_v2,
    validate_quality_assessment,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_QUALITY_ASSESSMENT_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION: Final = "prose-quality-assessment-v1"
PROSE_QUALITY_ASSESSMENT_REQUEST_PROTOCOL: Final = (
    "prose-quality-assessment-json-object-v1"
)
PROSE_QUALITY_ASSESSMENT_COMPONENT_VERSION: Final = (
    "prose-quality-assessment-runtime-v1"
)
PROSE_QUALITY_ASSESSMENT_MAX_TURNS: Final = 1
PROSE_QUALITY_ASSESSMENT_NETWORK_RETRIES: Final = 0
PROSE_QUALITY_ASSESSMENT_TEMPERATURE: Final = 0
PROSE_QUALITY_ASSESSMENT_MAX_OUTPUT_TOKENS: Final = 8192
PROSE_QUALITY_ASSESSMENT_THINKING_ENABLED: Final = False
PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA: Final = (
    ProseQualityAssessmentCandidate.model_json_schema()
)
PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA
)
PROSE_QUALITY_ASSESSMENT_REPORT_SCHEMA_HASH: Final = canonical_json_sha256(
    ProseQualityAssessment.model_json_schema()
)
PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_QUALITY_ASSESSMENT_COMPONENT_VERSION,
        "request_protocol": PROSE_QUALITY_ASSESSMENT_REQUEST_PROTOCOL,
        "candidate_schema_hash": PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA_HASH,
        "report_schema_hash": PROSE_QUALITY_ASSESSMENT_REPORT_SCHEMA_HASH,
        "evidence_catalog_version": PROSE_EVIDENCE_CATALOG_VERSION,
        "evidence_catalog_policy_hash": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "assessment": "anonymous-complete-five-dimension-severity-v1",
    }
)


class ProseQualityAssessmentError(RuntimeError):
    """Base pointwise assessment error."""


class ProseQualityAssessmentProtocolError(ProseQualityAssessmentError):
    """Frozen request, output, evidence, or recovery was invalid."""


class ProseQualityAssessmentInfrastructureError(ProseQualityAssessmentError):
    """A Provider failure made the assessment inconclusive."""

    def __init__(
        self,
        message: str,
        *,
        failed_call: ProseQualityAssessmentFailedCall | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProseQualityAssessmentTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProseQualityAssessmentRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    max_turns: int = PROSE_QUALITY_ASSESSMENT_MAX_TURNS
    network_retries: int = PROSE_QUALITY_ASSESSMENT_NETWORK_RETRIES
    temperature: int = PROSE_QUALITY_ASSESSMENT_TEMPERATURE
    max_output_tokens: int = PROSE_QUALITY_ASSESSMENT_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_QUALITY_ASSESSMENT_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseQualityAssessmentProviderResult:
    candidate: dict[str, Any] | None
    raw_response: str
    usage: dict[str, int]
    latency_ms: int
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    output_hash: str
    model_id: str
    prompt_version: str
    request_payload: dict[str, Any]
    transport_attempts: tuple[ProseQualityAssessmentTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProseQualityAssessmentFailedCall:
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    error_code: str
    transport_attempts: tuple[ProseQualityAssessmentTransportAttempt, ...]


class ProseQualityAssessmentProvider(Protocol):
    def assess_scene(
        self, request: ProseQualityAssessmentRequest
    ) -> ProseQualityAssessmentProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProseQualityAssessmentExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    assessment: dict[str, Any] | None
    call: ProseQualityAssessmentProviderResult | None
    failed_call: ProseQualityAssessmentFailedCall | None = None
    error_code: str | None = None


class DeepSeekProseQualityAssessmentProvider:
    """One-turn DeepSeek JSON-object adapter with hidden retries disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def assess_scene(
        self, request: ProseQualityAssessmentRequest
    ) -> ProseQualityAssessmentProviderResult:
        if not request.api_key:
            raise ProseQualityAssessmentInfrastructureError(
                "prose_quality_assessment_api_key_missing"
            )
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_quality_assessment_provider_failed:{type(error).__name__}"
            attempt = ProseQualityAssessmentTransportAttempt(
                1,
                "failed",
                max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code,
                False,
                None,
            )
            raise ProseQualityAssessmentInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        usage = _response_usage(response)
        attempt = ProseQualityAssessmentTransportAttempt(
            1,
            "completed",
            max(0, round((perf_counter() - attempt_started) * 1000)),
            None,
            True,
            usage,
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
        return ProseQualityAssessmentProviderResult(
            candidate,
            raw,
            usage,
            max(0, round((perf_counter() - started) * 1000)),
            request.request_fingerprint,
            request.prompt_hash,
            request.input_hash,
            request.component_input_hash,
            sha256(raw.encode("utf-8")).hexdigest(),
            request.model_id,
            request.prompt_version,
            request.input_payload,
            (attempt,),
        )

    def _create_completion(self, request: ProseQualityAssessmentRequest) -> Any:
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
                            PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA,
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


class FakeProseQualityAssessmentProvider:
    """Deterministic queued assessment candidates for zero-network tests."""

    def __init__(
        self,
        *,
        candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._candidates = deque(candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0
        self.requests: list[ProseQualityAssessmentRequest] = []

    def assess_scene(
        self, request: ProseQualityAssessmentRequest
    ) -> ProseQualityAssessmentProviderResult:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == self._failure_at_call:
            raise ProseQualityAssessmentInfrastructureError(
                "prose_quality_assessment_fake_infrastructure"
            )
        candidate = self._candidates.popleft() if self._candidates else None
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if candidate else ""
        usage = _zero_usage()
        return ProseQualityAssessmentProviderResult(
            candidate,
            raw,
            usage,
            0,
            request.request_fingerprint,
            request.prompt_hash,
            request.input_hash,
            request.component_input_hash,
            sha256(raw.encode("utf-8")).hexdigest(),
            request.model_id,
            request.prompt_version,
            request.input_payload,
            (ProseQualityAssessmentTransportAttempt(1, "completed", 0, None, True, usage),),
        )


def execute_quality_assessment(
    provider: ProseQualityAssessmentProvider,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    model_id: str,
    api_key: str,
    recover_call: Callable[
        [str], ProseQualityAssessmentProviderResult | None
    ]
    | None = None,
) -> ProseQualityAssessmentExecution:
    """Assess exactly one anonymous accepted render."""

    call: ProseQualityAssessmentProviderResult | None = None
    try:
        request, render_json, catalog = build_quality_assessment_request(
            checklist=checklist,
            render=render,
            profile=profile,
            semantic_consensus=semantic_consensus,
            model_id=model_id,
            api_key=api_key,
        )
        recovered = recover_call(request.request_fingerprint) if recover_call else None
        try:
            call = (
                replace(recovered, recovered=True)
                if recovered is not None
                else provider.assess_scene(request)
            )
        except ProseQualityAssessmentInfrastructureError as error:
            failed = error.failed_call or _failed_call_from_request(
                request,
                str(error),
                (
                    ProseQualityAssessmentTransportAttempt(
                        1, "failed", 0, str(error), False, None
                    ),
                ),
            )
            return ProseQualityAssessmentExecution(
                "inconclusive", None, None, failed, str(error)
            )
        _validate_result_binding(call, request)
        assessment = _assessment_from_candidate(
            call.candidate,
            checklist=checklist,
            render=render_json,
            profile=profile,
            semantic_consensus=semantic_consensus,
            evidence_catalog=catalog,
        )
    except (CompilerContractError, ProseQualityAssessmentProtocolError) as error:
        return ProseQualityAssessmentExecution(
            "protocol_failed", None, call, error_code=str(error)
        )
    return ProseQualityAssessmentExecution("completed", assessment, call)


def build_quality_assessment_request(
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    model_id: str,
    api_key: str,
) -> tuple[ProseQualityAssessmentRequest, dict[str, Any], list[dict[str, Any]]]:
    """Build a role-free request whose score is bound by server-owned hashes."""

    if model_id != PROSE_QUALITY_ASSESSMENT_MODEL_ID:
        raise ProseQualityAssessmentProtocolError(
            "prose_quality_assessment_model_id_not_frozen"
        )
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render_json = validate_scene_render(
        render, checklist=checklist, profile=profile_json
    ).model_dump(mode="json")
    consensus = validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render_json,
        profile=profile_json,
    ).model_dump(mode="json")
    catalog = build_server_evidence_catalog(render_json)
    prompt = load_prompt(
        "prose_quality_assessment", PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION
    )
    binding = {
        "component_id": "prose_quality_assessment",
        "component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
        "scene_id": render_json["scene_id"],
        "render_hash": canonical_json_sha256(render_json),
        "semantic_consensus_hash": canonical_json_sha256(consensus),
        "profile_hash": canonical_json_sha256(profile_json),
        "checklist_hash": canonical_json_sha256(checklist),
        "candidate_schema_hash": PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA_HASH,
        "report_schema_hash": PROSE_QUALITY_ASSESSMENT_REPORT_SCHEMA_HASH,
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
            "profile": profile_json["prose"],
            "checklist": checklist,
            "scene": {
                "blocks": [
                    {"block_id": item["block_id"], "text": item["text"]}
                    for item in render_json["blocks"]
                ]
            },
        },
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "output_schema_id": "compiler.prose-quality-assessment-candidate.v1",
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_QUALITY_ASSESSMENT_REQUEST_PROTOCOL,
            "component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
            "model_id": model_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_QUALITY_ASSESSMENT_MAX_TURNS,
            "network_retries": PROSE_QUALITY_ASSESSMENT_NETWORK_RETRIES,
            "temperature": PROSE_QUALITY_ASSESSMENT_TEMPERATURE,
            "max_output_tokens": PROSE_QUALITY_ASSESSMENT_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_QUALITY_ASSESSMENT_THINKING_ENABLED,
        }
    )
    return (
        ProseQualityAssessmentRequest(
            model_id,
            api_key,
            prompt.system_prompt,
            prompt.version,
            prompt.system_prompt_sha256,
            payload,
            input_hash,
            component_input_hash,
            fingerprint,
        ),
        render_json,
        catalog,
    )


def _assessment_from_candidate(
    candidate_value: dict[str, Any] | None,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    semantic_consensus: dict[str, Any],
    evidence_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    if candidate_value is None:
        raise ProseQualityAssessmentProtocolError(
            "prose_quality_assessment_empty_or_invalid_json"
        )
    try:
        candidate = ProseQualityAssessmentCandidate.model_validate(
            candidate_value
        ).model_dump(mode="json")
    except ValidationError as error:
        raise ProseQualityAssessmentProtocolError(
            "prose_quality_assessment_candidate_invalid"
        ) from error
    if [item["dimension"] for item in candidate["dimensions"]] != list(
        QUALITY_DIMENSIONS
    ):
        raise ProseQualityAssessmentProtocolError(
            "prose_quality_assessment_dimension_coverage_mismatch"
        )
    catalog = {item["evidence_id"]: item for item in evidence_catalog}
    dimensions = []
    for item in candidate["dimensions"]:
        evidence_ids = item["evidence_ids"]
        if (item["severity"] == "none") != (not evidence_ids):
            raise ProseQualityAssessmentProtocolError(
                "prose_quality_assessment_evidence_required"
            )
        if any(evidence_id not in catalog for evidence_id in evidence_ids):
            raise ProseQualityAssessmentProtocolError(
                "prose_quality_assessment_evidence_catalog_mismatch"
            )
        dimensions.append(
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
                "rationale": item["rationale"],
            }
        )
    report = {
        "schema_id": "compiler.prose-quality-assessment.v1",
        "scene_id": render["scene_id"],
        "render_hash": canonical_json_sha256(render),
        "dimensions": dimensions,
    }
    return validate_quality_assessment(
        report,
        checklist=checklist,
        render=render,
        profile=profile,
        semantic_consensus=semantic_consensus,
    ).model_dump(mode="json")


def _validate_result_binding(
    result: ProseQualityAssessmentProviderResult,
    request: ProseQualityAssessmentRequest,
) -> None:
    if (
        result.request_fingerprint != request.request_fingerprint
        or result.prompt_hash != request.prompt_hash
        or result.input_hash != request.input_hash
        or result.component_input_hash != request.component_input_hash
        or result.model_id != request.model_id
        or result.prompt_version != request.prompt_version
        or canonical_json_sha256(result.request_payload) != request.input_hash
    ):
        raise ProseQualityAssessmentProtocolError(
            "prose_quality_assessment_recovery_fingerprint_mismatch"
        )


def _failed_call_from_request(
    request: ProseQualityAssessmentRequest,
    error_code: str,
    attempts: tuple[ProseQualityAssessmentTransportAttempt, ...],
) -> ProseQualityAssessmentFailedCall:
    return ProseQualityAssessmentFailedCall(
        request.request_fingerprint,
        request.prompt_hash,
        request.input_hash,
        request.component_input_hash,
        request.model_id,
        request.prompt_version,
        error_code,
        attempts,
    )


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
    "DeepSeekProseQualityAssessmentProvider",
    "FakeProseQualityAssessmentProvider",
    "PROSE_QUALITY_ASSESSMENT_CANDIDATE_SCHEMA_HASH",
    "PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH",
    "PROSE_QUALITY_ASSESSMENT_MODEL_ID",
    "PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION",
    "PROSE_QUALITY_ASSESSMENT_REQUEST_PROTOCOL",
    "ProseQualityAssessmentExecution",
    "ProseQualityAssessmentFailedCall",
    "ProseQualityAssessmentInfrastructureError",
    "ProseQualityAssessmentProtocolError",
    "ProseQualityAssessmentProvider",
    "ProseQualityAssessmentProviderResult",
    "ProseQualityAssessmentRequest",
    "ProseQualityAssessmentTransportAttempt",
    "build_quality_assessment_request",
    "execute_quality_assessment",
]
