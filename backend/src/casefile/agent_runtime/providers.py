"""Fake and OpenAI Agents SDK adapters for Brief-to-Draft generation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_responses import OpenAIResponsesModel
from casefile_contracts import CaseFile
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from casefile.agent_runtime.models import GenerationRequest, GenerationResult, ToolMetrics
from casefile.agent_runtime.prompt import INSTRUCTIONS, generation_input
from casefile.agent_runtime.tools import GENERATION_TOOLS, GenerationToolContext
from casefile.contracts import validate_casefile


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class ProviderProtocolError(RuntimeError):
    """The provider returned a structurally unusable result or skipped a required tool."""


class FakeProvider:
    """Zero-cost deterministic provider used by tests and default benchmarks."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        request.emit("tool.started", "planning", {"tool": "plan_object_ids"})
        resolution_id = f"res_t{request.task_run_id}_01"
        metrics = ToolMetrics(
            calls=1,
            valid_calls=1,
            successful_calls=1,
            adopted_results=1,
            planned_object_ids={resolution_id},
        )
        request.emit(
            "tool.completed",
            "planning",
            {
                "tool": "plan_object_ids",
                "object_count": 1,
                "collection_counts": {"resolution_specs": 1},
            },
        )
        now = datetime.now(UTC).isoformat()
        candidate: dict[str, Any] = {
            "schema_version": "1.0",
            "casefile_id": request.casefile_id,
            "title": request.brief["one_line_concept"],
            "status": "draft",
            "version": {
                "version_id": request.version_id,
                "version_no": request.version_no,
                "parent_version_id": request.parent_version_id,
            },
            "brief_ref": {
                "brief_id": request.brief_id,
                "version": request.brief_version,
            },
            "project_profile": request.project_profile,
            "resolution_specs": [
                {
                    "id": resolution_id,
                    "title": "核心谜题",
                    "question_type": "fact_reconstruction",
                    "target_question": request.brief["core_mystery"],
                    "conclusion_mode": "unique",
                    "required_slots": [
                        {
                            "slot_id": "slot_core_answer",
                            "value_type": "text",
                            "required": True,
                        }
                    ],
                    "accepted_answers": [],
                    "required_claim_refs": [],
                    "fairness_requirements": list(request.brief.get("constraints", [])),
                    **_metadata(now),
                }
            ],
            "entities": [],
            "relationships": [],
            "locations": [],
            "events": [],
            "information_units": [],
            "claims": [],
            "hypotheses": [],
            "reasoning_paths": [],
            "phases": [],
            "constraints": [],
            "structure_locks": [],
            "content_notices": [],
            "extensions": {},
        }
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tools=metrics,
        )


class OpenAIAgentsProvider:
    """OpenAI Agents SDK implementation with a single structured-output Agent."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        return asyncio.run(self._generate(request))

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        context = GenerationToolContext(request=request)
        client = AsyncOpenAI(api_key=request.api_key, max_retries=2)
        model = OpenAIResponsesModel(model=request.model_id, openai_client=client)
        agent = Agent[GenerationToolContext](
            name="CaseFile Architect",
            instructions=INSTRUCTIONS,
            model=model,
            model_settings=ModelSettings(
                reasoning=Reasoning(effort="medium"),
                verbosity="low",
                include_usage=True,
                parallel_tool_calls=False,
            ),
            tools=GENERATION_TOOLS,
            output_type=CaseFile,
        )
        request.emit("model.started", "generating", {"model_id": request.model_id})
        result = await Runner.run(
            agent,
            generation_input(request),
            context=context,
            max_turns=request.max_turns,
            run_config=RunConfig(
                workflow_name="CaseFile brief_to_draft",
                trace_include_sensitive_data=False,
            ),
        )
        if context.plan_calls != 1:
            raise ProviderProtocolError("plan_object_ids must be called exactly once")
        output = result.final_output_as(CaseFile, raise_if_incorrect_type=True)
        candidate = _remove_absent_descriptions(output.model_dump(mode="json"))
        validate_casefile(candidate)
        candidate_ids = _candidate_object_ids(candidate)
        if context.metrics.planned_object_ids.issubset(candidate_ids):
            context.metrics.adopted_results += 1
        if context.validation_calls and context.metrics.successful_calls > 1:
            context.metrics.adopted_results += 1
        usage = result.context_wrapper.usage
        usage_json = {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": usage.input_tokens_details.cached_tokens,
            "reasoning_tokens": usage.output_tokens_details.reasoning_tokens,
        }
        request.emit("model.completed", "generating", {"usage": usage_json})
        return GenerationResult(candidate=candidate, usage=usage_json, tools=context.metrics)


def _metadata(updated_at: str) -> dict[str, Any]:
    return {
        "tags": [],
        "source_refs": [],
        "confidence": None,
        "confirmation_status": "ai_inferred",
        "created_by": {"actor_type": "agent", "actor_id": "agent_casefile_generator"},
        "updated_at": updated_at,
        "revision": 1,
    }


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
        "phases",
        "constraints",
        "structure_locks",
    )
    return {item["id"] for key in keys for item in candidate[key]}


def _remove_absent_descriptions(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_absent_descriptions(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _remove_absent_descriptions(item)
            for key, item in value.items()
            if not (key == "description" and item is None)
        }
    return value


__all__ = ["FakeProvider", "GenerationProvider", "OpenAIAgentsProvider"]
