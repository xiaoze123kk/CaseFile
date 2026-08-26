"""Worker orchestration for optional bounded closure-repair rounds."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime import AgentProvider, CaseFileChatResult, ProviderRepairProposer
from casefile.agent_runtime.models import EventSink
from casefile.application.closure_repair import (
    ClosureRepairMode,
    closure_repair_envelope,
    primary_mutation_from_mutation_set,
    primary_mutation_from_suggestions,
)
from casefile.data_postgres.models import TaskRun
from casefile.domain.logical_mutation import MutationSet
from casefile.domain.logical_mutation.repair import ClosureRepairResult, run_closure_repair
from casefile.domain.verification_engine import VerificationEngine
from casefile.worker.support import _required_provider_binding


def execute_chat_closure_repair(
    task: TaskRun,
    result: CaseFileChatResult,
    *,
    provider: AgentProvider,
    api_key: str,
    mode: ClosureRepairMode,
    emit: EventSink,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if mode == "off":
        return None, {}
    frozen = task.input_jsonb.get("casefile")
    intent = task.input_jsonb.get("message")
    if not isinstance(frozen, dict) or not isinstance(intent, str) or not intent.strip():
        raise RuntimeError("Closure repair requires frozen CaseFile and intent")
    suggestions = _suggestions(result)
    primary = primary_mutation_from_suggestions(
        frozen,
        draft_id=task.draft_id,
        base_revision=task.input_draft_revision,
        task_run_id=task.id,
        suggestions=suggestions,
    )
    envelope, usage, _ = execute_mutation_closure_repair(
        task,
        primary,
        provider=provider,
        api_key=api_key,
        mode=mode,
        emit=emit,
    )
    return envelope, usage


def execute_mutation_closure_repair(
    task: TaskRun,
    primary_mutation: MutationSet,
    *,
    provider: AgentProvider,
    api_key: str,
    mode: ClosureRepairMode,
    emit: EventSink,
) -> tuple[dict[str, Any] | None, dict[str, Any], ClosureRepairResult | None]:
    """Run the existing bounded repair protocol for a formal MutationSet."""

    if mode == "off":
        return None, {}, None
    frozen = task.input_jsonb.get("casefile")
    intent = task.input_jsonb.get("message")
    if not isinstance(frozen, dict) or not isinstance(intent, str) or not intent.strip():
        raise RuntimeError("Closure repair requires frozen CaseFile and intent")
    primary = primary_mutation_from_mutation_set(primary_mutation)
    verifier = VerificationEngine(
        profile="fast", closure_policy_version=primary.closure_policy_version
    )
    original = verifier.simulate_mutation_set(frozen, primary)
    proposer = ProviderRepairProposer(
        provider=provider,
        model_id=_required_provider_binding(task)[1],
        api_key=api_key,
        emit=emit,
        max_turns=1,
        network_retries=int(task.budget_jsonb.get("network_retries", 2)),
    )
    repair = run_closure_repair(
        frozen,
        primary,
        original,
        proposer,
        original_intent=intent,
    )
    envelope = closure_repair_envelope(mode=mode, result=repair)
    emit(
        "closure_repair.completed",
        "closure_repair",
        {
            "mode": mode,
            "status": repair.status,
            "reason_code": repair.reason_code,
            "round_count": len(repair.rounds),
            "companion_operation_count": len(repair.companion_operations),
            "final_candidate_hash": envelope["final_candidate_hash"],
        },
    )
    usage = _merge_usage(item.usage for item in proposer.results)
    return envelope, usage, repair


def _suggestions(result: CaseFileChatResult) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for suggestion in result.candidate.suggestions:
        try:
            value = json.loads(suggestion.value_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("Closure repair suggestion value_json is invalid") from error
        suggestions.append(
            {
                "object_id": suggestion.object_id,
                "path": suggestion.path,
                "value": value,
                "reason": suggestion.reason,
            }
        )
    return suggestions


def _merge_usage(records: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
            else:
                merged[key] = value
    return merged


__all__ = ["execute_chat_closure_repair", "execute_mutation_closure_repair"]
