"""Server-side validation for the casefile-chat-output-v2 audit_findings slot.

Pure helpers so the acceptance rules stay unit-testable without PostgreSQL:
the WorkflowService calls them inside the ``complete_chat_task`` transaction.
"""

from __future__ import annotations

from collections.abc import Set
from typing import Any, NoReturn

from casefile.agent_runtime.chat_validation import ValidationIssue

AUDIT_FINDING_KINDS = frozenset(
    {"dangling_ref", "contradiction", "temporal", "motivation_gap", "scope_gap"}
)
AUDIT_FINDING_SEVERITIES = frozenset({"S1", "S2", "S3"})
MAX_AUDIT_FINDINGS = 5

# These finding kinds describe a relation, not a one-sided observation.  The
# executor must therefore provide both endpoints (or two temporal anchors)
# before a finding can reach persistence.
_TWO_SIDED_KINDS = frozenset({"contradiction", "temporal", "motivation_gap", "dangling_ref"})

_STRING_FIELDS = ("finding_id", "kind", "severity", "title", "statement")
_EVIDENCE_FIELDS = (
    "evidence_object_ids",
    "evidence_event_ids",
    "evidence_validation_issue_ids",
)


class ChatAuditValidationError(ValueError):
    """Stable structural audit error that may enter bounded repair."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        self.code = issue.code
        super().__init__(issue.code)


def _raise_issue(
    code: str,
    *,
    path: str = "/audit_findings",
    message: str,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    raise ChatAuditValidationError(
        ValidationIssue(
            code=code,
            stage="audit",
            path=path,
            message=message,
            repairable=True,
            details=details or {},
        )
    )


def route_primary_intent(route: dict[str, Any] | None) -> str | None:
    """Read the executor route's primary intent from a route payload."""
    if route is None:
        return None
    profile = route.get("execution_profile")
    if not isinstance(profile, dict):
        return None
    value = profile.get("primary_intent")
    return None if not isinstance(value, str) else value


def audit_findings_suppressed_for(route: dict[str, Any] | None) -> bool:
    """Findings may only be persisted on the logic_audit executor route."""
    intent = route_primary_intent(route)
    return intent is not None and intent != "logic_audit"


def normalize_audit_findings(
    audit_findings: list[dict[str, Any]],
    *,
    frozen_object_ids: Set[str],
    frozen_event_ids: Set[str],
    known_issue_ids: Set[str],
    suggestion_finding_refs: list[str | None],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Normalize and evidence-check findings; return missing evidence IDs.

    Structural violations raise ``ChatAuditValidationError`` with a stable issue.
    Missing evidence references are returned so the caller can feed the
    existing ChatReferenceValidationError repair loop.
    """
    if len(audit_findings) > MAX_AUDIT_FINDINGS:
        _raise_issue(
            "audit_findings_exceeds_limit",
            message="审计发现超过最多 5 条的限制。",
            details={"actual": len(audit_findings), "limit": MAX_AUDIT_FINDINGS},
        )

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    missing_objects: set[str] = set()
    missing_events: set[str] = set()
    missing_issues: set[str] = set()
    for raw in audit_findings:
        if not isinstance(raw, dict):
            _raise_issue(
                "audit_finding_item_must_be_object",
                message="审计发现必须是对象。",
            )
        finding: dict[str, Any] = {}
        for field_name in _STRING_FIELDS:
            value = raw.get(field_name)
            if not isinstance(value, str) or not value.strip():
                _raise_issue(
                    "audit_finding_required_field_missing",
                    path=f"/audit_findings/{len(normalized)}/{field_name}",
                    message=f"审计发现缺少必填字段 {field_name}。",
                    details={"field": field_name, "finding_index": len(normalized)},
                )
            finding[field_name] = value.strip()
        finding_id = finding["finding_id"]
        if finding_id in seen_ids:
            _raise_issue(
                "audit_finding_id_duplicate",
                path=f"/audit_findings/{len(normalized)}/finding_id",
                message=f"审计发现 ID {finding_id} 重复。",
                details={"finding_id": finding_id},
            )
        seen_ids.add(finding_id)
        if finding["kind"] not in AUDIT_FINDING_KINDS:
            _raise_issue(
                "audit_finding_kind_invalid",
                path=f"/audit_findings/{len(normalized)}/kind",
                message="审计发现类型不合法。",
                details={"finding_id": finding_id, "kind": finding["kind"]},
            )
        if finding["severity"] not in AUDIT_FINDING_SEVERITIES:
            _raise_issue(
                "audit_finding_severity_invalid",
                path=f"/audit_findings/{len(normalized)}/severity",
                message="审计发现严重度不合法。",
                details={"finding_id": finding_id, "severity": finding["severity"]},
            )
        manual_review = bool(raw.get("needs_manual_review"))
        finding["needs_manual_review"] = manual_review
        for field_name in _EVIDENCE_FIELDS:
            value = raw.get(field_name)
            if value is None:
                value = []
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                _raise_issue(
                    "audit_finding_evidence_invalid",
                    path=f"/audit_findings/{len(normalized)}/{field_name}",
                    message=f"审计证据槽 {field_name} 不合法。",
                    details={"finding_id": finding_id, "field": field_name},
                )
            deduped: list[str] = []
            for item in value:
                item = item.strip()
                if item and item not in deduped:
                    deduped.append(item)
            finding[field_name] = deduped
        evidence_count = sum(len(finding[field_name]) for field_name in _EVIDENCE_FIELDS)
        required_evidence = 1 if manual_review else 2
        if finding["kind"] in _TWO_SIDED_KINDS and evidence_count < required_evidence:
            _raise_issue(
                "audit_finding_evidence_incomplete",
                path=f"/audit_findings/{len(normalized)}",
                message="关系类审计发现缺少足够的真实证据端点。",
                details={
                    "finding_id": finding_id,
                    "kind": finding["kind"],
                    "needs_manual_review": manual_review,
                    "required": required_evidence,
                    "actual": evidence_count,
                },
            )
        for object_id in finding["evidence_object_ids"]:
            if object_id not in frozen_object_ids:
                missing_objects.add(object_id)
        for event_id in finding["evidence_event_ids"]:
            if event_id not in frozen_event_ids:
                missing_events.add(event_id)
        for issue_id in finding["evidence_validation_issue_ids"]:
            if issue_id not in known_issue_ids:
                missing_issues.add(issue_id)
        normalized.append(finding)

    manual_review_ids = {
        item["finding_id"] for item in normalized if item["needs_manual_review"]
    }
    referenced_finding_ids = {
        ref for ref in suggestion_finding_refs if ref is not None
    }
    unknown_refs = sorted(referenced_finding_ids - seen_ids)
    if unknown_refs:
        _raise_issue(
            "audit_finding_ref_unknown",
            path="/suggestions",
            message="建议引用了不存在的审计发现。",
            details={"unknown_finding_refs": unknown_refs},
        )
    manual_refs = sorted(referenced_finding_ids & manual_review_ids)
    if manual_refs:
        _raise_issue(
            "audit_finding_ref_manual_review",
            path="/suggestions",
            message="待人工确认的发现不能绑定修改建议。",
            details={"manual_review_finding_refs": manual_refs},
        )

    return (
        normalized,
        sorted(missing_objects),
        sorted(missing_events),
        sorted(missing_issues),
    )


def audit_finding_ids(finding: dict[str, Any]) -> set[str]:
    """Collect every evidence ID slot of one normalized finding."""
    return {
        str(item)
        for field_name in _EVIDENCE_FIELDS
        for item in finding.get(field_name, [])
    }


def audit_finding_kind_labels() -> dict[str, str]:
    return {
        "dangling_ref": "断链",
        "contradiction": "矛盾",
        "temporal": "时序错误",
        "motivation_gap": "动机缺口",
        "scope_gap": "范围缺口",
    }


def audit_finding_severity_labels() -> dict[str, str]:
    return {"S1": "致命", "S2": "主要", "S3": "次要"}
