"""Provider-neutral bounded-window prose patch runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol

from casefile_contracts import ProsePolishPatchCandidate, SceneRender
from openai import OpenAI

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    build_server_evidence_catalog,
)
from casefile.domain.narrative_compiler import (
    PROSE_EDIT_WINDOW_POLICY_HASH,
    PROSE_EDIT_WINDOW_POLICY_VERSION,
    CompilerContractError,
    apply_prose_polish_patch,
    build_editable_window_manifest,
    canonical_json_sha256,
    validate_novel_profile_v2,
    validate_quality_assessment,
    validate_scene_render,
    validate_semantic_acceptance,
)

PROSE_PATCH_POLISHER_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_PATCH_POLISHER_PROMPT_VERSION: Final = "prose-patch-polisher-v1"
PROSE_PATCH_POLISHER_REQUEST_PROTOCOL: Final = "prose-patch-polisher-json-object-v1"
PROSE_PATCH_POLISHER_COMPONENT_VERSION: Final = "prose-patch-polisher-runtime-v1"
PROSE_PATCH_POLISHER_MAX_TURNS: Final = 1
PROSE_PATCH_POLISHER_NETWORK_RETRIES: Final = 0
PROSE_PATCH_POLISHER_TEMPERATURE: Final = 0
PROSE_PATCH_POLISHER_MAX_OUTPUT_TOKENS: Final = 8192
PROSE_PATCH_POLISHER_THINKING_ENABLED: Final = False
PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA: Final = (
    ProsePolishPatchCandidate.model_json_schema()
)
PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA
)
PROSE_PATCH_POLISHER_RENDER_SCHEMA_HASH: Final = canonical_json_sha256(
    SceneRender.model_json_schema()
)
PROSE_PATCH_POLISHER_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_PATCH_POLISHER_COMPONENT_VERSION,
        "request_protocol": PROSE_PATCH_POLISHER_REQUEST_PROTOCOL,
        "candidate_schema_hash": PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_PATCH_POLISHER_RENDER_SCHEMA_HASH,
        "window_policy_version": PROSE_EDIT_WINDOW_POLICY_VERSION,
        "window_policy_hash": PROSE_EDIT_WINDOW_POLICY_HASH,
        "normalization": "server-applied-authorized-window-patch-v1",
        "preservation_council_policy_hash": FULL_COUNCIL_POLICY.policy_hash,
        "max_calls_per_scene": 1,
    }
)


class ProsePatchPolisherError(RuntimeError):
    """Base bounded Polisher error."""


class ProsePatchPolisherProtocolError(ProsePatchPolisherError):
    """Frozen input, output, patch, or recovery was invalid."""


class ProsePatchPolisherInfrastructureError(ProsePatchPolisherError):
    """A Provider failure made the patch call inconclusive."""

    def __init__(
        self, message: str, *, failed_call: ProsePatchPolisherFailedCall | None = None
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProsePatchPolisherTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProsePatchPolisherRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    max_turns: int = PROSE_PATCH_POLISHER_MAX_TURNS
    network_retries: int = PROSE_PATCH_POLISHER_NETWORK_RETRIES
    temperature: int = PROSE_PATCH_POLISHER_TEMPERATURE
    max_output_tokens: int = PROSE_PATCH_POLISHER_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_PATCH_POLISHER_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProsePatchPolisherProviderResult:
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
    transport_attempts: tuple[ProsePatchPolisherTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProsePatchPolisherFailedCall:
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    error_code: str
    transport_attempts: tuple[ProsePatchPolisherTransportAttempt, ...]


class ProsePatchPolisherProvider(Protocol):
    def polish_windows(
        self, request: ProsePatchPolisherRequest
    ) -> ProsePatchPolisherProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProsePatchPolisherExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    candidate: dict[str, Any] | None
    render: dict[str, Any] | None
    call: ProsePatchPolisherProviderResult | None
    abstained: bool = False
    failed_call: ProsePatchPolisherFailedCall | None = None
    error_code: str | None = None


class DeepSeekProsePatchPolisherProvider:
    """One-turn DeepSeek JSON-object adapter with hidden retries disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def polish_windows(
        self, request: ProsePatchPolisherRequest
    ) -> ProsePatchPolisherProviderResult:
        if not request.api_key:
            raise ProsePatchPolisherInfrastructureError(
                "prose_patch_polisher_api_key_missing"
            )
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_patch_polisher_provider_failed:{type(error).__name__}"
            attempt = ProsePatchPolisherTransportAttempt(
                1,
                "failed",
                max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code,
                False,
                None,
            )
            raise ProsePatchPolisherInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        usage = _response_usage(response)
        attempt = ProsePatchPolisherTransportAttempt(
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
        return ProsePatchPolisherProviderResult(
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

    def _create_completion(self, request: ProsePatchPolisherRequest) -> Any:
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
                            PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA,
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


class FakeProsePatchPolisherProvider:
    """Deterministic queued patch candidates for zero-network tests."""

    def __init__(
        self,
        *,
        candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._candidates = deque(candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0
        self.requests: list[ProsePatchPolisherRequest] = []

    def polish_windows(
        self, request: ProsePatchPolisherRequest
    ) -> ProsePatchPolisherProviderResult:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == self._failure_at_call:
            raise ProsePatchPolisherInfrastructureError(
                "prose_patch_polisher_fake_infrastructure"
            )
        candidate = self._candidates.popleft() if self._candidates else None
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if candidate else ""
        usage = _zero_usage()
        return ProsePatchPolisherProviderResult(
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
            (ProsePatchPolisherTransportAttempt(1, "completed", 0, None, True, usage),),
        )


def execute_prose_patch_polisher(
    provider: ProsePatchPolisherProvider,
    *,
    profile: dict[str, Any],
    checklist: dict[str, Any],
    current_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_assessment: dict[str, Any],
    window_manifest: dict[str, Any],
    model_id: str,
    api_key: str,
    recover_call: Callable[[str], ProsePatchPolisherProviderResult | None] | None = None,
) -> ProsePatchPolisherExecution:
    """Generate and deterministically apply one authorized-window patch."""

    call: ProsePatchPolisherProviderResult | None = None
    try:
        request = build_prose_patch_polisher_request(
            profile=profile,
            checklist=checklist,
            current_render=current_render,
            semantic_consensus=semantic_consensus,
            quality_assessment=quality_assessment,
            window_manifest=window_manifest,
            model_id=model_id,
            api_key=api_key,
        )
        recovered = recover_call(request.request_fingerprint) if recover_call else None
        try:
            call = (
                replace(recovered, recovered=True)
                if recovered is not None
                else provider.polish_windows(request)
            )
        except ProsePatchPolisherInfrastructureError as error:
            failed = error.failed_call or _failed_call_from_request(
                request,
                str(error),
                (ProsePatchPolisherTransportAttempt(1, "failed", 0, str(error), False, None),),
            )
            return ProsePatchPolisherExecution(
                "inconclusive", None, None, None, failed_call=failed, error_code=str(error)
            )
        _validate_result_binding(call, request)
        if call.candidate is None:
            raise ProsePatchPolisherProtocolError(
                "prose_patch_polisher_empty_or_invalid_json"
            )
        parsed = ProsePolishPatchCandidate.model_validate(call.candidate).model_dump(
            mode="json"
        )
        render = apply_prose_polish_patch(
            parsed,
            manifest=window_manifest,
            checklist=checklist,
            profile=profile,
            current_render=current_render,
            component_input_hash=request.component_input_hash,
        )
    except (CompilerContractError, ProsePatchPolisherProtocolError, ValueError) as error:
        return ProsePatchPolisherExecution(
            "protocol_failed", None, None, call, error_code=str(error)
        )
    return ProsePatchPolisherExecution(
        "completed",
        parsed,
        render.model_dump(mode="json") if render is not None else None,
        call,
        abstained=render is None,
    )


def build_prose_patch_polisher_request(
    *,
    profile: dict[str, Any],
    checklist: dict[str, Any],
    current_render: dict[str, Any],
    semantic_consensus: dict[str, Any],
    quality_assessment: dict[str, Any],
    window_manifest: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProsePatchPolisherRequest:
    """Build the minimal patch request and re-derive its authorization manifest."""

    if model_id != PROSE_PATCH_POLISHER_MODEL_ID:
        raise ProsePatchPolisherProtocolError(
            "prose_patch_polisher_model_id_not_frozen"
        )
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render = validate_scene_render(
        current_render, checklist=checklist, profile=profile_json
    ).model_dump(mode="json")
    if render["stage"] not in {"writer", "rewrite_1", "rewrite_2"}:
        raise ProsePatchPolisherProtocolError(
            "prose_patch_polisher_source_stage_invalid"
        )
    consensus = validate_semantic_acceptance(
        semantic_consensus,
        checklist=checklist,
        render=render,
        profile=profile_json,
    ).model_dump(mode="json")
    assessment = validate_quality_assessment(
        quality_assessment,
        checklist=checklist,
        render=render,
        profile=profile_json,
        semantic_consensus=consensus,
    ).model_dump(mode="json")
    rebuilt = build_editable_window_manifest(
        assessment=assessment,
        checklist=checklist,
        render=render,
        profile=profile_json,
        semantic_consensus=consensus,
        evidence_catalog=build_server_evidence_catalog(render),
    )
    if (
        rebuilt.status != "ready"
        or canonical_json_sha256(rebuilt.manifest) != canonical_json_sha256(window_manifest)
    ):
        raise ProsePatchPolisherProtocolError(
            "prose_patch_polisher_window_manifest_invalid"
        )
    prompt = load_prompt("prose_patch_polisher", PROSE_PATCH_POLISHER_PROMPT_VERSION)
    manifest_hash = canonical_json_sha256(window_manifest)
    binding = {
        "component_id": "prose_patch_polisher",
        "component_hash": PROSE_PATCH_POLISHER_COMPONENT_HASH,
        "scene_id": render["scene_id"],
        "source_render_hash": canonical_json_sha256(render),
        "semantic_consensus_hash": canonical_json_sha256(consensus),
        "quality_assessment_hash": canonical_json_sha256(assessment),
        "window_manifest_hash": manifest_hash,
        "window_policy_version": PROSE_EDIT_WINDOW_POLICY_VERSION,
        "window_policy_hash": PROSE_EDIT_WINDOW_POLICY_HASH,
        "checklist_hash": canonical_json_sha256(checklist),
        "profile_hash": canonical_json_sha256(profile_json),
        "prompt_hash": prompt.system_prompt_sha256,
        "model_id": model_id,
        "candidate_schema_hash": PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_PATCH_POLISHER_RENDER_SCHEMA_HASH,
    }
    component_input_hash = canonical_json_sha256(binding)
    payload = {
        "server_bindings": {
            **binding,
            "component_input_hash": component_input_hash,
            "length_contract": {
                "unit": "unicode_code_points_in_block_text_only",
                **profile_json["prose"]["target_scene_chars"],
                "enforcement": "model_quality_guidance",
            },
        },
        "untrusted_data": {
            "profile": profile_json["prose"],
            "checklist": checklist,
            "scene": {
                "blocks": [
                    {"block_id": item["block_id"], "text": item["text"]}
                    for item in render["blocks"]
                ]
            },
            "quality_assessment": assessment["dimensions"],
            "editable_windows": window_manifest["windows"],
        },
        "output_schema_id": "compiler.prose-polish-patch-candidate.v1",
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_PATCH_POLISHER_REQUEST_PROTOCOL,
            "component_hash": PROSE_PATCH_POLISHER_COMPONENT_HASH,
            "model_id": model_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_PATCH_POLISHER_MAX_TURNS,
            "network_retries": PROSE_PATCH_POLISHER_NETWORK_RETRIES,
            "temperature": PROSE_PATCH_POLISHER_TEMPERATURE,
            "max_output_tokens": PROSE_PATCH_POLISHER_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_PATCH_POLISHER_THINKING_ENABLED,
        }
    )
    return ProsePatchPolisherRequest(
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
    result: ProsePatchPolisherProviderResult, request: ProsePatchPolisherRequest
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
        raise ProsePatchPolisherProtocolError(
            "prose_patch_polisher_recovery_fingerprint_mismatch"
        )


def _failed_call_from_request(
    request: ProsePatchPolisherRequest,
    error_code: str,
    attempts: tuple[ProsePatchPolisherTransportAttempt, ...],
) -> ProsePatchPolisherFailedCall:
    return ProsePatchPolisherFailedCall(
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
    "DeepSeekProsePatchPolisherProvider",
    "FakeProsePatchPolisherProvider",
    "PROSE_PATCH_POLISHER_CANDIDATE_SCHEMA_HASH",
    "PROSE_PATCH_POLISHER_COMPONENT_HASH",
    "PROSE_PATCH_POLISHER_MODEL_ID",
    "PROSE_PATCH_POLISHER_PROMPT_VERSION",
    "PROSE_PATCH_POLISHER_REQUEST_PROTOCOL",
    "ProsePatchPolisherExecution",
    "ProsePatchPolisherFailedCall",
    "ProsePatchPolisherInfrastructureError",
    "ProsePatchPolisherProtocolError",
    "ProsePatchPolisherProvider",
    "ProsePatchPolisherProviderResult",
    "ProsePatchPolisherRequest",
    "ProsePatchPolisherTransportAttempt",
    "build_prose_patch_polisher_request",
    "execute_prose_patch_polisher",
]
