"""Provider-neutral N4.5 full-Scene Polisher runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol

from casefile_contracts import SceneRender, SceneRenderCandidate
from openai import OpenAI

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import FULL_COUNCIL_POLICY
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    normalize_scene_polish_candidate,
    validate_novel_profile_v2,
    validate_quality_findings_report,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_POLISHER_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_POLISHER_PROMPT_VERSION: Final = "prose-polisher-v2"
PROSE_POLISHER_REQUEST_PROTOCOL: Final = "prose-polisher-json-object-v2"
PROSE_POLISHER_COMPONENT_VERSION: Final = "prose-polisher-runtime-v2"
PROSE_POLISHER_MAX_TURNS: Final = 1
PROSE_POLISHER_NETWORK_RETRIES: Final = 0
PROSE_POLISHER_TEMPERATURE: Final = 0
PROSE_POLISHER_MAX_OUTPUT_TOKENS: Final = 16_384
PROSE_POLISHER_THINKING_ENABLED: Final = False
PROSE_POLISHER_CANDIDATE_SCHEMA: Final = SceneRenderCandidate.model_json_schema()
PROSE_POLISHER_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_POLISHER_CANDIDATE_SCHEMA
)
PROSE_POLISHER_RENDER_SCHEMA_HASH: Final = canonical_json_sha256(
    SceneRender.model_json_schema()
)
PROSE_POLISHER_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_POLISHER_COMPONENT_VERSION,
        "request_protocol": PROSE_POLISHER_REQUEST_PROTOCOL,
        "candidate_schema_hash": PROSE_POLISHER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_POLISHER_RENDER_SCHEMA_HASH,
        "normalization": "full-scene-polish-soft-target-length-v2",
        "preservation_council_policy_hash": FULL_COUNCIL_POLICY.policy_hash,
        "max_calls_per_scene": 1,
    }
)


class ProsePolisherError(RuntimeError):
    """Base Polisher execution error."""


class ProsePolisherProtocolError(ProsePolisherError):
    """Frozen input, recovery, or output violated the Polisher protocol."""


class ProsePolisherInfrastructureError(ProsePolisherError):
    """A Provider failure made the Polisher call inconclusive."""

    def __init__(
        self, message: str, *, failed_call: ProsePolisherFailedCall | None = None
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProsePolisherTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProsePolisherRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    max_turns: int = PROSE_POLISHER_MAX_TURNS
    network_retries: int = PROSE_POLISHER_NETWORK_RETRIES
    temperature: int = PROSE_POLISHER_TEMPERATURE
    max_output_tokens: int = PROSE_POLISHER_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_POLISHER_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProsePolisherProviderResult:
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
    transport_attempts: tuple[ProsePolisherTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProsePolisherFailedCall:
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    error_code: str
    transport_attempts: tuple[ProsePolisherTransportAttempt, ...]


class ProsePolisherProvider(Protocol):
    def polish_scene(
        self, request: ProsePolisherRequest
    ) -> ProsePolisherProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProsePolisherExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    render: dict[str, Any] | None
    call: ProsePolisherProviderResult | None
    failed_call: ProsePolisherFailedCall | None = None
    error_code: str | None = None


class DeepSeekProsePolisherProvider:
    """One-turn DeepSeek JSON-object adapter with SDK retries disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def polish_scene(
        self, request: ProsePolisherRequest
    ) -> ProsePolisherProviderResult:
        if not request.api_key:
            raise ProsePolisherInfrastructureError("prose_polisher_api_key_missing")
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_polisher_provider_failed:{type(error).__name__}"
            attempt = ProsePolisherTransportAttempt(
                1,
                "failed",
                max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code,
                False,
                None,
            )
            raise ProsePolisherInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        usage = _response_usage(response)
        attempt = ProsePolisherTransportAttempt(
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
        return ProsePolisherProviderResult(
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

    def _create_completion(self, request: ProsePolisherRequest) -> Any:
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
                            PROSE_POLISHER_CANDIDATE_SCHEMA,
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


class FakeProsePolisherProvider:
    """Deterministic queued full-Scene candidates for zero-network tests."""

    def __init__(
        self,
        *,
        candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._candidates = deque(candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0

    def polish_scene(
        self, request: ProsePolisherRequest
    ) -> ProsePolisherProviderResult:
        self.call_count += 1
        if self.call_count == self._failure_at_call:
            raise ProsePolisherInfrastructureError("prose_polisher_fake_infrastructure")
        candidate = self._candidates.popleft() if self._candidates else None
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if candidate else ""
        usage = _zero_usage()
        return ProsePolisherProviderResult(
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
            (ProsePolisherTransportAttempt(1, "completed", 0, None, True, usage),),
        )


def execute_prose_polisher(
    provider: ProsePolisherProvider,
    *,
    profile: dict[str, Any],
    checklist: dict[str, Any],
    current_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_findings: dict[str, Any],
    model_id: str,
    api_key: str,
    recover_call: Callable[[str], ProsePolisherProviderResult | None] | None = None,
) -> ProsePolisherExecution:
    """Produce one complete polished Scene after exact semantic/findings validation."""

    call: ProsePolisherProviderResult | None = None
    try:
        request = build_prose_polisher_request(
            profile=profile,
            checklist=checklist,
            current_render=current_render,
            semantic_consensus=semantic_consensus,
            quality_findings=quality_findings,
            model_id=model_id,
            api_key=api_key,
        )
        recovered = recover_call(request.request_fingerprint) if recover_call else None
        try:
            call = (
                replace(recovered, recovered=True)
                if recovered is not None
                else provider.polish_scene(request)
            )
        except ProsePolisherInfrastructureError as error:
            failed = error.failed_call or _failed_call_from_request(
                request,
                str(error),
                (ProsePolisherTransportAttempt(1, "failed", 0, str(error), False, None),),
            )
            return ProsePolisherExecution(
                "inconclusive", None, None, failed_call=failed, error_code=str(error)
            )
        _validate_result_binding(call, request)
        if call.candidate is None:
            raise ProsePolisherProtocolError("prose_polisher_empty_or_invalid_json")
        render = normalize_scene_polish_candidate(
            call.candidate,
            checklist=checklist,
            profile=profile,
            current_render=current_render,
            component_input_hash=request.component_input_hash,
        ).model_dump(mode="json")
    except (CompilerContractError, ProsePolisherProtocolError) as error:
        return ProsePolisherExecution("protocol_failed", None, call, error_code=str(error))
    return ProsePolisherExecution("completed", render, call)


def build_prose_polisher_request(
    *,
    profile: dict[str, Any],
    checklist: dict[str, Any],
    current_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_findings: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProsePolisherRequest:
    """Build the minimal immutable Polisher view from accepted upstream facts."""

    if model_id != PROSE_POLISHER_MODEL_ID:
        raise ProsePolisherProtocolError("prose_polisher_model_id_not_frozen")
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render = validate_scene_render(
        current_render, checklist=checklist, profile=profile_json
    ).model_dump(mode="json")
    if render["stage"] not in {"writer", "rewrite_1", "rewrite_2"}:
        raise ProsePolisherProtocolError("prose_polisher_source_stage_invalid")
    consensus = validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render,
        profile=profile_json,
    ).model_dump(mode="json")
    findings = validate_quality_findings_report(
        quality_findings,
        checklist=checklist,
        render=render,
        profile=profile_json,
        semantic_consensus=consensus,
    ).model_dump(mode="json")
    prompt = load_prompt("prose_polisher", PROSE_POLISHER_PROMPT_VERSION)
    binding = {
        "component_id": "prose_polisher",
        "component_hash": PROSE_POLISHER_COMPONENT_HASH,
        "scene_id": render["scene_id"],
        "source_render_hash": canonical_json_sha256(render),
        "semantic_consensus_hash": canonical_json_sha256(consensus),
        "quality_findings_hash": canonical_json_sha256(findings),
        "checklist_hash": canonical_json_sha256(checklist),
        "profile_hash": canonical_json_sha256(profile_json),
        "prompt_hash": prompt.system_prompt_sha256,
        "model_id": model_id,
        "candidate_schema_hash": PROSE_POLISHER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_POLISHER_RENDER_SCHEMA_HASH,
    }
    component_input_hash = canonical_json_sha256(binding)
    payload = {
        "server_bindings": {
            **binding,
            "component_input_hash": component_input_hash,
            "candidate_schema_id": "compiler.scene-render-candidate.v1",
            "length_contract": {
                "unit": "unicode_code_points_in_block_text_only",
                **profile_json["prose"]["target_scene_chars"],
                "enforcement": "model_quality_guidance",
            },
        },
        "untrusted_data": {
            "profile": profile_json,
            "checklist": checklist,
            "current_render": render,
            "quality_findings": findings,
        },
        "output_schema_id": "compiler.scene-render-candidate.v1",
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_POLISHER_REQUEST_PROTOCOL,
            "component_hash": PROSE_POLISHER_COMPONENT_HASH,
            "model_id": model_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_POLISHER_MAX_TURNS,
            "network_retries": PROSE_POLISHER_NETWORK_RETRIES,
            "temperature": PROSE_POLISHER_TEMPERATURE,
            "max_output_tokens": PROSE_POLISHER_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_POLISHER_THINKING_ENABLED,
        }
    )
    return ProsePolisherRequest(
        model_id,
        api_key,
        prompt.system_prompt,
        prompt.version,
        prompt.system_prompt_sha256,
        payload,
        input_hash,
        component_input_hash,
        fingerprint,
    )


def _validate_result_binding(
    result: ProsePolisherProviderResult, request: ProsePolisherRequest
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
        raise ProsePolisherProtocolError("prose_polisher_recovery_fingerprint_mismatch")


def _failed_call_from_request(
    request: ProsePolisherRequest,
    error_code: str,
    attempts: tuple[ProsePolisherTransportAttempt, ...],
) -> ProsePolisherFailedCall:
    return ProsePolisherFailedCall(
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
    "DeepSeekProsePolisherProvider",
    "FakeProsePolisherProvider",
    "PROSE_POLISHER_CANDIDATE_SCHEMA_HASH",
    "PROSE_POLISHER_COMPONENT_HASH",
    "PROSE_POLISHER_MODEL_ID",
    "PROSE_POLISHER_PROMPT_VERSION",
    "PROSE_POLISHER_REQUEST_PROTOCOL",
    "ProsePolisherExecution",
    "ProsePolisherFailedCall",
    "ProsePolisherInfrastructureError",
    "ProsePolisherProtocolError",
    "ProsePolisherProvider",
    "ProsePolisherProviderResult",
    "ProsePolisherRequest",
    "ProsePolisherTransportAttempt",
    "build_prose_polisher_request",
    "execute_prose_polisher",
]
