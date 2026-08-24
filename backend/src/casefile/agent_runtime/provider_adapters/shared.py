"""Shared Provider execution, generation, and output normalization."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from hashlib import sha256
from typing import Any, Literal, cast

from agents import Agent, ModelSettings, RunConfig, Runner, Tool
from agents.exceptions import ModelBehaviorError
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from pydantic import BaseModel

from casefile.agent_runtime.chat_tools import (
    ChatToolContext,
    ChatToolLedger,
    chat_tool_manifest,
    freeze_chat_tool_ledger,
)
from casefile.agent_runtime.closure_repair import ClosureRepairRequest
from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
)
from casefile.agent_runtime.general_mutation import GeneralMutationPlannerRequest
from casefile.agent_runtime.models import (
    BriefAnchorExtractRequest,
    BriefIntakeQuestionsRequest,
    BriefIntakeSynthesizeRequest,
    BriefPolishCandidate,
    BriefPolishRequest,
    BriefStrategyOptionsRequest,
    CaseFileChatRequest,
    GenerationRequest,
    GenerationResult,
    IdeaGenerationRequest,
    ReverseParseRequest,
    RouteSpecificRewriteRequest,
)
from casefile.agent_runtime.prompt import (
    generation_input,
)
from casefile.agent_runtime.prompt_repository import (
    system_prompt_for_task,
)
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError
from casefile.agent_runtime.structured_output import (
    call_deepseek_strict_tool,
    compile_deepseek_strict_schema,
    repair_input,
    strict_fallback_reason,
)
from casefile.agent_runtime.structured_output import (
    merge_usage as _merge_structured_usage,
)
from casefile.agent_runtime.structured_output import (
    validate_model_json as _validate_auxiliary_output,
)
from casefile.agent_runtime.tools import GENERATION_TOOLS, GenerationToolContext
from casefile.agent_runtime.transport_diagnostics import (
    TransportDiagnostics,
    classify_transport_error,
)
from casefile.contracts import ContractValidationError, validate_casefile
from casefile_contracts import (
    CaseFile,
)

CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE_ENV = "CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE"


_CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE_ENV = "CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE"


def _chat_live_temperature() -> float | None:
    """Resolve live chat temperature: explicit env wins, live acceptance defaults to 0.

    Production remains None (provider default) unless the operator explicitly
    sets ``CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE``.
    """

    configured = os.getenv(CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE_ENV, "").strip()
    if not configured:
        if os.getenv(_CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE_ENV) == "1":
            return 0.0
        return None
    try:
        temperature = float(configured)
    except ValueError as error:
        raise ProviderProtocolError(
            f"{CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE_ENV} must be a number"
        ) from error
    if temperature < 0.0 or temperature > 2.0:
        raise ProviderProtocolError(
            f"{CASEFILE_CHAT_CONTEXT_LIVE_TEMPERATURE_ENV} must be between 0 and 2"
        )
    return temperature


def _chat_tool_runtime(
    request: CaseFileChatRequest,
) -> tuple[list[Tool] | None, ChatToolContext | None, int | None]:
    route = request.route
    if route is None:
        return None, None, None
    manifest = chat_tool_manifest(
        route,
        toolset_version=request.toolset_version,
    )
    if not manifest:
        return None, None, None
    max_turns = route.execution_profile.get("max_turns")
    return (
        manifest,
        ChatToolContext(request=request, route=route),
        max_turns if isinstance(max_turns, int) and max_turns > 0 else None,
    )


async def _run_chat_tool_agent(
    request: CaseFileChatRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    instructions: str,
    input_text: str,
    tools: list[Tool],
    context: ChatToolContext,
    max_turns: int | None,
    tracing_disabled: bool,
) -> tuple[ChatToolLedger, dict[str, Any]]:
    """Run tools without asking the model to serialize the final candidate."""

    request.emit(
        "model.tool_agent.started",
        "gathering_evidence",
        {"model_id": request.model_id, "tool_count": len(tools)},
    )
    tool_instructions = (
        instructions.rstrip() + "\n\n本阶段只负责调用工具、核对事实并形成简短的证据摘要。"
        "不要输出最终 CaseFile Chat JSON，不要展示隐藏推理。"
        "最终仅写可审计的事实、证据 ID、建议目标和未覆盖范围。"
    )
    agent: Agent[Any] = Agent(
        name="CaseFile Evidence Agent",
        instructions=tool_instructions,
        model=model,
        model_settings=model_settings,
        tools=tools,
        output_type=str,
    )
    result = await Runner.run(
        agent,
        input_text,
        context=context,
        max_turns=max_turns or request.max_turns,
        run_config=RunConfig(
            workflow_name="CaseFile gathering_evidence",
            tracing_disabled=tracing_disabled,
            trace_include_sensitive_data=False,
        ),
    )
    summary = (
        result.final_output.strip()
        if isinstance(result.final_output, str) and result.final_output.strip()
        else _frozen_evidence_summary(context)
    )
    if summary != result.final_output:
        request.emit(
            "model.tool_agent.summary_fallback",
            "gathering_evidence",
            {
                "reason_code": "empty_evidence_summary",
                "tool_calls": context.metrics.calls,
                "successful_calls": context.metrics.successful_calls,
            },
        )
    usage = _usage_json(result.context_wrapper.usage)
    ledger = freeze_chat_tool_ledger(
        context,
        evidence_summary=summary,
    )
    request.emit(
        "model.tool_agent.completed",
        "gathering_evidence",
        {
            "usage": usage,
            "tool_calls": context.metrics.calls,
            "ledger_entry_count": len(ledger.entries),
        },
    )
    request.emit(
        "model.tool_ledger.frozen",
        "finalizing",
        {
            "ledger_hash": ledger.ledger_hash,
            "entry_count": len(ledger.entries),
            "retrieved_object_ids": list(ledger.retrieved_object_ids),
            "retrieved_evidence_ids": list(ledger.retrieved_evidence_ids),
            "budget_exhausted": ledger.budget_exhausted,
        },
    )
    return ledger, usage


def _frozen_evidence_summary(context: ChatToolContext) -> str:
    """Provide a bounded handoff when tools succeeded but the narrative is empty."""

    retrieved = sorted(set(context.metrics.retrieved_object_ids))
    evidence = sorted(set(context.metrics.retrieved_evidence_ids))
    return (
        "Evidence Agent 未返回文字摘要；Finalizer 必须以 Frozen Tool Ledger 为准。"
        f"已完成只读工具调用 {context.metrics.successful_calls}/{context.metrics.calls} 次；"
        f"读取对象={','.join(retrieved) or '无'}；"
        f"证据={','.join(evidence) or '无'}；"
        f"预算耗尽={'是' if context.metrics.budget_exhausted else '否'}。"
    )[:20_000]


async def _run_auxiliary_agent(
    request: (
        GenerationRequest
        | BriefPolishRequest
        | BriefAnchorExtractRequest
        | BriefIntakeQuestionsRequest
        | BriefIntakeSynthesizeRequest
        | BriefStrategyOptionsRequest
        | CaseFileChatRequest
        | RouteSpecificRewriteRequest
        | ReverseParseRequest
        | IdeaGenerationRequest
        | ThreadCompactionRequest
        | ClosureRepairRequest
        | GeneralMutationPlannerRequest
    ),
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    instructions: str,
    input_text: str,
    output_type: type[BaseModel],
    stage: str,
    structured_output: bool,
    tracing_disabled: bool,
    planned_object_types: dict[str, str] | None = None,
    component_id: str | None = None,
    schema_id: str | None = None,
    deepseek_output_protocol: Literal["strict_tool", "json_object"] | None = None,
    deepseek_output_protocol_is_primary: bool = False,
    tools: list[Tool] | None = None,
    context: ChatToolContext | None = None,
    max_turns: int | None = None,
    strict_validation: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = (
        "native_json_schema" if structured_output else deepseek_output_protocol or "strict_tool"
    )
    usage_records: list[dict[str, Any]] = []
    repaired = False
    fallback_attempted = False
    fallback_error_class: str | None = None
    selected_protocols: set[str] = set()
    current_input = input_text

    if (
        not structured_output
        and deepseek_output_protocol == "json_object"
        and not deepseek_output_protocol_is_primary
    ):
        fallback_attempted = True
        fallback_error_class = "protocol_unsupported"
        request.emit(
            "model.output_protocol_fallback",
            stage,
            {
                "from": "strict_tool",
                "to": "json_object",
                "reason_code": "strict_tool_disabled_by_capability_policy",
                **_protocol_fallback_diagnostics(
                    protocol="strict_tool",
                    network_retry_budget=request.network_retries,
                ),
            },
        )

    if protocol == "strict_tool":
        try:
            compile_deepseek_strict_schema(output_type)
        except Exception as error:
            reason = strict_fallback_reason(error)
            if reason is None:
                raise
            protocol = "json_object"
            fallback_attempted = True
            fallback_error_class = "protocol_unsupported"
            request.emit(
                "model.output_protocol_fallback",
                stage,
                {
                    "from": "strict_tool",
                    "to": protocol,
                    "reason_code": reason,
                    **_protocol_fallback_diagnostics(
                        protocol="strict_tool",
                        network_retry_budget=request.network_retries,
                    ),
                },
            )
    if protocol == "strict_tool" and tools:
        protocol = "json_object"
        fallback_attempted = True
        fallback_error_class = "protocol_unsupported"
        request.emit(
            "model.output_protocol_fallback",
            stage,
            {
                "from": "strict_tool",
                "to": protocol,
                "reason_code": "chat_tools_require_json_object_loop",
                **_protocol_fallback_diagnostics(
                    protocol="strict_tool",
                    network_retry_budget=request.network_retries,
                ),
            },
        )

    for attempt_no in range(1, 4):
        raw_output_text: str | None = None
        if protocol not in selected_protocols:
            request.emit(
                "model.output_protocol_selected",
                stage,
                {"protocol": protocol, "attempt_no": attempt_no},
            )
            selected_protocols.add(protocol)
        request.emit(
            "model.started",
            stage,
            {
                "model_id": request.model_id,
                "attempt_no": attempt_no,
                "protocol": protocol,
                **({"component_id": component_id} if component_id else {}),
                **({"schema_id": schema_id} if schema_id else {}),
            },
        )
        if component_id:
            request.emit(
                "agent.model_call.started",
                stage,
                {
                    "component_id": component_id,
                    "schema_id": schema_id,
                    "attempt_no": attempt_no,
                    "protocol": protocol,
                    "model_id": request.model_id,
                    "prompt_sha256": sha256(instructions.encode("utf-8")).hexdigest(),
                },
            )
        try:
            discarded_paths: list[str] = []
            normalized_ref_paths: list[str] = []
            normalized_time_paths: list[str] = []
            if protocol == "strict_tool":
                if not request.api_key:
                    raise ProviderProtocolError("DeepSeek API key is required")
                strict_result = await call_deepseek_strict_tool(
                    api_key=request.api_key,
                    model_id=request.model_id,
                    network_retries=request.network_retries,
                    instructions=instructions,
                    input_text=current_input,
                    output_type=output_type,
                    temperature=model_settings.temperature,
                )
                raw_output_text = strict_result.raw_output
                usage_records.append(strict_result.usage)
                output = _validate_auxiliary_output(
                    output_type,
                    strict_result.raw_output,
                    discarded_paths=discarded_paths,
                    planned_object_types=planned_object_types,
                    normalized_ref_paths=normalized_ref_paths,
                    normalized_time_paths=normalized_time_paths,
                    discard_forbidden_fields=not strict_validation,
                )
            else:
                resolved_instructions = instructions
                agent_output_type: Any = output_type
                if protocol == "json_object":
                    resolved_instructions += _json_schema_instruction(output_type)
                    agent_output_type = str
                agent: Agent[Any] = Agent(
                    name="CaseFile Editorial Assistant",
                    instructions=resolved_instructions,
                    model=model,
                    model_settings=model_settings,
                    tools=tools or [],
                    output_type=agent_output_type,
                )
                if context is None:
                    result = await Runner.run(
                        agent,
                        current_input,
                        max_turns=max_turns or request.max_turns,
                        run_config=RunConfig(
                            workflow_name=f"CaseFile {stage}",
                            tracing_disabled=tracing_disabled,
                            trace_include_sensitive_data=False,
                        ),
                    )
                else:
                    result = await Runner.run(
                        agent,
                        current_input,
                        context=context,
                        max_turns=max_turns or request.max_turns,
                        run_config=RunConfig(
                            workflow_name=f"CaseFile {stage}",
                            tracing_disabled=tracing_disabled,
                            trace_include_sensitive_data=False,
                        ),
                    )
                usage_records.append(_usage_json(result.context_wrapper.usage))
                final_output = getattr(result, "final_output", None)
                if isinstance(final_output, str):
                    raw_output_text = final_output
                if protocol == "native_json_schema":
                    output = result.final_output_as(
                        output_type,
                        raise_if_incorrect_type=True,
                    )
                else:
                    if not isinstance(result.final_output, str):
                        raise ProviderProtocolError(
                            "DeepSeek auxiliary output must be a JSON string"
                        )
                    output = _validate_auxiliary_output(
                        output_type,
                        _deepseek_json_object_text(result.final_output),
                        discarded_paths=discarded_paths,
                        planned_object_types=planned_object_types,
                        normalized_ref_paths=normalized_ref_paths,
                        normalized_time_paths=normalized_time_paths,
                        discard_forbidden_fields=not strict_validation,
                    )
            if discarded_paths:
                request.emit(
                    "validation.extra_fields_discarded",
                    stage,
                    {"paths": discarded_paths, "field_count": len(discarded_paths)},
                )
            if normalized_ref_paths:
                request.emit(
                    "validation.object_ref_types_normalized",
                    stage,
                    {
                        "paths": normalized_ref_paths,
                        "field_count": len(normalized_ref_paths),
                    },
                )
            if normalized_time_paths:
                request.emit(
                    "validation.wall_clock_times_normalized",
                    stage,
                    {
                        "paths": normalized_time_paths,
                        "field_count": len(normalized_time_paths),
                    },
                )
            usage = _merge_structured_usage(usage_records)
            if context is not None:
                usage["tool_metrics"] = context.metrics.as_dict()
            request.emit("model.completed", stage, {"usage": usage})
            if component_id:
                serialized_output = json.dumps(
                    output.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                request.emit(
                    "agent.model_call.completed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "output_hash": sha256(serialized_output).hexdigest(),
                        "output_size_bytes": len(serialized_output),
                        "usage": usage,
                        **(
                            _retained_raw_output(
                                raw_output_text or serialized_output.decode("utf-8")
                            )
                            if isinstance(request, ClosureRepairRequest)
                            else {}
                        ),
                    },
                )
            request.emit(
                "model.output_validated",
                stage,
                {
                    "protocol": protocol,
                    "attempt_count": attempt_no,
                    "repaired": repaired,
                    "recoverable_transport_error_class": fallback_error_class,
                    **(
                        {
                            **_protocol_fallback_diagnostics(
                                protocol="strict_tool",
                                network_retry_budget=request.network_retries,
                            ),
                            "protocol": protocol,
                            "protocol_phase": "validated",
                            "fallback_succeeded": True,
                        }
                        if fallback_attempted
                        else {
                            "fallback_attempted": False,
                            "fallback_succeeded": False,
                        }
                    ),
                },
            )
            return output.model_dump(mode="json"), usage
        except ContractValidationError as error:
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "pydantic",
                        "error_code": "structured_output_validation_failed",
                        "issues": error.errors[:20],
                        **_retained_raw_output(raw_output_text),
                    },
                )
            if protocol == "strict_tool" and attempt_no < 3:
                issues = error.errors[:20]
                request.emit(
                    "model.output_protocol_fallback",
                    stage,
                    {
                        "from": protocol,
                        "to": "json_object",
                        "reason_code": "strict_schema_violation",
                        **_protocol_fallback_diagnostics(
                            protocol=protocol,
                            network_retry_budget=request.network_retries,
                        ),
                    },
                )
                protocol = "json_object"
                fallback_attempted = True
                fallback_error_class = "protocol_unsupported"
                current_input = repair_input(input_text, issues)
                continue
            if repaired or attempt_no == 3:
                raise
            repaired = True
            issues = error.errors[:20]
            request.emit(
                "model.output_repair_started",
                stage,
                {
                    "attempt_no": attempt_no + 1,
                    "protocol": protocol,
                    "validation_layer": "pydantic",
                    "issues": issues,
                },
            )
            current_input = repair_input(input_text, issues)
        except ModelBehaviorError:
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "pydantic",
                        "error_code": "model_output_invalid",
                        "issues": [
                            {
                                "code": "model_output_invalid",
                                "path": "",
                                "message": "模型输出无法按目标结构解析。",
                            }
                        ],
                        **_retained_raw_output(raw_output_text),
                    },
                )
            if repaired or attempt_no == 3:
                raise
            repaired = True
            issues = [
                {
                    "code": "model_output_invalid",
                    "path": "",
                    "message": "模型输出无法按目标结构解析。",
                }
            ]
            request.emit(
                "model.output_repair_started",
                stage,
                {
                    "attempt_no": attempt_no + 1,
                    "protocol": protocol,
                    "validation_layer": "pydantic",
                    "issues": issues,
                },
            )
            current_input = repair_input(input_text, issues)
        except Exception as error:
            reason = strict_fallback_reason(error) if protocol == "strict_tool" else None
            will_fallback = reason is not None and attempt_no < 3
            diagnostics = classify_transport_error(
                error,
                protocol=protocol,
                protocol_phase="provider_call",
                network_retry_budget=request.network_retries,
                retry_exhausted=not will_fallback,
                fallback_attempted=will_fallback,
                fallback_succeeded=False,
            )
            if reason is not None:
                diagnostics = replace(
                    diagnostics,
                    transport_error_class="protocol_unsupported",
                )
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "transport",
                        "error_code": "provider_call_failed",
                        "issues": [],
                        **diagnostics.as_dict(),
                        **_retained_raw_output(raw_output_text),
                    },
                )
            if reason is None or attempt_no == 3:
                raise
            request.emit(
                "model.output_protocol_fallback",
                stage,
                {
                    "from": protocol,
                    "to": "json_object",
                    "reason_code": reason,
                    **diagnostics.as_dict(),
                },
            )
            protocol = "json_object"
            fallback_attempted = True
            fallback_error_class = "protocol_unsupported"
    raise ProviderProtocolError("Structured output attempts were exhausted")


def _protocol_fallback_diagnostics(*, protocol: str, network_retry_budget: int) -> dict[str, Any]:
    return TransportDiagnostics(
        transport_error_class="protocol_unsupported",
        http_status_class=None,
        protocol=protocol,
        protocol_phase="protocol_negotiation",
        network_retry_budget=max(network_retry_budget, 0),
        network_retry_count=None,
        retry_exhausted=False,
        retry_after_present=False,
        fallback_attempted=True,
        fallback_succeeded=False,
    ).as_dict()


def _deepseek_json_object_text(raw_output: str) -> str:
    """Extract the model's JSON object from a text response.

    ``deepseek-v4-flash`` occasionally emits agent-style DSML blocks and
    trailing markers (for example ``</DSML tool_calls>``) around an otherwise
    valid JSON object. Scan every complete JSON object, prefer one that looks
    like a chat candidate (has an ``answer`` key), and otherwise keep the last
    object. JSON-decoder scanning is deterministic and never rewrites the
    payload, so valid output stays byte-identical and malformed output still
    fails the contract validator.
    """

    candidates: list[str] = []
    decoder = json.JSONDecoder()
    search_from = 0
    while True:
        start = raw_output.find("{", search_from)
        if start < 0:
            break
        try:
            _payload, end = decoder.raw_decode(raw_output, start)
        except json.JSONDecodeError:
            search_from = start + 1
            continue
        candidates.append(raw_output[start:end])
        search_from = end
    if not candidates:
        return raw_output
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "answer" in payload:
            return candidate
    return candidates[-1]


def _retained_raw_output(raw_output: str | None) -> dict[str, Any]:
    """Apply the v8 development/production failed-output retention policy."""

    if raw_output is None:
        return {}
    encoded = raw_output.encode("utf-8")
    payload: dict[str, Any] = {
        "output_hash": sha256(encoded).hexdigest(),
        "output_size_bytes": len(encoded),
    }
    environment = os.getenv("CASEFILE_RUNTIME_ENV", "development").strip().lower()
    if environment in {"production", "prod"}:
        return payload
    limit = 1024 * 1024
    payload["_raw_output"] = encoded[:limit].decode("utf-8", errors="ignore")
    payload["raw_output_truncated"] = len(encoded) > limit
    return payload


async def _run_agent(
    request: GenerationRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    structured_output: bool,
    tracing_disabled: bool,
) -> GenerationResult:
    context = GenerationToolContext(request=request)
    output_type: Any = CaseFile if structured_output else str
    instructions = system_prompt_for_task("brief_to_draft", request.prompt_version)
    if not structured_output:
        instructions += _json_schema_instruction(CaseFile)
    agent = Agent[GenerationToolContext](
        name="CaseFile Architect",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=GENERATION_TOOLS,
        output_type=output_type,
    )
    request.emit("model.started", "generating", {"model_id": request.model_id})
    result = await Runner.run(
        agent,
        generation_input(request),
        context=context,
        max_turns=request.max_turns,
        run_config=RunConfig(
            workflow_name="CaseFile brief_to_draft",
            tracing_disabled=tracing_disabled,
            trace_include_sensitive_data=False,
        ),
    )
    if context.plan_calls != 1:
        raise ProviderProtocolError("plan_object_ids must be called exactly once")
    if structured_output:
        output = result.final_output_as(CaseFile, raise_if_incorrect_type=True)
    else:
        if not isinstance(result.final_output, str):
            raise ProviderProtocolError("DeepSeek final output must be a JSON string")
        output = cast(
            CaseFile,
            _validate_auxiliary_output(CaseFile, result.final_output),
        )
    candidate = _remove_absent_optional_fields(output.model_dump(mode="json"))
    validate_casefile(candidate)
    _validate_generated_descriptions(candidate)
    candidate_ids = _candidate_object_ids(candidate)
    if context.metrics.planned_object_ids.issubset(candidate_ids):
        context.metrics.adopted_results += 1
    if context.validation_calls and context.metrics.successful_calls > 1:
        context.metrics.adopted_results += 1
    usage_json = _usage_json(result.context_wrapper.usage)
    request.emit("model.completed", "generating", {"usage": usage_json})
    return GenerationResult(candidate=candidate, usage=usage_json, tools=context.metrics)


def _candidate_object_ids(candidate: dict[str, Any]) -> set[str]:
    keys = (
        "resolution_specs",
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
        "constraints",
        "structure_locks",
    )
    return {item["id"] for key in keys for item in candidate[key]}


def _remove_absent_optional_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_absent_optional_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _remove_absent_optional_fields(item)
            for key, item in value.items()
            if not (key in {"description", "spatial_position"} and item is None)
        }
    return value


def _bind_safe_patch_registry(
    request: CaseFileChatRequest,
    ledger_payload: dict[str, Any] | None,
) -> tuple[CaseFileChatRequest, dict[str, Any] | None]:
    """Keep pre-finalizer inputs free of a v15 safe-patch registry.

    v15 now proves proposal safety only after the no-tool Finalizer returns;
    the ledger remains evidence and cannot pre-supply finalizer patch values.
    """

    del ledger_payload
    return request, None


def _validate_generated_descriptions(candidate: dict[str, Any]) -> None:
    issues: list[dict[str, str]] = []
    for collection in (
        "resolution_specs",
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
        "constraints",
        "structure_locks",
    ):
        for index, item in enumerate(candidate.get(collection, [])):
            description = item.get("description") if isinstance(item, dict) else None
            if not isinstance(description, str) or not description.strip():
                issues.append(
                    {
                        "code": "generated_description_missing",
                        "path": f"/{collection}/{index}/description",
                        "message": "Agent 生成的对象必须填写非空描述。",
                    }
                )
    if issues:
        raise ContractValidationError(issues)


def _model_client(model: OpenAIChatCompletionsModel) -> AsyncOpenAI | None:
    """Return the underlying async client owned by an Agents SDK model."""

    client = getattr(model, "_client", None)
    return cast(AsyncOpenAI | None, client)


def _deepseek_model_settings(temperature: float | None = None) -> ModelSettings:
    return ModelSettings(
        temperature=temperature,
        include_usage=True,
        parallel_tool_calls=False,
        extra_args={"response_format": {"type": "json_object"}},
        extra_body={"thinking": {"type": "disabled"}},
    )


def _deepseek_v8_output_protocol(model_id: str) -> Literal["strict_tool", "json_object"]:
    """Choose the safest DeepSeek output mode for the compound v8 IR.

    DeepSeek's Beta strict function schema is retained and can be forced for
    investigation.  Real v8 acceptance on the compatibility aliases for
    ``deepseek-v4-flash`` showed accepted strict requests returning tool
    arguments with missing required fields.  JSON mode plus local Pydantic
    validation and the same bounded repair contract is therefore the default
    for that capability profile.
    """

    configured = os.getenv("CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL", "auto").strip().lower()
    if configured not in {"auto", "strict_tool", "json_object"}:
        raise ProviderProtocolError(
            "CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL must be auto, strict_tool, or json_object"
        )
    if configured != "auto":
        return cast(Literal["strict_tool", "json_object"], configured)
    if model_id.strip().lower() in {"deepseek-v4-flash", "deepseek-chat"}:
        return "json_object"
    return "strict_tool"


def _json_schema_instruction(output_type: type[BaseModel]) -> str:
    schema = json.dumps(
        output_type.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "\n最终响应必须是严格匹配以下 JSON Schema 的一个 JSON 对象。"
        "必须使用准确的属性名；不得把来源追踪输入字段复制到输出；"
        "不得使用 Markdown 包装，也不得添加任何额外说明。\n" + schema
    )


def _validate_polish_candidate(
    candidate: BriefPolishCandidate,
    polish_mode: str,
) -> None:
    if polish_mode != "narrative_enhance" and candidate.introduced_details:
        raise ProviderProtocolError(
            "Polish candidate introduced new details outside narrative enhancement mode"
        )


def _usage_json(usage: Any) -> dict[str, Any]:
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "requests": int(getattr(usage, "requests", 0) or 0),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "cached_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(output_details, "reasoning_tokens", 0) or 0),
    }
