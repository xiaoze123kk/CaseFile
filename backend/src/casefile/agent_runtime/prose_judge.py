"""N4.5 provider-neutral semantic Judge Council runtime."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol, cast

from casefile_contracts import ProseConsensusReport, ProseJudgeReport
from openai import OpenAI

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    validate_novel_profile_v2,
    validate_prose_judge_report,
    validate_scene_render,
)

PROSE_COUNCIL_MODEL_ID: Final = "deepseek-v4-pro"
PROSE_COUNCIL_MAX_TURNS: Final = 1
PROSE_COUNCIL_NETWORK_RETRIES: Final = 0
PROSE_COUNCIL_TEMPERATURE: Final = 0
PROSE_COUNCIL_MAX_OUTPUT_TOKENS: Final = 8192
PROSE_COUNCIL_THINKING_ENABLED: Final = False
PROSE_JUDGE_SCHEMA_HASH: Final = canonical_json_sha256(
    ProseJudgeReport.model_json_schema()
)

JudgeRole = Literal["fidelity", "adversarial", "coherence"]
Verdict = Literal["pass", "fail", "uncertain"]


class ProseCouncilError(RuntimeError):
    """Base error for the bounded semantic Council runtime."""


class ProseCouncilInfrastructureError(ProseCouncilError):
    """A network or Provider failure makes the complete attempt inconclusive."""


class ProseCouncilProtocolError(ProseCouncilError):
    """A response failed the frozen output or evidence protocol."""


@dataclass(frozen=True, slots=True)
class ProseCouncilPolicy:
    policy_id: str
    roles: tuple[JudgeRole, ...]
    use_arbiter: bool

    def descriptor(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "roles": list(self.roles),
            "use_arbiter": self.use_arbiter,
            "consensus_rule": "unanimous-then-batch-arbiter-v1",
            "scene_verdict_precedence": ["uncertain", "fail", "pass"],
            "max_arbiter_calls_per_round": 1,
        }

    @property
    def policy_hash(self) -> str:
        return canonical_json_sha256(self.descriptor())


FIDELITY_ONLY_POLICY: Final = ProseCouncilPolicy(
    "fidelity-only-v1", ("fidelity",), False
)
FIDELITY_ADVERSARIAL_POLICY: Final = ProseCouncilPolicy(
    "fidelity-adversarial-arbiter-v1", ("fidelity", "adversarial"), True
)
FULL_COUNCIL_POLICY: Final = ProseCouncilPolicy(
    "fidelity-adversarial-coherence-arbiter-v1",
    ("fidelity", "adversarial", "coherence"),
    True,
)
PROSE_COUNCIL_POLICIES: Final = (
    FIDELITY_ONLY_POLICY,
    FIDELITY_ADVERSARIAL_POLICY,
    FULL_COUNCIL_POLICY,
)

_AGENT_BY_ROLE: Final = {
    "fidelity": "prose_fidelity_judge",
    "adversarial": "prose_adversarial_judge",
    "coherence": "prose_coherence_judge",
    "arbiter": "prose_arbiter",
}


@dataclass(frozen=True, slots=True)
class ProseJudgeRequest:
    role: JudgeRole
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    request_fingerprint: str
    max_turns: int = PROSE_COUNCIL_MAX_TURNS
    network_retries: int = PROSE_COUNCIL_NETWORK_RETRIES
    temperature: int = PROSE_COUNCIL_TEMPERATURE
    max_output_tokens: int = PROSE_COUNCIL_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_COUNCIL_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseArbiterRequest:
    model_id: str
    api_key: str
    system_prompt: str
    prompt_version: str
    prompt_hash: str
    input_payload: dict[str, Any]
    input_hash: str
    request_fingerprint: str
    disputed_check_ids: tuple[str, ...]
    max_turns: int = PROSE_COUNCIL_MAX_TURNS
    network_retries: int = PROSE_COUNCIL_NETWORK_RETRIES
    temperature: int = PROSE_COUNCIL_TEMPERATURE
    max_output_tokens: int = PROSE_COUNCIL_MAX_OUTPUT_TOKENS
    thinking_enabled: bool = PROSE_COUNCIL_THINKING_ENABLED


@dataclass(frozen=True, slots=True)
class ProseJudgeProviderResult:
    candidate: dict[str, Any] | None
    raw_response: str
    usage: dict[str, int]
    latency_ms: int
    request_fingerprint: str
    prompt_hash: str
    input_hash: str
    output_hash: str
    role: str
    model_id: str
    prompt_version: str
    request_payload: dict[str, Any]
    recovered: bool = False


class ProseJudgeProvider(Protocol):
    def judge_scene(self, request: ProseJudgeRequest) -> ProseJudgeProviderResult: ...

    def arbitrate_scene(self, request: ProseArbiterRequest) -> ProseJudgeProviderResult: ...


@dataclass(frozen=True, slots=True)
class ProseCouncilExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    policy_id: str
    policy_hash: str
    consensus: dict[str, Any] | None
    judge_reports: tuple[dict[str, Any], ...]
    arbiter_report: dict[str, Any] | None
    calls: tuple[ProseJudgeProviderResult, ...]
    error_code: str | None = None


class DeepSeekProseJudgeProvider:
    """One-turn JSON-object DeepSeek adapter with no transparent retry."""

    def __init__(self, *, base_url: str = "https://api.deepseek.com") -> None:
        self.base_url = base_url

    def judge_scene(self, request: ProseJudgeRequest) -> ProseJudgeProviderResult:
        return self._invoke(request)

    def arbitrate_scene(self, request: ProseArbiterRequest) -> ProseJudgeProviderResult:
        return self._invoke(request)

    def _invoke(
        self, request: ProseJudgeRequest | ProseArbiterRequest
    ) -> ProseJudgeProviderResult:
        if not request.api_key:
            raise ProseCouncilInfrastructureError("prose_judge_api_key_missing")
        started = perf_counter()
        client = OpenAI(
            api_key=request.api_key,
            base_url=self.base_url,
            max_retries=request.network_retries,
        )
        try:
            response = client.chat.completions.create(
                model=request.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": request.system_prompt
                        + "\n\n必须严格遵守以下 JSON Schema：\n"
                        + json.dumps(
                            ProseJudgeReport.model_json_schema(),
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
        except Exception as error:
            raise ProseCouncilInfrastructureError(
                f"prose_judge_provider_failed:{type(error).__name__}"
            ) from error
        finally:
            client.close()
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        if len(response.choices) != 1 or not response.choices[0].message.content:
            raw = ""
            candidate = None
        else:
            raw = response.choices[0].message.content or ""
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            candidate = parsed if isinstance(parsed, dict) else None
        response_usage = response.usage
        usage = {
            "requests": 1,
            "input_tokens": int(getattr(response_usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(response_usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response_usage, "total_tokens", 0) or 0),
            "cached_tokens": int(
                getattr(response_usage, "prompt_cache_hit_tokens", 0) or 0
            ),
            "reasoning_tokens": 0,
        }
        return ProseJudgeProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage=usage,
            latency_ms=latency_ms,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            output_hash=sha256(raw.encode("utf-8")).hexdigest(),
            role=(request.role if isinstance(request, ProseJudgeRequest) else "arbiter"),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
        )


class FakeProseJudgeProvider:
    """Queue-backed zero-cost provider; reports are explicit test oracles."""

    def __init__(
        self,
        *,
        judge_reports: tuple[dict[str, Any], ...] = (),
        arbiter_reports: tuple[dict[str, Any], ...] = (),
        failure_at_call: int | None = None,
    ) -> None:
        self._judge_reports = deque(judge_reports)
        self._arbiter_reports = deque(arbiter_reports)
        self._failure_at_call = failure_at_call
        self.call_count = 0

    def judge_scene(self, request: ProseJudgeRequest) -> ProseJudgeProviderResult:
        return self._result(request, self._judge_reports)

    def arbitrate_scene(self, request: ProseArbiterRequest) -> ProseJudgeProviderResult:
        return self._result(request, self._arbiter_reports)

    def _result(
        self,
        request: ProseJudgeRequest | ProseArbiterRequest,
        queue: deque[dict[str, Any]],
    ) -> ProseJudgeProviderResult:
        self.call_count += 1
        if self._failure_at_call == self.call_count:
            raise ProseCouncilInfrastructureError("prose_judge_fake_infrastructure")
        if not queue:
            raw = ""
            candidate = None
        else:
            candidate = queue.popleft()
            raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        return ProseJudgeProviderResult(
            candidate=candidate,
            raw_response=raw,
            usage={
                "requests": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            },
            latency_ms=0,
            request_fingerprint=request.request_fingerprint,
            prompt_hash=request.prompt_hash,
            input_hash=request.input_hash,
            output_hash=sha256(raw.encode("utf-8")).hexdigest(),
            role=(request.role if isinstance(request, ProseJudgeRequest) else "arbiter"),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
            request_payload=request.input_payload,
        )


def execute_semantic_council(
    provider: ProseJudgeProvider,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    policy: ProseCouncilPolicy,
    model_id: str,
    api_key: str,
    recover_call: Callable[[str], ProseJudgeProviderResult | None] | None = None,
) -> ProseCouncilExecution:
    """Execute one bounded semantic round and construct server-owned Consensus."""

    _validate_policy(policy)
    if model_id != PROSE_COUNCIL_MODEL_ID:
        raise ProseCouncilProtocolError("prose_council_model_id_not_frozen")
    checklist_json = _model_json(checklist)
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render_json = validate_scene_render(
        render, checklist=checklist_json, profile=profile_json
    ).model_dump(mode="json")
    calls: list[ProseJudgeProviderResult] = []
    reports: list[dict[str, Any]] = []
    try:
        for role in policy.roles:
            request = _judge_request(
                role=role,
                checklist=checklist_json,
                render=render_json,
                profile=profile_json,
                model_id=model_id,
                api_key=api_key,
            )
            result = _execute_call(provider.judge_scene, request, recover_call)
            calls.append(result)
            report = _validated_report(
                result, checklist=checklist_json, render=render_json, profile=profile_json
            )
            if report["role"] != role:
                raise ProseCouncilProtocolError("prose_judge_role_mismatch")
            reports.append(report)
    except ProseCouncilInfrastructureError as error:
        return _failed_execution(policy, reports, calls, "inconclusive", str(error))
    except (CompilerContractError, ProseCouncilProtocolError) as error:
        return _failed_execution(policy, reports, calls, "protocol_failed", str(error))

    check_ids = [item["check_id"] for item in checklist_json["checks"]]
    verdicts = {
        report["role"]: {
            item["check_id"]: item["verdict"] for item in report["assessments"]
        }
        for report in reports
    }
    disputed = [
        check_id
        for check_id in check_ids
        if len({verdicts[role][check_id] for role in policy.roles}) > 1
    ]
    arbiter_report: dict[str, Any] | None = None
    arbiter_request_hash: str | None = None
    arbiter_error = False
    if disputed and policy.use_arbiter:
        arbiter_request = _arbiter_request(
            disputed=disputed,
            reports=reports,
            checklist=checklist_json,
            render=render_json,
            profile=profile_json,
            model_id=model_id,
            api_key=api_key,
        )
        arbiter_request_hash = arbiter_request.request_fingerprint
        try:
            result = _execute_call(
                provider.arbitrate_scene, arbiter_request, recover_call
            )
            calls.append(result)
            arbiter_report = _validated_report(
                result,
                checklist=checklist_json,
                render=render_json,
                profile=profile_json,
                disputed_check_ids=disputed,
            )
            if arbiter_report["role"] != "arbiter":
                raise ProseCouncilProtocolError("prose_arbiter_role_mismatch")
        except ProseCouncilInfrastructureError as error:
            return _failed_execution(policy, reports, calls, "inconclusive", str(error))
        except (CompilerContractError, ProseCouncilProtocolError):
            arbiter_report = None
            arbiter_error = True

    report_hashes = [canonical_json_sha256(report) for report in reports]
    arbiter_hash = canonical_json_sha256(arbiter_report) if arbiter_report else None
    arbiter_verdicts = (
        {item["check_id"]: item["verdict"] for item in arbiter_report["assessments"]}
        if arbiter_report
        else {}
    )
    checks: list[dict[str, Any]] = []
    for check_id in check_ids:
        role_items = [
            {
                "role": role,
                "report_hash": report_hashes[index],
                "verdict": verdicts[role][check_id],
            }
            for index, role in enumerate(policy.roles)
        ]
        unanimous = len({item["verdict"] for item in role_items}) == 1
        if unanimous:
            final = cast(Verdict, role_items[0]["verdict"])
            source = "unanimous"
        else:
            final = cast(Verdict, arbiter_verdicts.get(check_id, "uncertain"))
            source = "arbiter"
        checks.append(
            {
                "check_id": check_id,
                "role_verdicts": role_items,
                "unanimous": unanimous,
                "entered_arbiter": not unanimous,
                "final_verdict": final,
                "resolution_source": source,
            }
        )
    final_verdicts = [item["final_verdict"] for item in checks]
    scene_verdict: Verdict = (
        "uncertain"
        if "uncertain" in final_verdicts
        else "fail" if "fail" in final_verdicts else "pass"
    )
    consensus = {
        "schema_id": "compiler.prose-consensus-report.v1",
        "scene_id": checklist_json["scene_id"],
        "round": render_json["round"],
        "checklist_hash": canonical_json_sha256(checklist_json),
        "render_hash": canonical_json_sha256(render_json),
        "council_policy_hash": policy.policy_hash,
        "judge_report_hashes": report_hashes,
        "checks": checks,
        "arbiter_request_hash": arbiter_request_hash,
        "arbiter_report_hash": arbiter_hash,
        "scene_verdict": scene_verdict,
        "failed_check_ids": [
            item["check_id"] for item in checks if item["final_verdict"] == "fail"
        ],
        "unresolved_check_ids": [
            item["check_id"] for item in checks if item["final_verdict"] == "uncertain"
        ],
    }
    try:
        validated = ProseConsensusReport.model_validate(consensus).model_dump(mode="json")
    except Exception as error:
        raise ProseCouncilProtocolError("prose_consensus_contract_invalid") from error
    return ProseCouncilExecution(
        status="completed",
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash,
        consensus=validated,
        judge_reports=tuple(reports),
        arbiter_report=arbiter_report,
        calls=tuple(calls),
        error_code="prose_arbiter_report_invalid" if arbiter_error else None,
    )


def _model_json(checklist: dict[str, Any]) -> dict[str, Any]:
    # Exact-rebuild validation belongs to the caller, which owns ScenePlan/NarrativeIR.
    from casefile_contracts import ProseJudgeChecklist

    return ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")


def _validate_policy(policy: ProseCouncilPolicy) -> None:
    if policy not in PROSE_COUNCIL_POLICIES:
        raise ProseCouncilProtocolError("prose_council_policy_not_frozen")


def _judge_request(
    *,
    role: JudgeRole,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProseJudgeRequest:
    prompt = load_prompt(_AGENT_BY_ROLE[role])
    payload = {
        "untrusted_data": {
            "checklist": checklist,
            "render": render,
            "profile": profile,
        },
        "required_role": role,
        "output_schema_id": "compiler.prose-judge-report.v1",
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = _request_fingerprint(
        model_id=model_id,
        role=role,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_hash=input_hash,
    )
    return ProseJudgeRequest(
        role=role,
        model_id=model_id,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        request_fingerprint=fingerprint,
    )


def _arbiter_request(
    *,
    disputed: list[str],
    reports: list[dict[str, Any]],
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProseArbiterRequest:
    prompt = load_prompt(_AGENT_BY_ROLE["arbiter"])
    disputed_set = set(disputed)
    payload = {
        "untrusted_data": {
            "checklist": {
                **checklist,
                "checks": [
                    item for item in checklist["checks"] if item["check_id"] in disputed_set
                ],
            },
            "render": render,
            "profile": profile,
            "judge_reports": reports,
        },
        "disputed_check_ids": disputed,
        "required_role": "arbiter",
        "output_schema_id": "compiler.prose-judge-report.v1",
    }
    input_hash = canonical_json_sha256(payload)
    fingerprint = _request_fingerprint(
        model_id=model_id,
        role="arbiter",
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_hash=input_hash,
    )
    return ProseArbiterRequest(
        model_id=model_id,
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=input_hash,
        request_fingerprint=fingerprint,
        disputed_check_ids=tuple(disputed),
    )


def _request_fingerprint(
    *, model_id: str, role: str, prompt_version: str, prompt_hash: str, input_hash: str
) -> str:
    return canonical_json_sha256(
        {
            "protocol": "prose-judge-json-object-v1",
            "role": role,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "input_hash": input_hash,
            "max_turns": PROSE_COUNCIL_MAX_TURNS,
            "network_retries": PROSE_COUNCIL_NETWORK_RETRIES,
            "temperature": PROSE_COUNCIL_TEMPERATURE,
            "thinking_enabled": PROSE_COUNCIL_THINKING_ENABLED,
            "max_output_tokens": PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
            "output_schema_id": "compiler.prose-judge-report.v1",
            "output_schema_hash": PROSE_JUDGE_SCHEMA_HASH,
        }
    )


def _execute_call(
    invoke: Callable[[Any], ProseJudgeProviderResult],
    request: ProseJudgeRequest | ProseArbiterRequest,
    recover_call: Callable[[str], ProseJudgeProviderResult | None] | None,
) -> ProseJudgeProviderResult:
    recovered = recover_call(request.request_fingerprint) if recover_call else None
    result = replace(recovered, recovered=True) if recovered is not None else invoke(request)
    if (
        result.request_fingerprint != request.request_fingerprint
        or result.prompt_hash != request.prompt_hash
        or result.input_hash != request.input_hash
    ):
        raise ProseCouncilProtocolError("prose_judge_recovery_fingerprint_mismatch")
    return result


def _validated_report(
    result: ProseJudgeProviderResult,
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    disputed_check_ids: list[str] | None = None,
) -> dict[str, Any]:
    if result.candidate is None:
        raise ProseCouncilProtocolError("prose_judge_empty_or_invalid_json")
    return validate_prose_judge_report(
        result.candidate,
        checklist=checklist,
        render=render,
        profile=profile,
        disputed_check_ids=disputed_check_ids,
    ).model_dump(mode="json")


def _failed_execution(
    policy: ProseCouncilPolicy,
    reports: list[dict[str, Any]],
    calls: list[ProseJudgeProviderResult],
    status: Literal["protocol_failed", "inconclusive"],
    error: str,
) -> ProseCouncilExecution:
    return ProseCouncilExecution(
        status=status,
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash,
        consensus=None,
        judge_reports=tuple(reports),
        arbiter_report=None,
        calls=tuple(calls),
        error_code=error,
    )


__all__ = [
    "DeepSeekProseJudgeProvider",
    "FIDELITY_ADVERSARIAL_POLICY",
    "FIDELITY_ONLY_POLICY",
    "FULL_COUNCIL_POLICY",
    "FakeProseJudgeProvider",
    "PROSE_COUNCIL_MAX_OUTPUT_TOKENS",
    "PROSE_COUNCIL_MODEL_ID",
    "PROSE_COUNCIL_POLICIES",
    "PROSE_JUDGE_SCHEMA_HASH",
    "ProseArbiterRequest",
    "ProseCouncilExecution",
    "ProseCouncilInfrastructureError",
    "ProseCouncilPolicy",
    "ProseCouncilProtocolError",
    "ProseJudgeProvider",
    "ProseJudgeProviderResult",
    "ProseJudgeRequest",
    "execute_semantic_council",
]
