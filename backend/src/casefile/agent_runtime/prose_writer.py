"""N4.5 provider-neutral, single-call Scene Writer runtime."""

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
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    normalize_scene_render_candidate,
    validate_novel_profile_v2,
    validate_prose_judge_checklist,
)

PROSE_WRITER_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_WRITER_PROMPT_VERSION: Final = "prose-writer-v1"
PROSE_WRITER_REQUEST_PROTOCOL: Final = "prose-writer-json-object-v1"
PROSE_WRITER_COMPONENT_VERSION: Final = "prose-writer-runtime-v1"
PROSE_WRITER_MAX_TURNS: Final = 1
PROSE_WRITER_MAX_CALLS: Final = 1
PROSE_WRITER_NETWORK_RETRIES: Final = 0
PROSE_WRITER_TEMPERATURE: Final = 0
PROSE_WRITER_MAX_OUTPUT_TOKENS: Final = 16_384
PROSE_WRITER_THINKING_ENABLED: Final = False
PROSE_WRITER_CANDIDATE_SCHEMA_ID: Final = "compiler.scene-render-candidate.v1"
PROSE_WRITER_RENDER_SCHEMA_ID: Final = "compiler.scene-render.v1"
PROSE_WRITER_CANDIDATE_SCHEMA: Final = SceneRenderCandidate.model_json_schema()
PROSE_WRITER_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_WRITER_CANDIDATE_SCHEMA
)
PROSE_WRITER_RENDER_SCHEMA_HASH: Final = canonical_json_sha256(SceneRender.model_json_schema())
PROSE_WRITER_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_WRITER_COMPONENT_VERSION,
        "request_protocol": PROSE_WRITER_REQUEST_PROTOCOL,
        "candidate_schema_id": PROSE_WRITER_CANDIDATE_SCHEMA_ID,
        "candidate_schema_hash": PROSE_WRITER_CANDIDATE_SCHEMA_HASH,
        "render_schema_id": PROSE_WRITER_RENDER_SCHEMA_ID,
        "render_schema_hash": PROSE_WRITER_RENDER_SCHEMA_HASH,
        "normalization": "drop-whitespace-only-blocks-and-inject-writer-identity-v1",
    }
)


class ProseWriterError(RuntimeError):
    """Base error for the bounded Writer component."""


class ProseWriterProtocolError(ProseWriterError):
    """A frozen input, recovery result, or candidate violated the protocol."""


class ProseWriterInfrastructureError(ProseWriterError):
    """A Provider or network failure made the Writer call inconclusive."""

    def __init__(
        self,
        message: str,
        *,
        failed_call: ProseWriterFailedCall | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProseWriterTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProseWriterRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    remaining_scene_call_budget: int
    max_turns: int = PROSE_WRITER_MAX_TURNS
    network_retries: int = PROSE_WRITER_NETWORK_RETRIES
    temperature: int = PROSE_WRITER_TEMPERATURE
    max_output_tokens: int = PROSE_WRITER_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_WRITER_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseWriterProviderResult:
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
    transport_attempts: tuple[ProseWriterTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProseWriterFailedCall:
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    error_code: str
    transport_attempts: tuple[ProseWriterTransportAttempt, ...]


class ProseWriterProvider(Protocol):
    def write_scene(self, request: ProseWriterRequest) -> ProseWriterProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProseWriterExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    render: dict[str, Any] | None
    call: ProseWriterProviderResult | None
    failed_call: ProseWriterFailedCall | None = None
    error_code: str | None = None


class DeepSeekProseWriterProvider:
    """One-turn DeepSeek JSON-object adapter with SDK retry disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def write_scene(self, request: ProseWriterRequest) -> ProseWriterProviderResult:
        if not request.api_key:
            raise ProseWriterInfrastructureError("prose_writer_api_key_missing")
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_writer_provider_failed:{type(error).__name__}"
            attempt = ProseWriterTransportAttempt(
                attempt_index=1,
                status="failed",
                latency_ms=max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code=error_code,
                response_observed=False,
                usage=None,
            )
            raise ProseWriterInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        response_usage = response.usage
        usage = {
            "requests": 1,
            "input_tokens": int(getattr(response_usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(response_usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response_usage, "total_tokens", 0) or 0),
            "cached_tokens": int(getattr(response_usage, "prompt_cache_hit_tokens", 0) or 0),
            "reasoning_tokens": 0,
        }
        attempt = ProseWriterTransportAttempt(
            attempt_index=1,
            status="completed",
            latency_ms=max(0, round((perf_counter() - attempt_started) * 1000)),
            error_code=None,
            response_observed=True,
            usage=usage if response.usage is not None else None,
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
        return ProseWriterProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage=usage,
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
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

    def _create_completion(self, request: ProseWriterRequest) -> Any:
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
                            PROSE_WRITER_CANDIDATE_SCHEMA,
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


class FakeProseWriterProvider:
    """Deterministic queued Writer responses for zero-network tests and baselines."""

    def __init__(
        self,
        *,
        candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._candidates = deque(candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0

    def write_scene(self, request: ProseWriterRequest) -> ProseWriterProviderResult:
        self.call_count += 1
        if self.call_count == self._failure_at_call:
            raise ProseWriterInfrastructureError("prose_writer_fake_infrastructure")
        candidate = self._candidates.popleft() if self._candidates else None
        raw = (
            json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if candidate is not None
            else ""
        )
        usage = _zero_usage()
        return ProseWriterProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage=usage,
            latency_ms=0,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            component_input_hash=request.component_input_hash,
            output_hash=sha256(raw.encode("utf-8")).hexdigest(),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
            transport_attempts=(
                ProseWriterTransportAttempt(
                    attempt_index=1,
                    status="completed",
                    latency_ms=0,
                    error_code=None,
                    response_observed=True,
                    usage=usage,
                ),
            ),
        )


def execute_prose_writer(
    provider: ProseWriterProvider,
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    model_id: str,
    api_key: str,
    remaining_scene_call_budget: int,
    recover_call: Callable[[str], ProseWriterProviderResult | None] | None = None,
) -> ProseWriterExecution:
    """Validate frozen inputs, execute at most one Writer call, and normalize it."""

    call: ProseWriterProviderResult | None = None
    try:
        request = build_prose_writer_request(
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            checklist=checklist,
            previous_scene_render=previous_scene_render,
            model_id=model_id,
            api_key=api_key,
            remaining_scene_call_budget=remaining_scene_call_budget,
        )
        recovered = recover_call(request.request_fingerprint) if recover_call else None
        try:
            call = (
                replace(recovered, recovered=True)
                if recovered
                else provider.write_scene(request)
            )
        except ProseWriterInfrastructureError as error:
            failed = error.failed_call or _failed_call_from_request(
                request,
                str(error),
                (
                    ProseWriterTransportAttempt(
                        attempt_index=1,
                        status="failed",
                        latency_ms=0,
                        error_code=str(error),
                        response_observed=False,
                        usage=None,
                    ),
                ),
            )
            return ProseWriterExecution(
                status="inconclusive",
                render=None,
                call=None,
                failed_call=failed,
                error_code=str(error),
            )
        _validate_result_binding(call, request)
        if call.candidate is None:
            raise ProseWriterProtocolError("prose_writer_empty_or_invalid_json")
        render = normalize_scene_render_candidate(
            call.candidate,
            checklist=checklist,
            profile=profile,
            component_input_hash=request.component_input_hash,
        ).model_dump(mode="json")
    except (CompilerContractError, ProseWriterProtocolError) as error:
        return ProseWriterExecution(
            status="protocol_failed",
            render=None,
            call=call,
            error_code=str(error),
        )
    return ProseWriterExecution(status="completed", render=render, call=call)


def build_prose_writer_request(
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    model_id: str,
    api_key: str,
    remaining_scene_call_budget: int,
) -> ProseWriterRequest:
    """Build the minimal Provider view after exact authoritative input validation."""

    if model_id != PROSE_WRITER_MODEL_ID:
        raise ProseWriterProtocolError("prose_writer_model_id_not_frozen")
    if not isinstance(remaining_scene_call_budget, int) or isinstance(
        remaining_scene_call_budget, bool
    ) or not (
        1 <= remaining_scene_call_budget <= 23
    ):
        raise ProseWriterProtocolError("prose_writer_call_budget_invalid")
    checklist_json = validate_prose_judge_checklist(
        checklist,
        scene_plan=scene_plan,
        narrative_ir=narrative_ir,
        profile=profile,
        previous_scene_render=previous_scene_render,
    ).model_dump(mode="json")
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    prompt = load_prompt("prose_writer", PROSE_WRITER_PROMPT_VERSION)
    component_input_hash = canonical_json_sha256(
        {
            "component_id": "prose_writer",
            "component_version": PROSE_WRITER_COMPONENT_VERSION,
            "component_hash": PROSE_WRITER_COMPONENT_HASH,
            "scene_id": checklist_json["scene_id"],
            "scene_ordinal": checklist_json["scene_ordinal"],
            "scene_plan_hash": checklist_json["source"]["scene_plan_hash"],
            "narrative_ir_hash": checklist_json["source"]["narrative_ir_hash"],
            "profile_hash": checklist_json["source"]["profile_hash"],
            "previous_scene_render_hash": checklist_json["source"][
                "previous_scene_render_hash"
            ],
            "checklist_hash": canonical_json_sha256(checklist_json),
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "model_id": model_id,
            "candidate_schema_hash": PROSE_WRITER_CANDIDATE_SCHEMA_HASH,
            "render_schema_hash": PROSE_WRITER_RENDER_SCHEMA_HASH,
            "remaining_scene_call_budget": remaining_scene_call_budget,
        }
    )
    payload = {
        "server_bindings": {
            "component_id": "prose_writer",
            "component_input_hash": component_input_hash,
            "scene_id": checklist_json["scene_id"],
            "scene_ordinal": checklist_json["scene_ordinal"],
            "checklist_hash": canonical_json_sha256(checklist_json),
            "scene_plan_hash": checklist_json["source"]["scene_plan_hash"],
            "narrative_ir_hash": checklist_json["source"]["narrative_ir_hash"],
            "profile_hash": checklist_json["source"]["profile_hash"],
            "previous_scene_render_hash": checklist_json["source"][
                "previous_scene_render_hash"
            ],
            "candidate_schema_id": PROSE_WRITER_CANDIDATE_SCHEMA_ID,
            "candidate_schema_hash": PROSE_WRITER_CANDIDATE_SCHEMA_HASH,
            "render_schema_id": PROSE_WRITER_RENDER_SCHEMA_ID,
            "render_schema_hash": PROSE_WRITER_RENDER_SCHEMA_HASH,
            "max_writer_calls": PROSE_WRITER_MAX_CALLS,
            "remaining_scene_call_budget": remaining_scene_call_budget,
        },
        "untrusted_data": {
            "checklist": checklist_json,
            "scene_context": checklist_json["scene_context"],
            "profile": profile_json,
        },
        "output_schema_id": PROSE_WRITER_CANDIDATE_SCHEMA_ID,
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_WRITER_REQUEST_PROTOCOL,
            "component_hash": PROSE_WRITER_COMPONENT_HASH,
            "model_id": model_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_WRITER_MAX_TURNS,
            "network_retries": PROSE_WRITER_NETWORK_RETRIES,
            "temperature": PROSE_WRITER_TEMPERATURE,
            "max_output_tokens": PROSE_WRITER_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_WRITER_THINKING_ENABLED,
        }
    )
    return ProseWriterRequest(
        model_id=model_id,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        component_input_hash=component_input_hash,
        request_fingerprint=fingerprint,
        remaining_scene_call_budget=remaining_scene_call_budget,
    )


def _validate_result_binding(
    result: ProseWriterProviderResult,
    request: ProseWriterRequest,
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
        raise ProseWriterProtocolError("prose_writer_recovery_fingerprint_mismatch")


def _failed_call_from_request(
    request: ProseWriterRequest,
    error_code: str,
    attempts: tuple[ProseWriterTransportAttempt, ...],
) -> ProseWriterFailedCall:
    return ProseWriterFailedCall(
        request_fingerprint=request.request_fingerprint,
        prompt_hash=request.prompt_hash,
        input_hash=request.input_hash,
        component_input_hash=request.component_input_hash,
        model_id=request.model_id,
        prompt_version=request.prompt_version,
        error_code=error_code,
        transport_attempts=attempts,
    )


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
    "DeepSeekProseWriterProvider",
    "FakeProseWriterProvider",
    "PROSE_WRITER_CANDIDATE_SCHEMA",
    "PROSE_WRITER_CANDIDATE_SCHEMA_HASH",
    "PROSE_WRITER_CANDIDATE_SCHEMA_ID",
    "PROSE_WRITER_COMPONENT_HASH",
    "PROSE_WRITER_COMPONENT_VERSION",
    "PROSE_WRITER_MAX_CALLS",
    "PROSE_WRITER_MAX_OUTPUT_TOKENS",
    "PROSE_WRITER_MODEL_ID",
    "PROSE_WRITER_NETWORK_RETRIES",
    "PROSE_WRITER_PROMPT_VERSION",
    "PROSE_WRITER_RENDER_SCHEMA_HASH",
    "PROSE_WRITER_RENDER_SCHEMA_ID",
    "PROSE_WRITER_REQUEST_PROTOCOL",
    "ProseWriterError",
    "ProseWriterExecution",
    "ProseWriterFailedCall",
    "ProseWriterInfrastructureError",
    "ProseWriterProtocolError",
    "ProseWriterProvider",
    "ProseWriterProviderResult",
    "ProseWriterRequest",
    "ProseWriterTransportAttempt",
    "build_prose_writer_request",
    "execute_prose_writer",
]
