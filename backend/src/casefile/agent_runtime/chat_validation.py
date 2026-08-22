"""Candidate validation and stable repair API for CaseFile Chat.

Owns candidate validation and the public validation/repair contract. Does not
own provider calls, persistence, prompt rendering, or retry orchestration.
"""

from __future__ import annotations

from typing import Any

from casefile.agent_runtime.chat_audit_validation import (
    audit_repair_integrity,
    audit_server_gate_issues,
    audit_simulation_issues,
    normalize_audit_findings,
)
from casefile.agent_runtime.chat_safe_patches import safe_patch_registry_from_dict
from casefile.agent_runtime.chat_tools import check_patch_proposal
from casefile.agent_runtime.chat_validation_contracts import (
    ChatCompletionValidationError,
    RepairPlan,
    ValidationIssue,
    ValidationReport,
    plan_repairs,
    resolve_authoritative_repair_target,
    select_semantic_repair_mode,
    target_label,
)
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult


def chat_record_ids(casefile: dict[str, Any]) -> tuple[set[str], set[str]]:
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
    object_ids, event_ids = chat_record_ids(request.casefile)
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
                simulation_issues = audit_server_gate_issues(suggestions, registry)
            else:
                ledger = result.tool_ledger or request.frozen_tool_ledger
                registry = None
                if isinstance(result.safe_patch_registry, dict):
                    registry = safe_patch_registry_from_dict(result.safe_patch_registry)
                elif isinstance(request.safe_patch_registry, dict):
                    registry = safe_patch_registry_from_dict(request.safe_patch_registry)
                simulation_issues = (
                    audit_simulation_issues(suggestions, ledger, registry=registry)
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
                integrity_issues = audit_repair_integrity(
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


__all__ = [
    "ChatCompletionValidationError",
    "RepairPlan",
    "ValidationIssue",
    "ValidationReport",
    "chat_record_ids",
    "plan_repairs",
    "resolve_authoritative_repair_target",
    "select_semantic_repair_mode",
    "target_label",
    "validate_chat_candidate",
]
