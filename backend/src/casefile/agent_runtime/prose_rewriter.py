"""N4.5 provider-neutral, bounded full-Scene Rewrite runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol

from casefile_contracts import ProseConsensusReport, SceneRenderCandidate
from openai import OpenAI
from pydantic import ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import FIDELITY_ONLY_POLICY
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    normalize_scene_rewrite_candidate,
    validate_novel_profile_v2,
    validate_prose_judge_checklist,
    validate_prose_judge_report,
    validate_scene_render,
)

PROSE_REWRITER_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_REWRITER_PROMPT_VERSION: Final = "prose-rewriter-v3"
PROSE_REWRITER_REQUEST_PROTOCOL: Final = "prose-rewriter-json-object-v3"
PROSE_REWRITER_COMPONENT_VERSION: Final = "prose-rewriter-runtime-v3"
PROSE_REWRITER_LENGTH_POLICY_VERSION: Final = "prose-rewriter-length-contract-v2"
PROSE_REWRITER_MAX_TURNS: Final = 1
PROSE_REWRITER_MAX_CALLS_PER_SCENE: Final = 2
PROSE_REWRITER_NETWORK_RETRIES: Final = 0
PROSE_REWRITER_TEMPERATURE: Final = 0
PROSE_REWRITER_MAX_OUTPUT_TOKENS: Final = 16_384
PROSE_REWRITER_THINKING_ENABLED: Final = False
PROSE_REWRITER_CANDIDATE_SCHEMA_ID: Final = "compiler.scene-render-candidate.v1"
PROSE_REWRITER_RENDER_SCHEMA_ID: Final = "compiler.scene-render.v1"
PROSE_REWRITER_CANDIDATE_SCHEMA: Final = SceneRenderCandidate.model_json_schema()
PROSE_REWRITER_CANDIDATE_SCHEMA_HASH: Final = canonical_json_sha256(
    PROSE_REWRITER_CANDIDATE_SCHEMA
)
# Historical v3 identity must not drift when later SceneRender selection reasons are
# appended for parallel supervisors. The old runtime never emits those new reasons.
PROSE_REWRITER_RENDER_SCHEMA_HASH: Final = (
    "a81c4fcaaf6bd7a7f94a99e5d1b57c5afdd585518c85e5c23dfd841ccb5118f2"
)
PROSE_REWRITER_COMPONENT_HASH: Final = canonical_json_sha256(
    {
        "component_version": PROSE_REWRITER_COMPONENT_VERSION,
        "request_protocol": PROSE_REWRITER_REQUEST_PROTOCOL,
        "candidate_schema_hash": PROSE_REWRITER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_REWRITER_RENDER_SCHEMA_HASH,
        "normalization": "full-scene-rewrite-with-direct-render-lineage-v1",
        "length_contract": PROSE_REWRITER_LENGTH_POLICY_VERSION,
        "council_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "max_rewrites_per_scene": PROSE_REWRITER_MAX_CALLS_PER_SCENE,
    }
)


class ProseRewriterError(RuntimeError):
    """Base error for the bounded Rewrite component."""


class ProseRewriterProtocolError(ProseRewriterError):
    """Frozen input, recovery, or candidate violated the Rewrite protocol."""


class ProseRewriterInfrastructureError(ProseRewriterError):
    """A Provider failure made the Rewrite call inconclusive."""

    def __init__(
        self, message: str, *, failed_call: ProseRewriterFailedCall | None = None
    ) -> None:
        super().__init__(message)
        self.failed_call = failed_call


@dataclass(frozen=True, slots=True)
class ProseRewriterTransportAttempt:
    attempt_index: int
    status: Literal["completed", "failed"]
    latency_ms: int
    error_code: str | None
    response_observed: bool
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ProseRewriterRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    component_input_hash: str
    request_fingerprint: str
    rewrite_round: int
    remaining_scene_call_budget: int
    max_turns: int = PROSE_REWRITER_MAX_TURNS
    network_retries: int = PROSE_REWRITER_NETWORK_RETRIES
    temperature: int = PROSE_REWRITER_TEMPERATURE
    max_output_tokens: int = PROSE_REWRITER_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_REWRITER_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseRewriterProviderResult:
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
    transport_attempts: tuple[ProseRewriterTransportAttempt, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ProseRewriterFailedCall:
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    component_input_hash: str
    model_id: str
    prompt_version: str
    rewrite_round: int
    error_code: str
    transport_attempts: tuple[ProseRewriterTransportAttempt, ...]


class ProseRewriterProvider(Protocol):
    def rewrite_scene(
        self, request: ProseRewriterRequest
    ) -> ProseRewriterProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProseRewriterExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    render: dict[str, Any] | None
    call: ProseRewriterProviderResult | None
    failed_call: ProseRewriterFailedCall | None = None
    error_code: str | None = None


class DeepSeekProseRewriterProvider:
    """One-turn DeepSeek JSON-object adapter with SDK retry disabled."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def rewrite_scene(
        self, request: ProseRewriterRequest
    ) -> ProseRewriterProviderResult:
        if not request.api_key:
            raise ProseRewriterInfrastructureError("prose_rewriter_api_key_missing")
        started = perf_counter()
        attempt_started = perf_counter()
        try:
            response = self._create_completion(request)
        except Exception as error:
            error_code = f"prose_rewriter_provider_failed:{type(error).__name__}"
            attempt = ProseRewriterTransportAttempt(
                attempt_index=1,
                status="failed",
                latency_ms=max(0, round((perf_counter() - attempt_started) * 1000)),
                error_code=error_code,
                response_observed=False,
                usage=None,
            )
            raise ProseRewriterInfrastructureError(
                error_code,
                failed_call=_failed_call_from_request(request, error_code, (attempt,)),
            ) from error
        usage = _response_usage(response)
        attempt = ProseRewriterTransportAttempt(
            attempt_index=1,
            status="completed",
            latency_ms=max(0, round((perf_counter() - attempt_started) * 1000)),
            error_code=None,
            response_observed=True,
            usage=usage,
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
        return ProseRewriterProviderResult(
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

    def _create_completion(self, request: ProseRewriterRequest) -> Any:
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
                            PROSE_REWRITER_CANDIDATE_SCHEMA,
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


class FakeProseRewriterProvider:
    """Deterministic queued full-Scene rewrites for zero-network tests."""

    def __init__(
        self,
        *,
        candidates: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._candidates = deque(candidates)
        self._failure_at_call = failure_at_call
        self.call_count = 0

    def rewrite_scene(
        self, request: ProseRewriterRequest
    ) -> ProseRewriterProviderResult:
        self.call_count += 1
        if self.call_count == self._failure_at_call:
            raise ProseRewriterInfrastructureError("prose_rewriter_fake_infrastructure")
        candidate = self._candidates.popleft() if self._candidates else None
        raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True) if candidate else ""
        usage = _zero_usage()
        return ProseRewriterProviderResult(
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
                ProseRewriterTransportAttempt(1, "completed", 0, None, True, usage),
            ),
        )


def execute_prose_rewriter(
    provider: ProseRewriterProvider,
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    current_render: dict[str, Any],
    consensus: dict[str, Any],
    judge_reports: tuple[dict[str, Any], ...],
    model_id: str,
    api_key: str,
    remaining_scene_call_budget: int,
    recover_call: Callable[[str], ProseRewriterProviderResult | None] | None = None,
) -> ProseRewriterExecution:
    """Validate one failed semantic round and produce its complete replacement."""

    call: ProseRewriterProviderResult | None = None
    try:
        request = build_prose_rewriter_request(
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            checklist=checklist,
            previous_scene_render=previous_scene_render,
            current_render=current_render,
            consensus=consensus,
            judge_reports=judge_reports,
            model_id=model_id,
            api_key=api_key,
            remaining_scene_call_budget=remaining_scene_call_budget,
        )
        recovered = recover_call(request.request_fingerprint) if recover_call else None
        try:
            call = (
                replace(recovered, recovered=True)
                if recovered
                else provider.rewrite_scene(request)
            )
        except ProseRewriterInfrastructureError as error:
            failed = error.failed_call or _failed_call_from_request(
                request,
                str(error),
                (ProseRewriterTransportAttempt(1, "failed", 0, str(error), False, None),),
            )
            return ProseRewriterExecution(
                "inconclusive", None, None, failed_call=failed, error_code=str(error)
            )
        _validate_result_binding(call, request)
        if call.candidate is None:
            raise ProseRewriterProtocolError("prose_rewriter_empty_or_invalid_json")
        render = normalize_scene_rewrite_candidate(
            call.candidate,
            checklist=checklist,
            profile=profile,
            current_render=current_render,
            rewrite_round=request.rewrite_round,
            component_input_hash=request.component_input_hash,
        ).model_dump(mode="json")
    except (CompilerContractError, ProseRewriterProtocolError) as error:
        return ProseRewriterExecution(
            "protocol_failed", None, call, error_code=str(error)
        )
    return ProseRewriterExecution("completed", render, call)


def build_prose_rewriter_request(
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    current_render: dict[str, Any],
    consensus: dict[str, Any],
    judge_reports: tuple[dict[str, Any], ...],
    model_id: str,
    api_key: str,
    remaining_scene_call_budget: int,
) -> ProseRewriterRequest:
    """Build the minimal full-Rewrite Provider view after exact validation."""

    if model_id != PROSE_REWRITER_MODEL_ID:
        raise ProseRewriterProtocolError("prose_rewriter_model_id_not_frozen")
    if not isinstance(remaining_scene_call_budget, int) or isinstance(
        remaining_scene_call_budget, bool
    ) or not (1 <= remaining_scene_call_budget <= 23):
        raise ProseRewriterProtocolError("prose_rewriter_call_budget_invalid")
    checklist_json = validate_prose_judge_checklist(
        checklist,
        scene_plan=scene_plan,
        narrative_ir=narrative_ir,
        profile=profile,
        previous_scene_render=previous_scene_render,
    ).model_dump(mode="json")
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render_json = validate_scene_render(
        current_render, checklist=checklist_json, profile=profile_json
    ).model_dump(mode="json")
    rewrite_round = render_json["round"] + 1
    expected_stage = "writer" if rewrite_round == 1 else "rewrite_1"
    if rewrite_round not in {1, 2} or render_json["stage"] != expected_stage:
        raise ProseRewriterProtocolError("prose_rewriter_source_stage_invalid")
    consensus_json, reports_json = _validate_review_inputs(
        consensus=consensus,
        judge_reports=judge_reports,
        checklist=checklist_json,
        render=render_json,
        profile=profile_json,
    )
    checks_by_id = {item["check_id"]: item for item in checklist_json["checks"]}
    assessment_by_id = {
        item["check_id"]: item for item in reports_json[0]["assessments"]
    }
    repair_ids = [
        item["check_id"]
        for item in consensus_json["checks"]
        if item["final_verdict"] != "pass"
    ]
    preserve_ids = [
        item["check_id"]
        for item in consensus_json["checks"]
        if item["final_verdict"] == "pass"
    ]
    repair_findings = [
        {
            "check_id": check_id,
            "expectation": checks_by_id[check_id]["expectation"],
            "polarity": checks_by_id[check_id]["polarity"],
            "final_verdict": next(
                item["final_verdict"]
                for item in consensus_json["checks"]
                if item["check_id"] == check_id
            ),
            "judge_rationale": assessment_by_id[check_id]["rationale"],
            "judge_evidence": assessment_by_id[check_id]["evidence"],
        }
        for check_id in repair_ids
    ]
    preserve_checks = [
        {
            "check_id": check_id,
            "expectation": checks_by_id[check_id]["expectation"],
            "polarity": checks_by_id[check_id]["polarity"],
        }
        for check_id in preserve_ids
    ]
    prompt = load_prompt("prose_rewriter", PROSE_REWRITER_PROMPT_VERSION)
    binding = {
        "component_id": "prose_rewriter",
        "component_hash": PROSE_REWRITER_COMPONENT_HASH,
        "scene_id": checklist_json["scene_id"],
        "rewrite_round": rewrite_round,
        "scene_plan_hash": checklist_json["source"]["scene_plan_hash"],
        "narrative_ir_hash": checklist_json["source"]["narrative_ir_hash"],
        "profile_hash": checklist_json["source"]["profile_hash"],
        "previous_scene_render_hash": checklist_json["source"][
            "previous_scene_render_hash"
        ],
        "checklist_hash": canonical_json_sha256(checklist_json),
        "current_render_hash": canonical_json_sha256(render_json),
        "consensus_hash": canonical_json_sha256(consensus_json),
        "judge_report_hashes": [canonical_json_sha256(item) for item in reports_json],
        "prompt_hash": prompt.system_prompt_sha256,
        "model_id": model_id,
        "candidate_schema_hash": PROSE_REWRITER_CANDIDATE_SCHEMA_HASH,
        "render_schema_hash": PROSE_REWRITER_RENDER_SCHEMA_HASH,
        "remaining_scene_call_budget": remaining_scene_call_budget,
    }
    component_input_hash = canonical_json_sha256(binding)
    length_range = profile_json["prose"]["target_scene_chars"]
    min_chars = length_range["min"]
    max_chars = length_range["max"]
    safety_margin = max(32, min(300, (max_chars - min_chars) // 3))
    generation_floor_chars = min(max_chars, min_chars + safety_margin)
    target_chars = max(
        generation_floor_chars,
        (min_chars + max_chars) // 2,
    )
    block_count = max(3, min(8, (target_chars + 124) // 125))
    length_contract = {
        "policy_version": PROSE_REWRITER_LENGTH_POLICY_VERSION,
        "unit": "unicode_code_points_in_block_text_only",
        "min_chars": min_chars,
        "max_chars": max_chars,
        "target_chars": target_chars,
        "generation_plan": {
            "block_count": block_count,
            "generation_floor_chars": generation_floor_chars,
            "min_chars_per_block": generation_floor_chars // block_count,
            "target_chars_per_block": target_chars // block_count,
        },
        "hard_gate": True,
    }
    payload = {
        "server_bindings": {
            **binding,
            "component_input_hash": component_input_hash,
            "candidate_schema_id": PROSE_REWRITER_CANDIDATE_SCHEMA_ID,
            "render_schema_id": PROSE_REWRITER_RENDER_SCHEMA_ID,
            "max_rewrites_per_scene": PROSE_REWRITER_MAX_CALLS_PER_SCENE,
            "length_contract": length_contract,
        },
        "untrusted_data": {
            "checklist": checklist_json,
            "scene_context": checklist_json["scene_context"],
            "profile": profile_json,
            "current_render": render_json,
            "consensus": consensus_json,
            "repair_findings": repair_findings,
            "preserve_checks": preserve_checks,
        },
        "output_schema_id": PROSE_REWRITER_CANDIDATE_SCHEMA_ID,
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = canonical_json_sha256(
        {
            "protocol": PROSE_REWRITER_REQUEST_PROTOCOL,
            "component_hash": PROSE_REWRITER_COMPONENT_HASH,
            "model_id": model_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.system_prompt_sha256,
            "input_hash": input_hash,
            "component_input_hash": component_input_hash,
            "max_turns": PROSE_REWRITER_MAX_TURNS,
            "network_retries": PROSE_REWRITER_NETWORK_RETRIES,
            "temperature": PROSE_REWRITER_TEMPERATURE,
            "max_output_tokens": PROSE_REWRITER_MAX_OUTPUT_TOKENS,
            "thinking_enabled": PROSE_REWRITER_THINKING_ENABLED,
        }
    )
    return ProseRewriterRequest(
        model_id=model_id,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        component_input_hash=component_input_hash,
        request_fingerprint=fingerprint,
        rewrite_round=rewrite_round,
        remaining_scene_call_budget=remaining_scene_call_budget,
    )


def _validate_review_inputs(
    *,
    consensus: dict[str, Any],
    judge_reports: tuple[dict[str, Any], ...],
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    try:
        consensus_json = ProseConsensusReport.model_validate(consensus).model_dump(
            mode="json"
        )
    except ValidationError as error:
        raise ProseRewriterProtocolError("prose_rewriter_consensus_invalid") from error
    if len(judge_reports) != 1:
        raise ProseRewriterProtocolError("prose_rewriter_frozen_policy_mismatch")
    reports_json = tuple(
        validate_prose_judge_report(
            item, checklist=checklist, render=render, profile=profile
        ).model_dump(mode="json")
        for item in judge_reports
    )
    if reports_json[0]["role"] != "fidelity":
        raise ProseRewriterProtocolError("prose_rewriter_frozen_policy_mismatch")
    check_ids = [item["check_id"] for item in checklist["checks"]]
    report_hash = canonical_json_sha256(reports_json[0])
    verdict_by_id = {
        item["check_id"]: item["verdict"] for item in reports_json[0]["assessments"]
    }
    expected_checks = [
        {
            "check_id": check_id,
            "role_verdicts": [
                {
                    "role": "fidelity",
                    "report_hash": report_hash,
                    "verdict": verdict_by_id[check_id],
                }
            ],
            "unanimous": True,
            "entered_arbiter": False,
            "final_verdict": verdict_by_id[check_id],
            "resolution_source": "unanimous",
        }
        for check_id in check_ids
    ]
    final_verdicts = [verdict_by_id[item] for item in check_ids]
    expected_scene_verdict = (
        "uncertain"
        if "uncertain" in final_verdicts
        else "fail"
        if "fail" in final_verdicts
        else "pass"
    )
    failed = [item for item in check_ids if verdict_by_id[item] == "fail"]
    unresolved = [item for item in check_ids if verdict_by_id[item] == "uncertain"]
    if (
        consensus_json["scene_id"] != checklist["scene_id"]
        or consensus_json["round"] != render["round"]
        or consensus_json["checklist_hash"] != canonical_json_sha256(checklist)
        or consensus_json["render_hash"] != canonical_json_sha256(render)
        or consensus_json["council_policy_hash"] != FIDELITY_ONLY_POLICY.policy_hash
        or consensus_json["checks"] != expected_checks
        or consensus_json["judge_report_hashes"] != [report_hash]
        or consensus_json["arbiter_request_hash"] is not None
        or consensus_json["arbiter_report_hash"] is not None
        or consensus_json["failed_check_ids"] != failed
        or consensus_json["unresolved_check_ids"] != unresolved
        or consensus_json["scene_verdict"] != expected_scene_verdict
        or expected_scene_verdict == "pass"
    ):
        raise ProseRewriterProtocolError("prose_rewriter_review_binding_invalid")
    return consensus_json, reports_json


def _validate_result_binding(
    result: ProseRewriterProviderResult, request: ProseRewriterRequest
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
        raise ProseRewriterProtocolError("prose_rewriter_recovery_fingerprint_mismatch")


def _failed_call_from_request(
    request: ProseRewriterRequest,
    error_code: str,
    attempts: tuple[ProseRewriterTransportAttempt, ...],
) -> ProseRewriterFailedCall:
    return ProseRewriterFailedCall(
        request.request_fingerprint,
        request.prompt_hash,
        request.input_hash,
        request.component_input_hash,
        request.model_id,
        request.prompt_version,
        request.rewrite_round,
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
    "DeepSeekProseRewriterProvider",
    "FakeProseRewriterProvider",
    "PROSE_REWRITER_CANDIDATE_SCHEMA_HASH",
    "PROSE_REWRITER_COMPONENT_HASH",
    "PROSE_REWRITER_MAX_CALLS_PER_SCENE",
    "PROSE_REWRITER_LENGTH_POLICY_VERSION",
    "PROSE_REWRITER_MODEL_ID",
    "PROSE_REWRITER_PROMPT_VERSION",
    "PROSE_REWRITER_REQUEST_PROTOCOL",
    "ProseRewriterExecution",
    "ProseRewriterFailedCall",
    "ProseRewriterInfrastructureError",
    "ProseRewriterProtocolError",
    "ProseRewriterProvider",
    "ProseRewriterProviderResult",
    "ProseRewriterRequest",
    "ProseRewriterTransportAttempt",
    "build_prose_rewriter_request",
    "execute_prose_rewriter",
]
