"""Shared, persistence-free execution loop for CaseFile Chat candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from casefile.agent_runtime.chat_audit_validation import (
    ChatAuditValidationError,
    normalize_audit_findings,
    rank_and_dedupe_audit_findings,
)
from casefile.agent_runtime.chat_intent import (
    build_edit_target_manifest,
    route_allows_suggestions,
    route_public_payload,
    route_suggestion_policy,
)
from casefile.agent_runtime.chat_reference_autofill import autofill_chat_references
from casefile.agent_runtime.chat_safe_patches import (
    SafePatchRegistry,
    canonicalize_value_json,
    materialize_unique_safe_patches,
    safe_patch_registry_from_dict,
    server_gate_audit_suggestions,
)
from casefile.agent_runtime.chat_tools import check_patch_proposal
from casefile.agent_runtime.chat_validation import (
    RepairPlan,
    ValidationIssue,
    plan_repairs,
    target_label,
)
from casefile.agent_runtime.context import build_chat_context_manifest
from casefile.agent_runtime.context.assembly_render import chat_input_payload_from_assembly
from casefile.agent_runtime.context.audit_evidence import build_audit_evidence_bundle
from casefile.agent_runtime.context.thread_memory import (
    empty_thread_memory_state,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.models import (
    LEGACY_CONTEXT_POLICY_VERSION,
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatResult,
    CaseFileChatSuggestionCandidateV2,
    CaseFileChatTargetLockedRepairOutput,
    ToolMetrics,
    chat_routing_payload_as_dict,
)

_TARGET_LOCKED_REPAIR_CODES = frozenset({"audit_repair_expectation_missing_target"})


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
        issues: tuple[ValidationIssue, ...] = (),
        repair_plan: RepairPlan | None = None,
    ) -> None:
        self.object_ids = object_ids
        self.event_ids = event_ids
        self.issue_ids = issue_ids
        self.wrong_slot_object_ids = wrong_slot_object_ids
        self.wrong_slot_event_ids = wrong_slot_event_ids
        self.code = code
        self.issues = issues
        self.repair_plan = repair_plan or plan_repairs(issues)
        super().__init__(code)

    def repair_feedback(self) -> str:
        if self.issues:
            issue_payloads = [issue.as_dict() for issue in self.issues]
            return (
                "上一轮结构化结果未通过系统校验。"
                f"validation_issues={issue_payloads!r}；"
                f"repair_plan={self.repair_plan.as_dict()!r}。"
                "严格按 repair_plan 修复：preserve 项原样保留，add 项必须补齐，"
                "remove 项必须删除，replace 项必须使用指定冻结补丁，"
                "fix 项逐条修正；不得改写其他已通过内容。"
            )
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

    if request.prompt_version not in {
        "casefile-chat-v13",
        "casefile-chat-v14",
        "casefile-chat-v15",
    }:
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
            missing = expected - actual
            extra = actual - expected
            preserve = expected & actual
            issue = ValidationIssue(
                code="edit_target_manifest_incomplete",
                stage="edit",
                path="/suggestions",
                message="修改建议没有完整且唯一地覆盖冻结编辑目标。",
                repairable=True,
                details={
                    "expected": sorted(target_label(*item) for item in expected),
                    "actual": sorted(target_label(*item) for item in actual),
                    "missing": sorted(target_label(*item) for item in missing),
                    "extra": sorted(target_label(*item) for item in extra),
                    "preserve": sorted(target_label(*item) for item in preserve),
                },
            )
            raise ChatCompletionValidationError(
                code=issue.code,
                issues=(issue,),
            )
    patch_issues: list[ValidationIssue] = []
    for index, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict):
            continue
        object_id = suggestion.get("object_id")
        path = suggestion.get("path")
        value_json = suggestion.get("value_json")
        if not (
            isinstance(object_id, str)
            and object_id
            and isinstance(path, str)
            and path
            and isinstance(value_json, str)
            and value_json
        ):
            continue
        check = check_patch_proposal(
            request,
            object_id,
            path,
            value_json,
            require_path_exists=True,
        )
        if check.reason_code is None:
            continue
        target = target_label(object_id, path)
        patch_issues.append(
            ValidationIssue(
                code="chat_suggestion_server_gate_failed",
                stage="patch",
                path=f"/suggestions/{index}",
                message="修改建议未通过服务器字段和值门禁。",
                repairable=True,
                details={
                    "missing": [target],
                    "replace": [
                        {
                            "object_id": object_id,
                            "path": path,
                            "reason_code": check.reason_code,
                            "allowed_fields": list(check.allowed_fields),
                        }
                    ],
                },
            )
        )
    if patch_issues:
        raise ChatCompletionValidationError(
            code=patch_issues[0].code,
            issues=tuple(patch_issues),
        )
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
            deterministic_pairs=(
                bundle.get("candidate_pairs")
                if isinstance(bundle, dict)
                and isinstance(bundle.get("candidate_pairs"), list)
                else None
            ),
            require_deterministic_pair=request.prompt_version == "casefile-chat-v15",
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
                    reference_slot = (
                        referenced_events if object_id in event_ids else referenced_objects
                    )
                    if object_id not in reference_slot:
                        raise ChatCompletionValidationError(
                            code="audit_suggestion_reference_missing"
                        )
        if (
            request.prompt_version in {"casefile-chat-v14", "casefile-chat-v15"}
            and request.route is not None
            and request.route.execution_profile.get("primary_intent") == "logic_audit"
        ):
            if request.prompt_version == "casefile-chat-v15":
                registry = (
                    safe_patch_registry_from_dict(result.safe_patch_registry)
                    if isinstance(result.safe_patch_registry, dict)
                    else None
                )
                simulation_issues = _audit_server_gate_issues(suggestions, registry)
            else:
                ledger = result.tool_ledger or request.frozen_tool_ledger
                registry = None
                if isinstance(result.safe_patch_registry, dict):
                    registry = safe_patch_registry_from_dict(result.safe_patch_registry)
                elif isinstance(request.safe_patch_registry, dict):
                    registry = safe_patch_registry_from_dict(request.safe_patch_registry)
                simulation_issues = (
                    _audit_simulation_issues(suggestions, ledger, registry=registry)
                    if ledger is not None
                    else ()
                )
            if simulation_issues:
                raise ChatCompletionValidationError(
                    code=simulation_issues[0].code,
                    issues=simulation_issues,
                )
            if (
                request.prompt_version == "casefile-chat-v15"
                and request.route is not None
                and request.route.execution_profile.get("primary_intent") == "logic_audit"
            ):
                integrity_issues = _audit_repair_integrity(
                    bundle,
                    findings,
                    suggestions,
                )
                if integrity_issues:
                    raise ChatCompletionValidationError(
                        code=integrity_issues[0].code,
                        issues=integrity_issues,
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


def _audit_simulation_issues(
    suggestions: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
    *,
    registry: SafePatchRegistry | None = None,
) -> tuple[ValidationIssue, ...]:
    """Require every v14 audit suggestion to match a safe frozen simulation."""

    if not suggestions:
        return ()
    entries = ledger.get("entries", []) if isinstance(ledger, dict) else []
    simulations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("tool_name") != "simulate_patch_application":
            continue
        arguments = entry.get("sanitized_arguments")
        payload = entry.get("bounded_result")
        if not isinstance(arguments, dict) or not isinstance(payload, dict):
            continue
        key = (
            str(arguments.get("object_id") or ""),
            str(arguments.get("path") or ""),
            str(arguments.get("value_json") or ""),
        )
        simulations[key] = payload
    issues: list[ValidationIssue] = []
    for index, suggestion in enumerate(suggestions):
        key = (
            str(suggestion.get("object_id") or ""),
            str(suggestion.get("path") or ""),
            str(suggestion.get("value_json") or ""),
        )
        simulation = simulations.get(key)
        target = target_label(key[0], key[1])
        safe = (
            simulation is not None
            and simulation.get("valid") is True
            and simulation.get("advice") != "introduces_new_issues"
            and int(simulation.get("counts", {}).get("new", 0)) == 0
        )
        if safe:
            continue
        target_candidates = (
            registry.candidates_for_target(key[0], key[1]) if registry is not None else ()
        )
        if target_candidates:
            issues.append(
                ValidationIssue(
                    code="audit_suggestion_value_not_frozen",
                    stage="patch",
                    path=f"/suggestions/{index}",
                    message="审计建议目标已有冻结安全补丁，但值未使用冻结候选。",
                    repairable=True,
                    details={
                        "object_id": key[0],
                        "path": key[1],
                        "replace": [candidate.as_dict() for candidate in target_candidates],
                    },
                )
            )
            continue
        issues.append(
            ValidationIssue(
                code=(
                    "audit_suggestion_not_simulated"
                    if simulation is None
                    else "audit_suggestion_simulation_failed"
                ),
                stage="patch",
                path=f"/suggestions/{index}",
                message="审计建议缺少成功且不引入新问题的冻结补丁预演。",
                repairable=True,
                details={
                    "extra": [target],
                    "object_id": key[0],
                    "path": key[1],
                    "simulation": simulation or {},
                },
            )
        )
    return tuple(issues)


def _audit_server_gate_issues(
    suggestions: list[dict[str, Any]],
    registry: SafePatchRegistry | None,
) -> tuple[ValidationIssue, ...]:
    """Require v15 suggestions to match server-proven registry entries."""

    issues: list[ValidationIssue] = []
    for index, suggestion in enumerate(suggestions):
        object_id = str(suggestion.get("object_id") or "")
        path = str(suggestion.get("path") or "")
        value_json = suggestion.get("value_json")
        exact = (
            registry.exact_candidate(object_id, path, value_json)
            if registry is not None
            else None
        )
        if exact is not None:
            continue
        candidates = registry.candidates_for_target(object_id, path) if registry else ()
        issues.append(
            ValidationIssue(
                code=(
                    "audit_suggestion_value_not_frozen"
                    if candidates
                    else "audit_suggestion_server_gate_failed"
                ),
                stage="patch",
                path=f"/suggestions/{index}",
                message=(
                    "审计建议未通过服务器确定性补丁门禁。"
                    if not candidates
                    else "审计建议目标已有安全证明，但值未使用服务器冻结候选。"
                ),
                repairable=True,
                details={
                    "object_id": object_id,
                    "path": path,
                    "extra": [target_label(object_id, path)],
                    "replace": [candidate.as_dict() for candidate in candidates],
                },
            )
        )
    return tuple(issues)


def _audit_repair_integrity(
    bundle: Any,
    findings: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> tuple[ValidationIssue, ...]:
    """Reject an empty proposal set when frozen evidence exposes repairable targets."""

    if not isinstance(bundle, dict) or not findings:
        return ()
    expectation = bundle.get("repair_expectation")
    targets = expectation.get("candidate_patch_targets") if isinstance(expectation, dict) else None
    if not isinstance(targets, list) or not targets:
        return ()
    non_manual = [
        finding
        for finding in findings
        if isinstance(finding, dict) and not finding.get("needs_manual_review")
    ]
    expected_targets = {
        (str(target.get("object_id")), str(target.get("path")))
        for target in targets
        if isinstance(target, dict)
        and isinstance(target.get("object_id"), str)
        and isinstance(target.get("path"), str)
    }
    if suggestions and any(
        (
            str(suggestion.get("object_id")),
            str(suggestion.get("path")),
        )
        in expected_targets
        for suggestion in suggestions
        if isinstance(suggestion, dict)
    ):
        return ()
    issues: list[ValidationIssue] = []
    finding_ids = [
        str(item.get("finding_id"))
        for item in findings
        if isinstance(item, dict) and item.get("finding_id")
    ]
    finding_ref = next(
        (
            str(item.get("finding_id"))
            for item in non_manual
            if item.get("finding_id")
        ),
        None,
    )
    issue_code = "audit_repairable_finding_missing_suggestion"
    if suggestions:
        issue_code = "audit_repair_expectation_missing_target"
    elif finding_ref is None:
        issue_code = "audit_deterministic_pair_missing_suggestion"
    for target in targets:
        if not isinstance(target, dict):
            continue
        object_id = target.get("object_id")
        path = target.get("path")
        if not isinstance(object_id, str) or not isinstance(path, str):
            continue
        label = target_label(object_id, path)
        issues.append(
            ValidationIssue(
                code=issue_code,
                stage="audit",
                path="/suggestions",
                message="存在有证据且可编辑的审计发现，但最终结果缺少补丁提案。",
                repairable=True,
                details={
                    "missing": [label],
                    "preserve": finding_ids,
                    "extra": [
                        target_label(item.get("object_id"), item.get("path"))
                        for item in suggestions
                        if isinstance(item, dict)
                    ],
                    "finding_ref": finding_ref,
                    "object_id": object_id,
                    "path": path,
                },
            )
        )
        # One precise missing-target repair is sufficient to wake the
        # Finalizer; independent findings remain available in the frozen
        # Bundle for subsequent audit passes. A manual-only result still
        # reaches this path when the Bundle exposes a deterministic conflict
        # pair and editable target; its proposal must remain unbound.
        break
    return tuple(issues)


def _target_locked_repair_contract(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    validation: ChatCompletionValidationError,
) -> dict[str, Any] | None:
    """Return a server-owned hard-repair contract only for one exact audit delta."""

    route = request.route
    if (
        request.prompt_version != "casefile-chat-v15"
        or route is None
        or route.execution_profile.get("primary_intent") != "logic_audit"
        or not isinstance(result.candidate, CaseFileChatCandidateV2)
    ):
        return None
    issues = [
        issue for issue in validation.issues if issue.code in _TARGET_LOCKED_REPAIR_CODES
    ]
    if len(issues) != 1 or any(
        issue.code not in _TARGET_LOCKED_REPAIR_CODES for issue in validation.issues
    ):
        return None
    issue = issues[0]
    details = issue.details
    object_id = details.get("object_id")
    path = details.get("path")
    finding_ref = details.get("finding_ref")
    if not all(isinstance(value, str) and value for value in (object_id, path, finding_ref)):
        return None
    target = target_label(object_id, path)
    if validation.repair_plan.add != (target,) or validation.repair_plan.replace:
        return None
    if not any(
        finding.finding_id == finding_ref and not finding.needs_manual_review
        for finding in result.candidate.audit_findings
    ):
        return None
    bundle = request.validation.get("audit_evidence_bundle")
    expectation = bundle.get("repair_expectation") if isinstance(bundle, dict) else None
    targets = (
        expectation.get("candidate_patch_targets")
        if isinstance(expectation, dict)
        else None
    )
    locked_target = next(
        (
            item
            for item in targets or ()
            if isinstance(item, dict)
            and item.get("object_id") == object_id
            and item.get("path") == path
            and isinstance(item.get("current_value_json"), str)
            and isinstance(item.get("value_type"), str)
        ),
        None,
    )
    if locked_target is None:
        return None
    return {
        "issue_code": issue.code,
        "object_id": object_id,
        "path": path,
        "finding_ref": finding_ref,
        "preserve": list(validation.repair_plan.preserve),
        "remove": list(validation.repair_plan.remove),
        "current_value_json": locked_target["current_value_json"],
        "value_type": locked_target["value_type"],
    }


def _materialize_target_locked_repair(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Compose a full audit candidate from a locked target and minimal model output."""

    contract = request.target_locked_repair
    repair_output = cast(CaseFileChatTargetLockedRepairOutput, result.candidate)
    if not isinstance(contract, dict) or not isinstance(
        repair_output, CaseFileChatTargetLockedRepairOutput
    ):
        raise ChatCompletionValidationError(code="audit_target_locked_repair_output_invalid")
    object_id = contract.get("object_id")
    path = contract.get("path")
    finding_ref = contract.get("finding_ref")
    previous = request.previous_candidate
    if not (
        isinstance(object_id, str)
        and isinstance(path, str)
        and isinstance(finding_ref, str)
        and isinstance(previous, dict)
    ):
        raise ChatCompletionValidationError(code="audit_target_locked_repair_contract_invalid")
    try:
        candidate = CaseFileChatCandidateV2.model_validate(previous)
    except ValueError as error:
        raise ChatCompletionValidationError(
            code="audit_target_locked_repair_contract_invalid"
        ) from error
    if not any(
        finding.finding_id == finding_ref and not finding.needs_manual_review
        for finding in candidate.audit_findings
    ):
        raise ChatCompletionValidationError(code="audit_target_locked_repair_contract_invalid")
    if canonicalize_value_json(repair_output.value_json) is None:
        raise ChatCompletionValidationError(code="audit_target_locked_repair_value_invalid")
    remove = {
        value for value in contract.get("remove", ()) if isinstance(value, str)
    }
    target = target_label(object_id, path)
    remove.add(target)
    suggestions = [
        suggestion
        for suggestion in candidate.suggestions
        if target_label(suggestion.object_id, suggestion.path) not in remove
    ]
    suggestions.append(
        CaseFileChatSuggestionCandidateV2(
            object_id=object_id,
            path=path,
            value_json=repair_output.value_json,
            reason=repair_output.reason,
            finding_ref=finding_ref,
        )
    )
    return replace(result, candidate=candidate.model_copy(update={"suggestions": suggestions}))


def _normalize_finding_reference_slots(
    findings: list[dict[str, Any]],
    object_ids: set[str],
    event_ids: set[str],
) -> list[dict[str, Any]]:
    """Move known event/object IDs to the typed evidence slot they belong to."""

    normalized: list[dict[str, Any]] = []
    for finding in findings:
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


def _autofill_pair_evidence(
    findings: list[dict[str, Any]],
    *,
    object_ids: set[str],
    event_ids: set[str],
    deterministic_pairs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Add one-hop frozen pair endpoints to an already grounded finding.

    The Finalizer still identifies the finding. This only completes its
    evidence citation from the Bundle's deterministic conflict graph, so an
    output citing ``researcher + trigger claim`` also retains the directly
    connected backup-system record that establishes the opposed mechanism.
    """

    adjacency: dict[str, set[str]] = {}
    for pair in deterministic_pairs or []:
        left_id = pair.get("left_id")
        right_id = pair.get("right_id")
        if not isinstance(left_id, str) or not isinstance(right_id, str):
            continue
        if left_id not in object_ids | event_ids or right_id not in object_ids | event_ids:
            continue
        adjacency.setdefault(left_id, set()).add(right_id)
        adjacency.setdefault(right_id, set()).add(left_id)
    if not adjacency:
        return findings
    normalized: list[dict[str, Any]] = []
    for finding in findings:
        item = dict(finding)
        current = {
            str(value)
            for field in (
                "evidence_object_ids",
                "evidence_event_ids",
            )
            for value in item.get(field, [])
            if isinstance(value, str)
        }
        additions = sorted(
            {
                neighbor
                for object_id in current
                for neighbor in adjacency.get(object_id, set())
            }
            - current
        )
        objects = list(item.get("evidence_object_ids", []))
        events = list(item.get("evidence_event_ids", []))
        for object_id in additions:
            if object_id in event_ids and object_id not in events:
                events.append(object_id)
            elif object_id in object_ids and object_id not in objects:
                objects.append(object_id)
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
        deterministic_pairs = (
            request.validation.get("audit_evidence_bundle", {}).get("candidate_pairs")
            if isinstance(request.validation.get("audit_evidence_bundle"), dict)
            else None
        )
        if request.prompt_version == "casefile-chat-v15":
            normalized_findings = _autofill_pair_evidence(
                normalized_findings,
                object_ids=object_ids,
                event_ids=event_ids,
                deterministic_pairs=(
                    deterministic_pairs if isinstance(deterministic_pairs, list) else None
                ),
            )
        deduped_findings, finding_aliases = rank_and_dedupe_audit_findings(
            normalized_findings,
            deterministic_pairs=(
                deterministic_pairs if isinstance(deterministic_pairs, list) else None
            ),
            require_deterministic_pair=request.prompt_version == "casefile-chat-v15",
        )
        if deduped_findings != findings:
            payload["audit_findings"] = deduped_findings
        raw_suggestions = payload.get("suggestions")
        if isinstance(raw_suggestions, list):
            valid_finding_ids = {
                str(item.get("finding_id"))
                for item in deduped_findings
                if item.get("finding_id")
            }
            rebound_suggestions = []
            for suggestion in raw_suggestions:
                item = dict(suggestion)
                finding_ref = item.get("finding_ref")
                if isinstance(finding_ref, str) and finding_ref in finding_aliases:
                    item["finding_ref"] = finding_aliases[finding_ref]
                elif isinstance(finding_ref, str) and finding_ref not in valid_finding_ids:
                    continue
                rebound_suggestions.append(item)
            if rebound_suggestions != raw_suggestions:
                payload["suggestions"] = rebound_suggestions
        if isinstance(payload.get("suggestions"), list):
            seen_finding_refs: set[str] = set()
            minimal_suggestions = []
            for suggestion in payload["suggestions"]:
                item = dict(suggestion)
                finding_ref = item.get("finding_ref")
                if isinstance(finding_ref, str):
                    if finding_ref in seen_finding_refs:
                        continue
                    seen_finding_refs.add(finding_ref)
                minimal_suggestions.append(item)
            payload["suggestions"] = minimal_suggestions
        if request.prompt_version == "casefile-chat-v15" and isinstance(
            payload.get("suggestions"), list
        ):
            manual_ids = {
                str(item.get("finding_id"))
                for item in deduped_findings
                if item.get("needs_manual_review") and item.get("finding_id")
            }
            if manual_ids:
                unbound_suggestions = []
                for suggestion in payload["suggestions"]:
                    item = dict(suggestion)
                    if item.get("finding_ref") in manual_ids:
                        # Dedupe while the original finding_ref is still
                        # present. Once it becomes None, duplicate manual
                        # proposals would no longer share a stable key.
                        item["finding_ref"] = None
                    unbound_suggestions.append(item)
                payload["suggestions"] = unbound_suggestions
    suggestions = payload.get("suggestions", [])
    if isinstance(suggestions, list):
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            object_id = suggestion.get("object_id")
            if not isinstance(object_id, str):
                continue
            if object_id in event_ids and object_id not in events:
                events.append(object_id)
            elif object_id in object_ids and object_id not in objects:
                objects.append(object_id)
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
    if payload.get("suggestions", []) != candidate.model_dump(mode="json").get(
        "suggestions", []
    ):
        updates["suggestions"] = payload["suggestions"]
    if not updates:
        return result
    normalized_payload = candidate.model_dump(mode="json")
    normalized_payload.update(updates)
    return replace(
        result,
        candidate=candidate.__class__.model_validate(normalized_payload),
    )


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


def _apply_route_suggestion_policy(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Return the post-permission candidate shared by Worker and M2.

    Validate the model candidate before this call so an edit-target repair can
    still reason about the original proposal. A denied route must nevertheless
    never expose that proposal to persistence or the outcome grader.
    """

    route = request.route
    suggestions = result.candidate.suggestions
    if route is None or route_allows_suggestions(route) or not suggestions:
        return result
    request.emit(
        "route.suggestions_suppressed",
        "routing",
        {
            **route_public_payload(route),
            "suggestion_policy": route_suggestion_policy(route),
            "suppressed_count": len(suggestions),
            "source": "shared_execution_runner",
        },
    )
    return replace(
        result,
        candidate=result.candidate.model_copy(update={"suggestions": []}),
    )


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
        repair_history: list[dict[str, Any]] = []
        materialization_history: list[dict[str, Any]] = []
        for attempt in (1, 2, 3):
            server_gate_issues: tuple[ValidationIssue, ...] = ()
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
            if request.target_locked_repair is not None:
                try:
                    result = _materialize_target_locked_repair(request, result)
                except Exception as error:
                    _attach_failure_metrics(
                        error,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                    )
                    raise
            if (
                request.prompt_version == "casefile-chat-v15"
                and request.route is not None
                and request.route.execution_profile.get("primary_intent") == "logic_audit"
            ):
                candidate_payload = result.candidate.model_dump(mode="json")
                raw_suggestions = candidate_payload.get("suggestions")
                if isinstance(raw_suggestions, list):
                    proposals = [item for item in raw_suggestions if isinstance(item, dict)]
                    gate = server_gate_audit_suggestions(request, proposals)
                    ledger = result.tool_ledger or request.frozen_tool_ledger
                    if isinstance(ledger, dict):
                        gate = replace(
                            gate,
                            registry=replace(
                                gate.registry,
                                ledger_hash=str(ledger.get("ledger_hash") or ""),
                            ),
                        )
                    rejected_indexes = {
                        failure.suggestion_index for failure in gate.failures
                    } | {discard.suggestion_index for discard in gate.discards}
                    safe_suggestions = [
                        suggestion
                        for index, suggestion in enumerate(proposals)
                        if index not in rejected_indexes
                    ]
                    materialized, changes = materialize_unique_safe_patches(
                        safe_suggestions,
                        gate.registry,
                    )
                    if materialized != raw_suggestions:
                        candidate_payload["suggestions"] = materialized
                        result = replace(
                            result,
                            candidate=result.candidate.__class__.model_validate(
                                candidate_payload
                            ),
                        )
                    result = replace(result, safe_patch_registry=gate.registry.as_dict())
                    request.emit(
                        "model.safe_patch_gated",
                        "validating",
                        {
                            "source": "server_post_finalizer_gate",
                            "safe_count": len(gate.registry.candidates),
                            "rejected": [failure.as_dict() for failure in gate.failures],
                            "discarded": [discard.as_dict() for discard in gate.discards],
                        },
                    )
                    if changes:
                        change_payloads = [change.as_dict() for change in changes]
                        materialization_history.extend(change_payloads)
                        request.emit(
                            "model.safe_patch_materialized",
                            "validating",
                            {
                                "ledger_hash": gate.registry.ledger_hash,
                                "source": gate.registry.source,
                                "changes": change_payloads,
                            },
                        )
                    if gate.failures:
                        preserved = sorted(
                            target_label(item.get("object_id"), item.get("path"))
                            for item in materialized
                        )
                        server_gate_issues = tuple(
                            ValidationIssue(
                                code="audit_suggestion_server_gate_failed",
                                stage="patch",
                                path=f"/suggestions/{failure.suggestion_index}",
                                message="审计建议未通过服务器确定性补丁门禁。",
                                repairable=True,
                                details={
                                    "extra": [failure.target],
                                    "preserve": preserved,
                                    "object_id": failure.object_id,
                                    "path": failure.path,
                                    "reason_code": failure.reason_code,
                                    "validation": failure.validation,
                                    "simulation": failure.simulation,
                                },
                            )
                            for failure in gate.failures
                        )
            result = _normalize_reference_slots(request, result)
            result = _apply_deterministic_audit_gate(request, result)
            try:
                if server_gate_issues:
                    candidate_payload = result.candidate.model_dump(mode="json")
                    integrity_issues = _audit_repair_integrity(
                        request.validation.get("audit_evidence_bundle"),
                        candidate_payload.get("audit_findings", []),
                        candidate_payload.get("suggestions", []),
                    )
                    server_gate_issues = (*server_gate_issues, *integrity_issues)
                    raise ChatCompletionValidationError(
                        code=server_gate_issues[0].code,
                        issues=server_gate_issues,
                    )
                validate_chat_candidate(request, result)
                result = _apply_route_suggestion_policy(request, result)
                if complete is not None:
                    complete(result)
            except Exception as error:
                validation = _as_validation_error(error)
                if validation is None:
                    _attach_failure_metrics(
                        error,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                    )
                    raise
                target_locked_repair = (
                    _target_locked_repair_contract(request, result, validation)
                    if attempt == 2
                    else None
                )
                if attempt == 3 or (attempt == 2 and target_locked_repair is None):
                    _attach_failure_metrics(
                        validation,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                    )
                    raise validation from error
                repair_attempted = True
                repair_no = 2 if target_locked_repair is not None else 1
                repair_mode = (
                    "target_locked" if target_locked_repair is not None else "minimal"
                )
                repair_record = {
                    "attempt": attempt,
                    "repair_no": repair_no,
                    "repair_mode": repair_mode,
                    "validation_issues": [
                        issue.as_dict() for issue in validation.issues
                    ],
                    "repair_plan": validation.repair_plan.as_dict(),
                    "suggestion_count": len(result.candidate.suggestions),
                    "suggestion_targets": [
                        target_label(item.object_id, item.path)
                        for item in result.candidate.suggestions
                    ],
                }
                if target_locked_repair is not None:
                    repair_record["target_locked_repair"] = target_locked_repair
                repair_history.append(repair_record)
                request.emit(
                    (
                        "model.target_locked_repair_started"
                        if target_locked_repair is not None
                        else "model.reference_repair_started"
                    ),
                    "repairing",
                    {
                        "repair_no": repair_no,
                        "max_repairs": 2 if target_locked_repair is not None else 1,
                        "repair_mode": repair_mode,
                        "unknown_object_ids": list(validation.object_ids),
                        "unknown_event_ids": list(validation.event_ids),
                        "unknown_issue_ids": list(validation.issue_ids),
                        "wrong_slot_object_ids": list(validation.wrong_slot_object_ids),
                        "wrong_slot_event_ids": list(validation.wrong_slot_event_ids),
                        "validation_issues": [
                            issue.as_dict() for issue in validation.issues
                        ],
                        "repair_plan": validation.repair_plan.as_dict(),
                        "candidate_summary": {
                            "suggestion_count": repair_record["suggestion_count"],
                            "suggestion_targets": repair_record["suggestion_targets"],
                        },
                        **(
                            {"target_locked_repair": target_locked_repair}
                            if target_locked_repair is not None
                            else {}
                        ),
                    },
                )
                request = replace(
                    request,
                    repair_feedback=(validation.repair_feedback(),),
                    frozen_tool_ledger=result.tool_ledger,
                    safe_patch_registry=result.safe_patch_registry,
                    previous_candidate=result.candidate.model_dump(mode="json"),
                    repair_plan=validation.repair_plan.as_dict(),
                    target_locked_repair=target_locked_repair,
                )
                continue
            return ChatExecutionResult(
                result=result,
                usage=_merge_usage(usages),
                tools=_merge_tools(tools),
                attempts=attempt,
                repair_attempted=repair_attempted,
                diagnostics={
                    "error_code": None,
                    "attempts": attempt,
                    "repair_history": repair_history,
                    "safe_patch_materializations": materialization_history,
                },
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
    if isinstance(error, ChatAuditValidationError):
        return ChatCompletionValidationError(
            code=error.code,
            issues=(error.issue,),
        )
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
