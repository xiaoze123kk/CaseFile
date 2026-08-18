"""Server-side validation for the casefile-chat-output-v2 audit_findings slot.

Pure helpers so the acceptance rules stay unit-testable without PostgreSQL:
the WorkflowService calls them inside the ``complete_chat_task`` transaction.
"""

from __future__ import annotations

from typing import Any

AUDIT_FINDING_KINDS = frozenset(
    {"dangling_ref", "contradiction", "temporal", "motivation_gap", "scope_gap"}
)
AUDIT_FINDING_SEVERITIES = frozenset({"S1", "S2", "S3"})
MAX_AUDIT_FINDINGS = 50

_STRING_FIELDS = ("finding_id", "kind", "severity", "title", "statement")
_EVIDENCE_FIELDS = (
    "evidence_object_ids",
    "evidence_event_ids",
    "evidence_validation_issue_ids",
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
    frozen_object_ids: set[str],
    frozen_event_ids: set[str],
    known_issue_ids: set[str],
    suggestion_finding_refs: list[str | None],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Normalize and evidence-check findings; return missing evidence IDs.

    Structural violations raise ``ValueError`` with a stable reason code.
    Missing evidence references are returned so the caller can feed the
    existing ChatReferenceValidationError repair loop.
    """
    if len(audit_findings) > MAX_AUDIT_FINDINGS:
        raise ValueError(
            f"audit_findings_exceeds_limit:{len(audit_findings)}>{MAX_AUDIT_FINDINGS}"
        )

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    missing_objects: set[str] = set()
    missing_events: set[str] = set()
    missing_issues: set[str] = set()
    for raw in audit_findings:
        if not isinstance(raw, dict):
            raise ValueError("audit_finding_item_must_be_object")
        finding: dict[str, Any] = {}
        for field_name in _STRING_FIELDS:
            value = raw.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"audit_finding_missing:{field_name}")
            finding[field_name] = value.strip()
        finding_id = finding["finding_id"]
        if finding_id in seen_ids:
            raise ValueError(f"audit_finding_id_duplicate:{finding_id}")
        seen_ids.add(finding_id)
        if finding["kind"] not in AUDIT_FINDING_KINDS:
            raise ValueError(f"audit_finding_kind_invalid:{finding['kind']}")
        if finding["severity"] not in AUDIT_FINDING_SEVERITIES:
            raise ValueError(
                f"audit_finding_severity_invalid:{finding['severity']}"
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
                raise ValueError(f"audit_finding_evidence_invalid:{field_name}")
            deduped: list[str] = []
            for item in value:
                item = item.strip()
                if item and item not in deduped:
                    deduped.append(item)
            finding[field_name] = deduped
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
        raise ValueError(f"audit_finding_ref_unknown:{','.join(unknown_refs)}")
    manual_refs = sorted(referenced_finding_ids & manual_review_ids)
    if manual_refs:
        raise ValueError(f"audit_finding_ref_manual_review:{','.join(manual_refs)}")

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
