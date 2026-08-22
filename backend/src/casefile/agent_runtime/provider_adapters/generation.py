"""Partitioned brief-to-draft generation orchestration and validation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from pydantic import BaseModel, create_model

from casefile.agent_runtime.brief_to_draft_v8.workflow import run_v8_generation
from casefile.agent_runtime.brief_to_draft_v9.workflow import run_v9_generation
from casefile.agent_runtime.brief_to_draft_v10.workflow import run_v10_generation
from casefile.agent_runtime.brief_to_draft_v11.workflow import run_v11_generation
from casefile.agent_runtime.brief_to_draft_v12.workflow import run_v12_generation
from casefile.agent_runtime.brief_to_draft_v13.workflow import run_v13_generation
from casefile.agent_runtime.brief_to_draft_v14.workflow import run_v14_generation
from casefile.agent_runtime.brief_to_draft_v15.workflow import run_v15_generation
from casefile.agent_runtime.models import (
    GenerationPlan,
    GenerationRequest,
    GenerationResult,
    StrictAgentOutput,
    ToolMetrics,
)
from casefile.agent_runtime.prompt_repository import (
    system_prompt_for_task,
)
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError
from casefile.agent_runtime.provider_adapters.shared import (
    _remove_absent_optional_fields,
    _run_auxiliary_agent,
    _validate_generated_descriptions,
)
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.contracts.validation import COLLECTION_OBJECT_TYPES
from casefile_contracts import (
    CaseFile,
)

_PARTITION_FIELDS: dict[str, tuple[str, ...]] = {
    "story": ("entities", "relationships", "locations", "events"),
    "reasoning": (
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
    ),
    "governance": (
        "resolution_specs",
        "constraints",
        "structure_locks",
        "content_notices",
        "extensions",
    ),
}


_COLLECTION_PREFIXES = {
    "resolution_specs": "res",
    "entities": "ent",
    "relationships": "rel",
    "locations": "loc",
    "events": "evt",
    "information_units": "info",
    "claims": "claim",
    "hypotheses": "hyp",
    "reasoning_paths": "path",
    "constraints": "con",
    "structure_locks": "lock",
}


def _partition_output_model(partition: str) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name in _PARTITION_FIELDS[partition]:
        model_field = CaseFile.model_fields[field_name]
        fields[field_name] = (model_field.annotation, ...)
    return cast(
        type[BaseModel],
        create_model(  # type: ignore[call-overload]
            f"CaseFile{partition.title()}Partition",
            __base__=StrictAgentOutput,
            **fields,
        ),
    )


_PARTITION_MODELS = {
    partition: _partition_output_model(partition) for partition in _PARTITION_FIELDS
}


_BRIEF_TO_DRAFT_RUNNERS = {
    "brief-to-draft-v9": run_v9_generation,
    "brief-to-draft-v10": run_v10_generation,
    "brief-to-draft-v11": run_v11_generation,
    "brief-to-draft-v12": run_v12_generation,
    "brief-to-draft-v13": run_v13_generation,
    "brief-to-draft-v14": run_v14_generation,
    "brief-to-draft-v15": run_v15_generation,
}


def _brief_to_draft_runner(prompt_version: str) -> Any:
    """Resolve the workflow runner for one frozen prompt version."""

    return _BRIEF_TO_DRAFT_RUNNERS.get(prompt_version, run_v8_generation)


async def _run_partitioned_generation(
    request: GenerationRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    structured_output: bool,
    tracing_disabled: bool,
) -> GenerationResult:
    """Generate one complete CaseFile through a shared plan and isolated partitions."""

    instructions = system_prompt_for_task("brief_to_draft", request.prompt_version)
    strategy = (
        request.candidate_strategy.value
        if hasattr(request.candidate_strategy, "value")
        else str(request.candidate_strategy)
    )
    frozen_context = {
        "schema_version": request.schema_version,
        "casefile_id": request.casefile_id,
        "brief_ref": {"brief_id": request.brief_id, "version": request.brief_version},
        "version": {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        },
        "status": "draft",
        "candidate_strategy": strategy,
        "candidate_strategy_version": request.candidate_strategy_version,
    }
    usage_records: list[dict[str, Any]] = []

    request.emit("generation.plan_started", "planning", {"attempt": 1})
    plan: GenerationPlan | None = None
    for attempt in range(1, 3):
        try:
            plan_json, usage = await _run_auxiliary_agent(
                request,
                model=model,
                model_settings=model_settings,
                instructions=(
                    instructions + "\n当前阶段：只返回紧凑对象计划。每个 local_key 必须唯一，"
                    "referenced_keys 只能引用同一计划中的 local_key。"
                    "\nGenerationPlan constraints: collection MUST be exactly one of "
                    "resolution_specs, entities, relationships, locations, events, "
                    "information_units, claims, hypotheses, reasoning_paths, constraints, "
                    "structure_locks. local_key MUST match ^[a-z][a-z0-9_]*$, MUST be unique, "
                    "and referenced_keys MUST only use local_key values declared in this plan."
                ),
                input_text=json.dumps(
                    {
                        "brief": request.brief,
                        "frozen_context": frozen_context,
                        "repair": attempt == 2,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                output_type=GenerationPlan,
                stage="planning",
                structured_output=structured_output,
                tracing_disabled=tracing_disabled,
            )
            usage_records.append(usage)
            plan = GenerationPlan.model_validate(plan_json)
            break
        except Exception as error:
            if attempt == 1:
                raise
            request.emit(
                "generation.plan_repair_started",
                "repairing",
                {"attempt": 2, "error_type": type(error).__name__},
            )
    if plan is None:
        raise ProviderProtocolError("Generation plan was not produced")

    id_directory = _allocate_plan_ids(request.task_run_id, plan)
    planned_object_types = {
        id_directory[item.local_key]: COLLECTION_OBJECT_TYPES[item.collection]
        for item in plan.objects
    }
    plan_payload = {
        "title": plan.title,
        "objects": [
            {
                **item.model_dump(mode="json"),
                "object_id": id_directory[item.local_key],
                "referenced_object_ids": [id_directory[ref] for ref in item.referenced_keys],
            }
            for item in plan.objects
        ],
    }
    request.emit(
        "generation.plan_completed",
        "planning",
        {
            "object_count": len(plan.objects),
            "collection_counts": _plan_collection_counts(plan),
        },
    )

    updated_at = datetime.now(UTC).isoformat()

    async def generate_partition(
        partition: str,
        *,
        issues: list[dict[str, Any]] | None = None,
        previous: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        output_type = _PARTITION_MODELS[partition]
        last_error: Exception | None = None
        for attempt in range(1, 3):
            started_at = perf_counter()
            request.emit(
                "generation.partition_started",
                f"generating_{partition}",
                {"partition": partition, "attempt": attempt, "targeted_repair": bool(issues)},
            )
            try:
                payload: dict[str, Any] = {
                    "brief": request.brief,
                    "frozen_context": frozen_context,
                    "updated_at": updated_at,
                    "shared_plan": plan_payload,
                    "partition": partition,
                    "required_fields": list(_PARTITION_FIELDS[partition]),
                }
                if issues:
                    payload["repair_feedback"] = issues
                    payload["previous_partition"] = previous
                elif attempt == 2 and isinstance(last_error, ContractValidationError):
                    payload["repair_feedback"] = last_error.errors
                elif attempt == 2:
                    payload["repair_feedback"] = [
                        {
                            "code": "partition_output_invalid",
                            "path": f"/{partition}",
                            "message": "上一响应无法解析或不符合当前分区结构",
                        }
                    ]
                partition_json, usage = await _run_auxiliary_agent(
                    request,
                    model=model,
                    model_settings=model_settings,
                    instructions=(
                        instructions
                        + f"\n当前阶段：只生成 {partition} 分区，且必须完整返回："
                        + ", ".join(_PARTITION_FIELDS[partition])
                        + "。必须恰好使用 shared_plan 中属于这些集合的全部 object_id。"
                    ),
                    input_text=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    output_type=output_type,
                    stage=f"generating_{partition}",
                    structured_output=structured_output,
                    tracing_disabled=tracing_disabled,
                    planned_object_types=planned_object_types,
                )
                validated = output_type.model_validate(partition_json).model_dump(mode="json")
                validated, discarded_ids = _retain_planned_objects(
                    validated,
                    set(id_directory.values()),
                )
                if discarded_ids:
                    request.emit(
                        "generation.unplanned_objects_discarded",
                        f"generating_{partition}",
                        {
                            "partition": partition,
                            "object_ids": discarded_ids,
                            "object_count": len(discarded_ids),
                        },
                    )
                request.emit(
                    "generation.partition_completed",
                    f"generating_{partition}",
                    {
                        "partition": partition,
                        "attempt": attempt,
                        "targeted_repair": bool(issues),
                        "elapsed_ms": round((perf_counter() - started_at) * 1000),
                    },
                )
                return validated, usage
            except Exception as error:
                last_error = error
                request.emit(
                    "generation.partition_failed",
                    f"generating_{partition}",
                    {
                        "partition": partition,
                        "attempt": attempt,
                        "targeted_repair": bool(issues),
                        "elapsed_ms": round((perf_counter() - started_at) * 1000),
                        "error_type": type(error).__name__,
                    },
                )
                if issues or attempt == 1:
                    raise
                request.emit(
                    "generation.partition_repair_started",
                    "repairing",
                    {"partition": partition, "attempt": 2, "error_type": type(error).__name__},
                )
        raise last_error or ProviderProtocolError("Partition generation failed")

    request.emit(
        "generation.partitions_started",
        "generating",
        {"partitions": list(_PARTITION_FIELDS)},
    )
    partition_pairs = await asyncio.gather(
        *(generate_partition(partition) for partition in _PARTITION_FIELDS)
    )
    partitions = {
        partition: partition_pairs[index][0] for index, partition in enumerate(_PARTITION_FIELDS)
    }
    usage_records.extend(pair[1] for pair in partition_pairs)
    request.emit("generation.assembly_started", "assembling", {})
    candidate = _assemble_partitioned_candidate(
        request,
        plan.title,
        partitions,
    )
    request.emit(
        "generation.assembly_completed",
        "assembling",
        {"partitions": list(_PARTITION_FIELDS), "object_count": len(id_directory)},
    )

    request.emit("generation.validation_started", "validating", {})
    try:
        _validate_partitioned_candidate(candidate, id_directory)
    except ContractValidationError as error:
        issues_by_partition = _partition_issues(error.errors)
        if not issues_by_partition:
            raise
        repaired_pairs = await asyncio.gather(
            *(
                generate_partition(
                    partition,
                    issues=issues,
                    previous=partitions[partition],
                )
                for partition, issues in issues_by_partition.items()
            )
        )
        for index, partition in enumerate(issues_by_partition):
            partitions[partition] = repaired_pairs[index][0]
            usage_records.append(repaired_pairs[index][1])
        candidate = _assemble_partitioned_candidate(request, plan.title, partitions)
        try:
            _validate_partitioned_candidate(candidate, id_directory)
        except ContractValidationError as repaired_error:
            pruned_paths = _prune_invalid_reference_list_items(
                candidate,
                repaired_error.errors,
            )
            if not pruned_paths:
                raise
            request.emit(
                "generation.invalid_references_pruned",
                "repairing",
                {"paths": pruned_paths, "reference_count": len(pruned_paths)},
            )
            _validate_partitioned_candidate(candidate, id_directory)
    request.emit(
        "generation.validation_completed",
        "validating",
        {"object_count": len(id_directory)},
    )

    metrics = ToolMetrics(
        calls=len(usage_records),
        valid_calls=len(usage_records),
        successful_calls=len(usage_records),
        adopted_results=1,
        planned_object_ids=set(id_directory.values()),
    )
    request.emit(
        "generation.assembled",
        "validating",
        {"partitions": list(_PARTITION_FIELDS), "object_count": len(id_directory)},
    )
    return GenerationResult(
        candidate=candidate,
        usage=_merge_usage(usage_records),
        tools=metrics,
    )


def _allocate_plan_ids(task_run_id: int, plan: GenerationPlan) -> dict[str, str]:
    counters = {collection: 0 for collection in _COLLECTION_PREFIXES}
    allocated: dict[str, str] = {}
    for item in plan.objects:
        counters[item.collection] += 1
        allocated[item.local_key] = (
            f"{_COLLECTION_PREFIXES[item.collection]}_t{task_run_id}_"
            f"{counters[item.collection]:02d}"
        )
    return allocated


def _plan_collection_counts(plan: GenerationPlan) -> dict[str, int]:
    counts = {collection: 0 for collection in _COLLECTION_PREFIXES}
    for item in plan.objects:
        counts[item.collection] += 1
    return counts


def _retain_planned_objects(
    partition: dict[str, Any],
    planned_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    discarded_ids: list[str] = []
    for collection in _COLLECTION_PREFIXES:
        if collection not in partition:
            continue
        retained: list[Any] = []
        for item in partition[collection]:
            object_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(object_id, str) and object_id in planned_ids:
                retained.append(item)
                continue
            if isinstance(object_id, str):
                discarded_ids.append(object_id)
        partition[collection] = retained
    return partition, sorted(discarded_ids)


def _assemble_partitioned_candidate(
    request: GenerationRequest,
    title: str,
    partitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "schema_version": request.schema_version,
        "casefile_id": request.casefile_id,
        "title": title,
        "status": "draft",
        "version": {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        },
        "brief_ref": {"brief_id": request.brief_id, "version": request.brief_version},
    }
    for partition in _PARTITION_FIELDS:
        candidate.update(partitions[partition])
    return cast(dict[str, Any], _remove_absent_optional_fields(candidate))


def _validate_partitioned_candidate(
    candidate: dict[str, Any],
    id_directory: dict[str, str],
) -> None:
    issues: list[dict[str, Any]] = []
    for validator in (validate_casefile, _validate_generated_descriptions):
        try:
            validator(candidate)
        except ContractValidationError as error:
            issues.extend(error.errors)
    issues.extend(_planned_object_id_issues(candidate, id_directory))
    if issues:
        raise ContractValidationError(issues)


def _planned_object_id_issues(
    candidate: dict[str, Any],
    id_directory: dict[str, str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    expected_ids = set(id_directory.values())
    for collection, prefix in _COLLECTION_PREFIXES.items():
        expected = {object_id for object_id in expected_ids if object_id.startswith(f"{prefix}_")}
        actual = {
            str(item.get("id"))
            for item in candidate.get(collection, [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        if actual == expected:
            continue
        issues.append(
            {
                "code": "planned_object_ids_mismatch",
                "path": f"/{collection}",
                "message": (
                    "候选对象 ID 与计划不一致；"
                    f"缺少：{sorted(expected - actual)!r}；"
                    f"多出：{sorted(actual - expected)!r}。"
                ),
            }
        )
    return issues


def _partition_issues(
    issues: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    field_to_partition = {
        field: partition for partition, fields in _PARTITION_FIELDS.items() for field in fields
    }
    for issue in issues:
        path = str(issue.get("path", ""))
        first = path.lstrip("/").split("/", 1)[0]
        partition = field_to_partition.get(first)
        if partition is not None:
            grouped.setdefault(partition, []).append(issue)
    return grouped


def _prune_invalid_reference_list_items(
    candidate: dict[str, Any],
    issues: list[dict[str, Any]],
) -> list[str]:
    removable_codes = {
        "missing_reference",
        "reference_type_mismatch",
        "self_reference",
    }
    removals: dict[tuple[str, ...], set[int]] = {}
    for issue in issues:
        if issue.get("code") not in removable_codes:
            continue
        path = str(issue.get("path", ""))
        parts = tuple(
            part.replace("~1", "/").replace("~0", "~")
            for part in path.lstrip("/").split("/")
            if part
        )
        if not parts or not parts[-1].isdigit():
            continue
        removals.setdefault(parts[:-1], set()).add(int(parts[-1]))

    pruned_paths: list[str] = []
    for parent_parts, indexes in removals.items():
        parent: Any = candidate
        try:
            for part in parent_parts:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        if not isinstance(parent, list):
            continue
        for index in sorted(indexes, reverse=True):
            if index < 0 or index >= len(parent):
                continue
            parent.pop(index)
            pointer = "/" + "/".join(
                part.replace("~", "~0").replace("/", "~1") for part in (*parent_parts, str(index))
            )
            pruned_paths.append(pointer)
    return sorted(pruned_paths)


def _merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for record in records:
        for key in tuple(merged):
            value = record.get(key, 0)
            if isinstance(value, int):
                merged[key] += value
    merged["partition_calls"] = len(records)
    return merged
