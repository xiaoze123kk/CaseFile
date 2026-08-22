"""Server-side validation for the casefile-chat-output-v2 audit_findings slot.

Pure helpers so the acceptance rules stay unit-testable without PostgreSQL:
the WorkflowService calls them inside the ``complete_chat_task`` transaction.
"""

from __future__ import annotations

import json
from collections.abc import Set
from itertools import combinations
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
    deterministic_pairs: list[dict[str, Any]] | None = None,
    require_deterministic_pair: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Normalize and evidence-check findings; return missing evidence IDs.

    Structural violations raise ``ChatAuditValidationError`` with a stable issue.
    Missing evidence references are returned so the caller can feed the
    existing ChatReferenceValidationError repair loop.
    """
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    missing_objects: set[str] = set()
    missing_events: set[str] = set()
    missing_issues: set[str] = set()
    same_record_pair_ids = {
        str(pair["left_id"])
        for pair in deterministic_pairs or []
        if isinstance(pair, dict)
        and pair.get("left_id") == pair.get("right_id")
        and isinstance(pair.get("left_id"), str)
    }
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
        same_record_pair_covered = (
            evidence_count == 1
            and not manual_review
            and any(
                object_id in same_record_pair_ids
                for field_name in _EVIDENCE_FIELDS
                for object_id in finding[field_name]
            )
        )
        if (
            finding["kind"] in _TWO_SIDED_KINDS
            and evidence_count < required_evidence
            and not same_record_pair_covered
        ):
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

    normalized, aliases = rank_and_dedupe_audit_findings(
        normalized,
        deterministic_pairs=deterministic_pairs,
        require_deterministic_pair=require_deterministic_pair,
    )
    if len(normalized) > MAX_AUDIT_FINDINGS:
        _raise_issue(
            "audit_findings_exceeds_limit",
            message="审计发现超过最多 5 条的限制。",
            details={"actual": len(normalized), "limit": MAX_AUDIT_FINDINGS},
        )

    seen_ids = {item["finding_id"] for item in normalized}
    manual_review_ids = {
        item["finding_id"] for item in normalized if item["needs_manual_review"]
    }
    referenced_finding_ids = {
        aliases.get(ref, ref) for ref in suggestion_finding_refs if ref is not None
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


def rank_and_dedupe_audit_findings(
    findings: list[dict[str, Any]],
    *,
    deterministic_pairs: list[dict[str, Any]] | None = None,
    require_deterministic_pair: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Collapse overlapping findings while retaining the strongest evidence."""

    severity_rank = {"S1": 0, "S2": 1, "S3": 2}

    def evidence_ids(finding: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            sorted(
                str(value)
                for field in _EVIDENCE_FIELDS
                for value in finding.get(field, [])
            )
        )

    deterministic_endpoint_sets = {
        tuple(sorted((str(pair["left_id"]), str(pair["right_id"]))))
        for pair in deterministic_pairs or []
        if isinstance(pair, dict)
        and isinstance(pair.get("left_id"), str)
        and isinstance(pair.get("right_id"), str)
    }

    def has_deterministic_match(finding: dict[str, Any]) -> bool:
        evidence = set(evidence_ids(finding))
        return any(set(pair).issubset(evidence) for pair in deterministic_endpoint_sets)

    def deterministic_pair_key(finding: dict[str, Any]) -> tuple[str, ...]:
        evidence = set(evidence_ids(finding))
        matches = [
            pair for pair in deterministic_endpoint_sets if set(pair).issubset(evidence)
        ]
        return min(matches) if matches else ()

    ranked = sorted(
        enumerate(findings),
        key=lambda item: (
            severity_rank.get(str(item[1].get("severity")), 99),
            -sum(len(item[1].get(field, [])) for field in _EVIDENCE_FIELDS),
            -int(has_deterministic_match(item[1])),
            1 if item[1].get("needs_manual_review") else 0,
            item[0],
        ),
    )
    selected: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    exact_signatures: set[str] = set()
    kind_evidence_signatures: set[tuple[str, tuple[str, ...]]] = set()
    endpoint_signatures: set[tuple[str, str]] = set()
    deterministic_pair_signatures: set[tuple[str, ...]] = set()
    for _index, finding in ranked:
        evidence = evidence_ids(finding)
        signature = (str(finding.get("kind") or ""), evidence)
        exact = json.dumps(
            {
                key: value
                for key, value in finding.items()
                if key not in {"finding_id", "title", "statement"}
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        endpoint_pairs = set(combinations(evidence, 2))
        deterministic_pair = deterministic_pair_key(finding)
        if require_deterministic_pair and deterministic_endpoint_sets and not deterministic_pair:
            continue
        if (
            exact in exact_signatures
            or signature in kind_evidence_signatures
            or bool(endpoint_pairs & endpoint_signatures)
            or (
                deterministic_pair
                and deterministic_pair in deterministic_pair_signatures
            )
        ):
            representative = next(
                (
                    item["finding_id"]
                    for item in selected
                    if (str(item.get("kind") or ""), tuple(
                        sorted(
                            str(value)
                            for field in _EVIDENCE_FIELDS
                            for value in item.get(field, [])
                        )
                    )) == signature
                    or bool(
                        endpoint_pairs
                        & {
                            pair
                            for pair in combinations(
                                tuple(
                                    sorted(
                                        str(value)
                                        for field in _EVIDENCE_FIELDS
                                        for value in item.get(field, [])
                                    )
                                ),
                                2,
                            )
                        }
                    )
                    or (
                        deterministic_pair
                        and deterministic_pair_key(item) == deterministic_pair
                    )
                ),
                None,
            )
            if isinstance(representative, str):
                aliases[str(finding["finding_id"])] = representative
            continue
        exact_signatures.add(exact)
        kind_evidence_signatures.add(signature)
        endpoint_signatures.update(endpoint_pairs)
        if deterministic_pair:
            deterministic_pair_signatures.add(deterministic_pair)
        selected.append(finding)
    return selected, aliases


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
