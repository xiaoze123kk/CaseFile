"""Shared, persistence-free execution loop for CaseFile Chat candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from casefile.agent_runtime.chat_audit_validation import normalize_audit_findings
from casefile.agent_runtime.chat_intent import build_edit_target_manifest
from casefile.agent_runtime.chat_reference_autofill import autofill_chat_references
from casefile.agent_runtime.context import build_chat_context_manifest
from casefile.agent_runtime.context.assembly_render import chat_input_payload_from_assembly
from casefile.agent_runtime.context.audit_evidence import build_audit_evidence_bundle
from casefile.agent_runtime.context.thread_memory import (
    empty_thread_memory_state,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.models import (
    LEGACY_CONTEXT_POLICY_VERSION,
    CaseFileChatRequest,
    CaseFileChatResult,
    ToolMetrics,
    chat_routing_payload_as_dict,
)


class ChatProvider(Protocol):
    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...


class ChatCompletionValidationError(RuntimeError):
    """Stable public completion error with a bounded repair message."""

    def __init__(
        self,
        *,
        object_ids: tuple[str, ...] = (),
        event_ids: tuple[str, ...] = (),
        issue_ids: tuple[str, ...] = (),
        wrong_slot_object_ids: tuple[str, ...] = (),
        wrong_slot_event_ids: tuple[str, ...] = (),
        code: str = "chat_reference_validation_failed",
    ) -> None:
        self.object_ids = object_ids
        self.event_ids = event_ids
        self.issue_ids = issue_ids
        self.wrong_slot_object_ids = wrong_slot_object_ids
        self.wrong_slot_event_ids = wrong_slot_event_ids
        self.code = code
        super().__init__(code)

    def repair_feedback(self) -> str:
        return (
            "上一轮结构化结果被系统拒绝："
            f"objects={list(self.object_ids)!r}, events={list(self.event_ids)!r}, "
            f"validation_issues={list(self.issue_ids)!r}；"
            f"wrong_slot_object_ids={list(self.wrong_slot_object_ids)!r}, "
            f"wrong_slot_event_ids={list(self.wrong_slot_event_ids)!r}。"
            "未知 ID 必须删除或替换为白名单 ID；wrong-slot ID 必须移动到对应槽。"
            "这些 ID 可能出现在顶层 referenced_* 槽或 audit_findings 的证据槽中，"
            "需一并修正；只修正引用槽，"
            "保留正文结论。"
        )


@dataclass(frozen=True, slots=True)
class ChatExecutionResult:
    result: CaseFileChatResult
    usage: dict[str, Any]
    tools: ToolMetrics
    attempts: int
    repair_attempted: bool
    diagnostics: dict[str, Any]


def prepare_chat_request_artifacts(request: CaseFileChatRequest) -> CaseFileChatRequest:
    """Freeze v13 Bundle/manifest before context rendering and provider I/O."""

    if request.prompt_version != "casefile-chat-v13":
        return request
    validation = dict(request.validation)
    intent = (
        None
        if request.route is None
        else request.route.execution_profile.get("primary_intent")
    )
    if intent == "logic_audit" and "audit_evidence_bundle" not in validation:
        validation["audit_evidence_bundle"] = build_audit_evidence_bundle(
            request.casefile,
            editable_fields_by_collection=request.editable_fields_by_collection,
        ).payload
    if intent == "edit_request" and "edit_target_manifest" not in validation:
        validation["edit_target_manifest"] = build_edit_target_manifest(request).as_list()
    return replace(request, validation=validation)


def bind_chat_context_input(
    request: CaseFileChatRequest,
    *,
    frozen_input: dict[str, Any],
    thread_memory_state: dict[str, Any] | None = None,
) -> CaseFileChatRequest:
    """Bind the same policy-rendered executor payload used by Worker paths."""

    if (
        request.assembled_input is not None
        or request.context_policy_version == LEGACY_CONTEXT_POLICY_VERSION
    ):
        return request
    policy_requires_thread_memory = request.context_policy_version != LEGACY_CONTEXT_POLICY_VERSION
    if thread_memory_state is None and policy_requires_thread_memory:
        thread_memory_state = thread_memory_state_to_jsonable(empty_thread_memory_state())
    result = build_chat_context_manifest(
        policy_version=request.context_policy_version,
        frozen_input={**frozen_input, "validation": dict(request.validation)},
        input_hash=request.input_hash,
        routing=chat_routing_payload_as_dict(request),
        extra_input={
            "editable_fields_by_collection": request.editable_fields_by_collection,
            **({"thread_memory_state": thread_memory_state} if thread_memory_state else {}),
        },
        provider="deepseek" if request.model_id.startswith("deepseek") else "openai",
        model_id=request.model_id,
        hard_input_tokens=128_000,
    )
    if result.fallback is not None:
        return request
    return replace(
        request,
        assembled_input=chat_input_payload_from_assembly(
            result.assembly,
            require_thread_memory=policy_requires_thread_memory,
            dashboard=result.dashboard,
        ),
    )


def _ids(casefile: dict[str, Any]) -> tuple[set[str], set[str]]:
    events = {
        str(item["id"])
        for item in casefile.get("events", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    objects = {
        str(item["id"])
        for values in casefile.values()
        if isinstance(values, list)
        for item in values
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } - events
    return objects, events


def validate_chat_candidate(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> None:
    """Validate references visible in the frozen request before completion."""

    candidate = result.candidate.model_dump(mode="json")
    object_ids, event_ids = _ids(request.casefile)
    issue_ids = {
        str(item["issue_id"])
        for item in request.validation_issues
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }
    referenced_objects = set(candidate.get("referenced_object_ids", []))
    referenced_events = set(candidate.get("referenced_event_ids", []))
    referenced_issues = set(candidate.get("referenced_validation_issue_ids", []))
    findings = candidate.get("audit_findings", [])
    suggestions = candidate.get("suggestions", [])
    wrong_slot_object_ids = tuple(sorted(referenced_objects & event_ids))
    wrong_slot_event_ids = tuple(sorted(referenced_events & object_ids))
    manifest = request.validation.get("edit_target_manifest")
    if isinstance(manifest, list) and manifest:
        expected = {
            (item.get("object_id"), item.get("path"))
            for item in manifest
            if isinstance(item, dict)
        }
        actual = {
            (item.get("object_id"), item.get("path"))
            for item in suggestions
            if isinstance(item, dict)
        }
        if len(actual) != len(suggestions) or expected != actual:
            raise ChatCompletionValidationError(code="edit_target_manifest_incomplete")
    if findings:
        bundle = request.validation.get("audit_evidence_bundle")
        evidence_object_ids = object_ids
        evidence_event_ids = event_ids
        if isinstance(bundle, dict) and isinstance(bundle.get("included_ids"), list):
            allowed = {
                str(item) for item in bundle["included_ids"] if isinstance(item, str)
            }
            tool_ids = {
                str(item)
                for item in bundle.get("tool_result_ids", [])
                if isinstance(item, str)
            }
            allowed.update(tool_ids)
            evidence_object_ids = object_ids & allowed
            evidence_event_ids = event_ids & allowed
        (
            normalized,
            finding_missing_objects,
            finding_missing_events,
            finding_missing_issues,
        ) = normalize_audit_findings(
            findings,
            frozen_object_ids=evidence_object_ids,
            frozen_event_ids=evidence_event_ids,
            known_issue_ids=issue_ids,
            suggestion_finding_refs=[
                item.get("finding_ref") if isinstance(item, dict) else None
                for item in suggestions
            ],
        )
        for finding in normalized:
            referenced_objects.update(finding["evidence_object_ids"])
            referenced_events.update(finding["evidence_event_ids"])
            referenced_issues.update(finding["evidence_validation_issue_ids"])
        referenced_objects.update(finding_missing_objects)
        referenced_events.update(finding_missing_events)
        referenced_issues.update(finding_missing_issues)
        if isinstance(bundle, dict):
            allowed_fields = bundle.get("suggestion_allowed_fields")
            if isinstance(allowed_fields, dict):
                for suggestion in suggestions:
                    if not isinstance(suggestion, dict):
                        continue
                    object_id = suggestion.get("object_id")
                    path = suggestion.get("path")
                    if not isinstance(object_id, str) or not isinstance(path, str):
                        continue
                    top_level = path.removeprefix("/").split("/", 1)[0]
                    allowed_raw = allowed_fields.get(object_id, [])
                    allowed_paths: list[str] = (
                        [str(value) for value in allowed_raw]
                        if isinstance(allowed_raw, list)
                        else []
                    )
                    if top_level not in allowed_paths and path not in allowed_paths:
                        raise ChatCompletionValidationError(
                            code="audit_suggestion_field_not_allowed"
                        )
                    if object_id not in referenced_objects:
                        raise ChatCompletionValidationError(
                            code="audit_suggestion_reference_missing"
                        )
    missing_objects: tuple[str, ...] = tuple(sorted(referenced_objects - object_ids))
    missing_events: tuple[str, ...] = tuple(sorted(referenced_events - event_ids))
    missing_issues: tuple[str, ...] = tuple(sorted(referenced_issues - issue_ids))
    if missing_objects or missing_events or missing_issues:
        raise ChatCompletionValidationError(
            object_ids=missing_objects,
            event_ids=missing_events,
            issue_ids=missing_issues,
            wrong_slot_object_ids=wrong_slot_object_ids,
            wrong_slot_event_ids=wrong_slot_event_ids,
        )
    if wrong_slot_object_ids or wrong_slot_event_ids:
        raise ChatCompletionValidationError(
            wrong_slot_object_ids=wrong_slot_object_ids,
            wrong_slot_event_ids=wrong_slot_event_ids,
        )


def _normalize_finding_reference_slots(
    findings: list[dict[str, Any]],
    object_ids: set[str],
    event_ids: set[str],
) -> list[dict[str, Any]]:
    """Move known event/object IDs to the typed evidence slot they belong to."""

    normalized: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            normalized.append(finding)
            continue
        item = dict(finding)
        objects = list(item.get("evidence_object_ids", []))
        events = list(item.get("evidence_event_ids", []))
        moved_events = [value for value in objects if value in event_ids]
        moved_objects = [value for value in events if value in object_ids]
        if moved_events:
            objects = [value for value in objects if value not in event_ids]
            events.extend(value for value in moved_events if value not in events)
        if moved_objects:
            events = [value for value in events if value not in object_ids]
            objects.extend(value for value in moved_objects if value not in objects)
        item["evidence_object_ids"] = objects
        item["evidence_event_ids"] = events
        normalized.append(item)
    return normalized


def _normalize_reference_slots(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Correct known IDs placed in the wrong typed reference slot.

    This is a type correction, not a guessed reference: the frozen CaseFile
    itself is the authority. Unknown IDs remain untouched and reach the bounded
    repair path.
    """

    candidate = result.candidate
    payload = candidate.model_dump(mode="json")
    object_ids, event_ids = _ids(request.casefile)
    objects = list(payload.get("referenced_object_ids", []))
    events = list(payload.get("referenced_event_ids", []))
    moved_events = [item for item in objects if item in event_ids]
    moved_objects = [item for item in events if item in object_ids]
    if moved_events:
        objects = [item for item in objects if item not in event_ids]
        events.extend(item for item in moved_events if item not in events)
    if moved_objects:
        events = [item for item in events if item not in object_ids]
        objects.extend(item for item in moved_objects if item not in objects)
    findings = payload.get("audit_findings", [])
    if isinstance(findings, list):
        normalized_findings = _normalize_finding_reference_slots(
            findings, object_ids, event_ids
        )
        if normalized_findings != findings:
            payload["audit_findings"] = normalized_findings
    # Fill only empty top-level slots, preserving all existing legal entries.
    if not objects or not events:
        auto_objects, auto_events = autofill_chat_references(
            str(payload.get("answer", "")),
            request.casefile,
        )
        if not objects:
            objects.extend(item for item in auto_objects if item not in objects)
        if not events:
            events.extend(item for item in auto_events if item not in events)
    updates: dict[str, Any] = {}
    if objects != payload.get("referenced_object_ids", []):
        updates["referenced_object_ids"] = objects
    if events != payload.get("referenced_event_ids", []):
        updates["referenced_event_ids"] = events
    if payload.get("audit_findings", []) != candidate.model_dump(mode="json").get(
        "audit_findings", []
    ):
        updates["audit_findings"] = payload["audit_findings"]
    if not updates:
        return result
    return replace(result, candidate=candidate.model_copy(update=updates))


def _apply_deterministic_audit_gate(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Apply the server-owned clean-no-op conclusion before persistence.

    A model cannot manufacture an audit finding when the frozen deterministic
    verification and the evidence bundle both say there is no candidate pair.
    Keep the prose answer (it is useful to the author), but remove structured
    findings and patches from the candidate that proceeds to completion.
    """

    route = request.route
    if route is None or route.execution_profile.get("primary_intent") != "logic_audit":
        return result
    bundle = request.validation.get("audit_evidence_bundle")
    if not isinstance(bundle, dict):
        return result
    if bundle.get("clean_noop_eligible") is not True:
        return result
    if bundle.get("deterministic_findings"):
        return result
    if bundle.get("candidate_pairs") or bundle.get("tool_counterevidence"):
        return result
    candidate = result.candidate
    updates: dict[str, Any] = {"suggestions": [], "audit_findings": []}
    # v1 candidates do not define audit_findings; Pydantic ignores no fields,
    # so only update the slot when it exists on the concrete model.
    if not hasattr(candidate, "audit_findings"):
        updates.pop("audit_findings")
    return replace(result, candidate=candidate.model_copy(update=updates))


def _merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
            else:
                merged[key] = value
    return merged


def _merge_tools(records: list[ToolMetrics]) -> ToolMetrics:
    merged = ToolMetrics()
    for record in records:
        merged.calls += record.calls
        merged.valid_calls += record.valid_calls
        merged.successful_calls += record.successful_calls
        merged.adopted_results += record.adopted_results
        merged.planned_object_ids.update(record.planned_object_ids)
    return merged


class ChatExecutionRunner:
    """Execute and repair one frozen request without owning persistence."""

    def __init__(self, provider: ChatProvider) -> None:
        self.provider = provider

    def run(
        self,
        request: CaseFileChatRequest,
        *,
        complete: Callable[[CaseFileChatResult], None] | None = None,
    ) -> ChatExecutionResult:
        request = prepare_chat_request_artifacts(request)
        usages: list[dict[str, Any]] = []
        tools: list[ToolMetrics] = []
        repair_attempted = False
        for attempt in (1, 2):
            try:
                result = self.provider.chat(request)
            except Exception as error:
                # Providers may fail after emitting a usage/tool snapshot.
                # Preserve those snapshots for the terminal diagnostic.
                failed_usage = getattr(error, "usage", None)
                failed_tools = getattr(error, "tools", None)
                if isinstance(failed_usage, dict):
                    usages.append(failed_usage)
                if isinstance(failed_tools, ToolMetrics):
                    tools.append(failed_tools)
                _attach_failure_metrics(
                    error,
                    usages,
                    tools,
                    attempts=len(usages),
                    repair_attempted=repair_attempted,
                )
                raise
            usages.append(result.usage)
            tools.append(result.tools)
            result = _normalize_reference_slots(request, result)
            result = _apply_deterministic_audit_gate(request, result)
            try:
                validate_chat_candidate(request, result)
                if complete is not None:
                    complete(result)
            except Exception as error:
                validation = _as_validation_error(error)
                if validation is None or attempt == 2:
                    _attach_failure_metrics(
                        error,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                    )
                    raise
                repair_attempted = True
                request.emit(
                    "model.reference_repair_started",
                    "repairing",
                    {
                        "repair_no": 1,
                        "max_repairs": 1,
                        "unknown_object_ids": list(validation.object_ids),
                        "unknown_event_ids": list(validation.event_ids),
                        "unknown_issue_ids": list(validation.issue_ids),
                        "wrong_slot_object_ids": list(validation.wrong_slot_object_ids),
                        "wrong_slot_event_ids": list(validation.wrong_slot_event_ids),
                    },
                )
                request = replace(request, repair_feedback=(validation.repair_feedback(),))
                continue
            return ChatExecutionResult(
                result=result,
                usage=_merge_usage(usages),
                tools=_merge_tools(tools),
                attempts=attempt,
                repair_attempted=repair_attempted,
                diagnostics={"error_code": None, "attempts": attempt},
            )
        raise AssertionError("unreachable")


def _attach_failure_metrics(
    error: Exception,
    usages: list[dict[str, Any]],
    tools: list[ToolMetrics],
    *,
    attempts: int,
    repair_attempted: bool,
) -> None:
    """Best-effort diagnostic attachment without changing public exceptions."""

    try:
        error.__dict__["usage"] = _merge_usage(usages)
        error.__dict__["tools"] = _merge_tools(tools)
        error.__dict__["attempts"] = attempts
        error.__dict__["repair_attempted"] = repair_attempted
    except (AttributeError, TypeError):
        return


def _as_validation_error(error: Exception) -> ChatCompletionValidationError | None:
    if isinstance(error, ChatCompletionValidationError):
        return error
    object_ids = getattr(error, "object_ids", None)
    event_ids = getattr(error, "event_ids", None)
    issue_ids = getattr(error, "issue_ids", None)
    wrong_slot_object_ids = getattr(error, "wrong_slot_object_ids", None)
    wrong_slot_event_ids = getattr(error, "wrong_slot_event_ids", None)
    if (
        isinstance(object_ids, (list, tuple))
        and isinstance(event_ids, (list, tuple))
        and isinstance(issue_ids, (list, tuple))
    ):
        return ChatCompletionValidationError(
            object_ids=tuple(str(item) for item in object_ids),
            event_ids=tuple(str(item) for item in event_ids),
            issue_ids=tuple(str(item) for item in issue_ids),
            wrong_slot_object_ids=tuple(
                str(item) for item in (wrong_slot_object_ids or ())
            ),
            wrong_slot_event_ids=tuple(
                str(item) for item in (wrong_slot_event_ids or ())
            ),
        )
    return None


__all__ = [
    "ChatCompletionValidationError",
    "ChatExecutionResult",
    "ChatExecutionRunner",
    "prepare_chat_request_artifacts",
    "validate_chat_candidate",
]
