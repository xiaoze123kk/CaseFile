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
PROSE_JUDGE_REQUEST_PROTOCOL: Final = "prose-judge-json-object-v3"
PROSE_JUDGE_SCHEMA_HASH: Final = canonical_json_sha256(
    ProseJudgeReport.model_json_schema()
)
PROSE_EVIDENCE_CATALOG_VERSION: Final = "prose-evidence-catalog-v1"
PROSE_EVIDENCE_CATALOG_MAX_SPAN_CHARS: Final = 4000
PROSE_EVIDENCE_CATALOG_POLICY: Final = {
    "version": PROSE_EVIDENCE_CATALOG_VERSION,
    "segmentation": "unicode-sentence-or-newline-v1",
    "sentence_terminators": ["。", "！", "？", "!", "?", "\n"],
    "trim_boundary_whitespace": True,
    "max_span_chars": PROSE_EVIDENCE_CATALOG_MAX_SPAN_CHARS,
}
PROSE_EVIDENCE_CATALOG_POLICY_HASH: Final = canonical_json_sha256(
    PROSE_EVIDENCE_CATALOG_POLICY
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


@dataclass(frozen=True, slots=True)
class ProseProtocolCallExecution:
    """One validated Provider call for bounded protocol qualification probes."""

    status: Literal["completed", "protocol_failed", "inconclusive"]
    report: dict[str, Any] | None
    call: ProseJudgeProviderResult | None
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


def execute_prose_judge_protocol_call(
    provider: ProseJudgeProvider,
    *,
    role: JudgeRole,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProseProtocolCallExecution:
    """Execute and validate exactly one production Judge request."""

    if model_id != PROSE_COUNCIL_MODEL_ID:
        raise ProseCouncilProtocolError("prose_council_model_id_not_frozen")
    call: ProseJudgeProviderResult | None = None
    try:
        checklist_json, render_json, profile_json, evidence_catalog = (
            _validated_protocol_inputs(checklist, render, profile)
        )
        request = _judge_request(
            role=role,
            checklist=checklist_json,
            render=render_json,
            profile=profile_json,
            model_id=model_id,
            api_key=api_key,
            evidence_catalog=evidence_catalog,
        )
        call = _execute_call(provider.judge_scene, request, None)
        report = _validated_report(
            call,
            checklist=checklist_json,
            render=render_json,
            profile=profile_json,
            evidence_catalog=evidence_catalog,
        )
        if report["role"] != role:
            raise ProseCouncilProtocolError("prose_judge_role_mismatch")
    except ProseCouncilInfrastructureError as error:
        return ProseProtocolCallExecution("inconclusive", None, call, str(error))
    except (CompilerContractError, ProseCouncilProtocolError) as error:
        return ProseProtocolCallExecution("protocol_failed", None, call, str(error))
    return ProseProtocolCallExecution("completed", report, call)


def execute_prose_arbiter_protocol_call(
    provider: ProseJudgeProvider,
    *,
    disputed_check_ids: list[str],
    judge_reports: list[dict[str, Any]],
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    model_id: str,
    api_key: str,
) -> ProseProtocolCallExecution:
    """Execute and validate exactly one production batch Arbiter request."""

    if model_id != PROSE_COUNCIL_MODEL_ID:
        raise ProseCouncilProtocolError("prose_council_model_id_not_frozen")
    call: ProseJudgeProviderResult | None = None
    try:
        checklist_json, render_json, profile_json, evidence_catalog = (
            _validated_protocol_inputs(checklist, render, profile)
        )
        check_ids = [item["check_id"] for item in checklist_json["checks"]]
        disputed = [item for item in check_ids if item in set(disputed_check_ids)]
        if not disputed or disputed != disputed_check_ids:
            raise ProseCouncilProtocolError("prose_arbiter_disputed_checks_invalid")
        reports = [
            validate_prose_judge_report(
                report,
                checklist=checklist_json,
                render=render_json,
                profile=profile_json,
            ).model_dump(mode="json")
            for report in judge_reports
        ]
        roles = [report["role"] for report in reports]
        if (
            len(reports) < 2
            or len(set(roles)) != len(roles)
            or any(role not in _AGENT_BY_ROLE or role == "arbiter" for role in roles)
        ):
            raise ProseCouncilProtocolError("prose_arbiter_judge_reports_invalid")
        if any(
            len(
                {
                    next(
                        item["verdict"]
                        for item in report["assessments"]
                        if item["check_id"] == check_id
                    )
                    for report in reports
                }
            )
            < 2
            for check_id in disputed
        ):
            raise ProseCouncilProtocolError("prose_arbiter_dispute_not_proven")
        request = _arbiter_request(
            disputed=disputed,
            reports=reports,
            checklist=checklist_json,
            render=render_json,
            profile=profile_json,
            model_id=model_id,
            api_key=api_key,
            evidence_catalog=evidence_catalog,
        )
        call = _execute_call(provider.arbitrate_scene, request, None)
        report = _validated_report(
            call,
            checklist=checklist_json,
            render=render_json,
            profile=profile_json,
            evidence_catalog=evidence_catalog,
            disputed_check_ids=disputed,
        )
        if report["role"] != "arbiter":
            raise ProseCouncilProtocolError("prose_arbiter_role_mismatch")
    except ProseCouncilInfrastructureError as error:
        return ProseProtocolCallExecution("inconclusive", None, call, str(error))
    except (CompilerContractError, ProseCouncilProtocolError) as error:
        return ProseProtocolCallExecution("protocol_failed", None, call, str(error))
    return ProseProtocolCallExecution("completed", report, call)


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
    evidence_catalog = build_server_evidence_catalog(render_json)
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
                evidence_catalog=evidence_catalog,
            )
            result = _execute_call(provider.judge_scene, request, recover_call)
            calls.append(result)
            report = _validated_report(
                result,
                checklist=checklist_json,
                render=render_json,
                profile=profile_json,
                evidence_catalog=evidence_catalog,
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
            evidence_catalog=evidence_catalog,
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
                evidence_catalog=evidence_catalog,
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


def _validated_protocol_inputs(
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    checklist_json = _model_json(checklist)
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    render_json = validate_scene_render(
        render,
        checklist=checklist_json,
        profile=profile_json,
    ).model_dump(mode="json")
    return (
        checklist_json,
        render_json,
        profile_json,
        build_server_evidence_catalog(render_json),
    )


def _validate_policy(policy: ProseCouncilPolicy) -> None:
    if policy not in PROSE_COUNCIL_POLICIES:
        raise ProseCouncilProtocolError("prose_council_policy_not_frozen")


def build_server_evidence_catalog(
    render: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build deterministic, exact-copy Evidence candidates from render blocks."""

    catalog: list[dict[str, Any]] = []
    terminators = {"。", "！", "？", "!", "?", "\n"}
    max_chars = PROSE_EVIDENCE_CATALOG_MAX_SPAN_CHARS
    for block in render["blocks"]:
        text = str(block["text"])
        segment_start = 0
        segment_ends = [
            index + 1 for index, char in enumerate(text) if char in terminators
        ]
        if not segment_ends or segment_ends[-1] != len(text):
            segment_ends.append(len(text))
        for segment_end in segment_ends:
            start = segment_start
            end = segment_end
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            chunk_start = start
            while chunk_start < end:
                chunk_end = min(chunk_start + max_chars, end)
                catalog.append(
                    {
                        "block_id": block["block_id"],
                        "start_char": chunk_start,
                        "end_char": chunk_end,
                        "text": text[chunk_start:chunk_end],
                    }
                )
                chunk_start = chunk_end
            segment_start = segment_end
    return catalog


def _judge_request(
    *,
    role: JudgeRole,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    model_id: str,
    api_key: str,
    evidence_catalog: list[dict[str, Any]],
) -> ProseJudgeRequest:
    prompt = load_prompt(_AGENT_BY_ROLE[role])
    payload = {
        "server_bindings": _server_bindings(checklist=checklist, render=render),
        "server_evidence_catalog": evidence_catalog,
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
    evidence_catalog: list[dict[str, Any]],
) -> ProseArbiterRequest:
    prompt = load_prompt(_AGENT_BY_ROLE["arbiter"])
    disputed_set = set(disputed)
    payload = {
        "server_bindings": _server_bindings(checklist=checklist, render=render),
        "server_evidence_catalog": evidence_catalog,
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


def _server_bindings(
    *, checklist: dict[str, Any], render: dict[str, Any]
) -> dict[str, str]:
    """Expose server-computed identity values for verbatim model echoing."""

    return {
        "scene_id": str(checklist["scene_id"]),
        "checklist_hash": canonical_json_sha256(checklist),
        "render_hash": canonical_json_sha256(render),
        "output_schema_id": "compiler.prose-judge-report.v1",
        "output_schema_hash": PROSE_JUDGE_SCHEMA_HASH,
        "evidence_catalog_version": PROSE_EVIDENCE_CATALOG_VERSION,
        "evidence_catalog_policy_hash": PROSE_EVIDENCE_CATALOG_POLICY_HASH,
    }


def _request_fingerprint(
    *, model_id: str, role: str, prompt_version: str, prompt_hash: str, input_hash: str
) -> str:
    return canonical_json_sha256(
        {
            "protocol": PROSE_JUDGE_REQUEST_PROTOCOL,
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
    evidence_catalog: list[dict[str, Any]],
    disputed_check_ids: list[str] | None = None,
) -> dict[str, Any]:
    if result.candidate is None:
        raise ProseCouncilProtocolError("prose_judge_empty_or_invalid_json")
    validated = validate_prose_judge_report(
        result.candidate,
        checklist=checklist,
        render=render,
        profile=profile,
        disputed_check_ids=disputed_check_ids,
    ).model_dump(mode="json")
    allowed_evidence = {
        canonical_json_sha256(item) for item in evidence_catalog
    }
    if any(
        canonical_json_sha256(evidence) not in allowed_evidence
        for assessment in validated["assessments"]
        for evidence in assessment["evidence"]
    ):
        raise ProseCouncilProtocolError(
            "compiler_prose_judge_evidence_catalog_mismatch"
        )
    return validated


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
    "PROSE_EVIDENCE_CATALOG_POLICY",
    "PROSE_EVIDENCE_CATALOG_POLICY_HASH",
    "PROSE_EVIDENCE_CATALOG_VERSION",
    "PROSE_JUDGE_REQUEST_PROTOCOL",
    "PROSE_JUDGE_SCHEMA_HASH",
    "ProseArbiterRequest",
    "ProseCouncilExecution",
    "ProseCouncilInfrastructureError",
    "ProseCouncilPolicy",
    "ProseCouncilProtocolError",
    "ProseJudgeProvider",
    "ProseJudgeProviderResult",
    "ProseJudgeRequest",
    "ProseProtocolCallExecution",
    "build_server_evidence_catalog",
    "execute_prose_arbiter_protocol_call",
    "execute_prose_judge_protocol_call",
    "execute_semantic_council",
]
