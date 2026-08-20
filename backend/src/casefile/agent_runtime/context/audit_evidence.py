"""Deterministic, compact evidence bundle for the v13 audit executor."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from casefile.application.verification_engine import VerificationEngine

AUDIT_COLLECTIONS = (
    "entities",
    "relationships",
    "events",
    "information_units",
    "claims",
    "hypotheses",
    "resolution_specs",
)
AUDIT_TEXT_FIELDS = (
    "name",
    "title",
    "description",
    "statement",
    "content",
    "summary",
    "motivation",
)


@dataclass(frozen=True, slots=True)
class AuditEvidenceBundle:
    payload: dict[str, Any]
    included_ids: frozenset[str]
    truncated: bool


def build_audit_evidence_bundle(
    casefile: dict[str, Any],
    *,
    draft_revision: int = 1,
    max_chars: int = 24_000,
    candidate_pairs: list[dict[str, Any]] | None = None,
    tool_counterevidence: list[dict[str, Any]] | None = None,
    editable_fields_by_collection: dict[str, tuple[str, ...]] | None = None,
) -> AuditEvidenceBundle:
    """Build stable records plus deterministic findings under a hard size cap."""

    if max_chars < 1_000:
        raise ValueError("audit evidence max_chars must be at least 1000")
    canonical = json.dumps(casefile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    all_ids: list[str] = []
    for collection in AUDIT_COLLECTIONS:
        values = casefile.get(collection, [])
        items = values if isinstance(values, list) else []
        counts[collection] = len(items)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            object_id = str(item["id"])
            all_ids.append(object_id)
            record: dict[str, Any] = {
                "id": object_id,
                "collection": collection,
                "evidence_slot": "event" if collection == "events" else "object",
            }
            for field in AUDIT_TEXT_FIELDS:
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    record[field] = value.strip()[:800]
            records.append(record)
    verification = VerificationEngine(
        profile="fast",
        draft_revision=draft_revision,
    ).verify(casefile)
    if candidate_pairs is None:
        candidate_pairs = []
        relevant_records = [
            record
            for record in records
            if record["collection"] in {"entities", "events", "claims", "relationships"}
        ]
        for left_index, left in enumerate(relevant_records):
            left_text = _record_text(left)
            for right in relevant_records[left_index + 1 :]:
                if left["id"] == right["id"]:
                    continue
                shared_tokens = left_text & _record_text(right)
                conflict_terms = _conflict_terms(_record_excerpt(left), _record_excerpt(right))
                if shared_tokens and conflict_terms:
                    candidate_pairs.append(
                        {
                            "kind": "cross_record_conflict_candidate",
                            "source_rule": "shared_term_with_opposed_terms",
                            "left_id": str(left["id"]),
                            "right_id": str(right["id"]),
                            "left_collection": str(left["collection"]),
                            "right_collection": str(right["collection"]),
                            "shared_terms": sorted(shared_tokens)[:8],
                            "conflict_terms": conflict_terms,
                            "left_excerpt": _record_excerpt(left),
                            "right_excerpt": _record_excerpt(right),
                        }
                    )
        candidate_pairs.extend(
            {
                "kind": "same_record_field_conflict_candidate",
                "source_rule": "event_title_description_opposed_terms",
                "left_id": str(record["id"]),
                "right_id": str(record["id"]),
                "collection": "events",
                "left_field": "title",
                "right_field": "description",
                "left_excerpt": str(record["title"])[:400],
                "right_excerpt": str(record["description"])[:400],
                "conflict_terms": _conflict_terms(
                    str(record["title"]), str(record["description"])
                ),
            }
            for record in records
            if record["collection"] == "events"
            and isinstance(record.get("title"), str)
            and isinstance(record.get("description"), str)
            and _conflict_terms(str(record["title"]), str(record["description"]))
        )
    base = {
        "schema_version": "audit-evidence-v1",
        "casefile_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "draft_revision": draft_revision,
        "collection_counts": counts,
        "deterministic_findings": [finding.as_dict() for finding in verification.findings],
        # Candidate pairs are populated by read-only audit tools when present;
        # an empty list is meaningful and drives the clean-no-op gate.
        "candidate_pairs": list(candidate_pairs or []),
        "tool_counterevidence": list(tool_counterevidence or []),
        # This is finalized below after the hard-cap crop is known.  A cropped
        # Bundle can never claim that the full CaseFile was clean.
        "clean_noop_eligible": False,
        "suggestion_allowed_fields": {
            str(record["id"]): [
                str(path)
                for path in (editable_fields_by_collection or {}).get(
                    str(record["collection"]), ()
                )
            ]
            for record in records
        },
    }
    selected: list[dict[str, Any]] = []
    for record in records:
        candidate = {**base, "records": [*selected, record], "truncated": False}
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > max_chars:
            break
        selected.append(record)
    included = frozenset(str(record["id"]) for record in selected)
    truncated = len(selected) != len(records)
    payload = {
        **base,
        "records": selected,
        "included_ids": sorted(included),
        "truncated": truncated,
        "uncovered_record_count": len(records) - len(selected),
        "uncovered_ids": [object_id for object_id in all_ids if object_id not in included],
    }
    payload["clean_noop_eligible"] = bool(
        not truncated
        and not payload["deterministic_findings"]
        and not payload["candidate_pairs"]
        and not payload["tool_counterevidence"]
    )
    return AuditEvidenceBundle(payload=payload, included_ids=included, truncated=truncated)


def _record_text(record: dict[str, Any]) -> set[str]:
    words: set[str] = set()
    for key, value in record.items():
        if key in {"id", "collection", "evidence_slot"} or not isinstance(value, str):
            continue
        normalized = value.casefold().replace("，", "").replace("。", "")
        chunks = normalized.split()
        for token in chunks:
            if len(token) >= 2:
                words.add(token)
        for index in range(len(normalized) - 1):
            words.add(normalized[index : index + 2])
    return words


def _record_excerpt(record: dict[str, Any]) -> str:
    """Return a compact deterministic excerpt for pair-level audit evidence."""

    parts = [
        str(record[field]).strip()
        for field in AUDIT_TEXT_FIELDS
        if isinstance(record.get(field), str) and str(record[field]).strip()
    ]
    return "；".join(parts)[:600]


_OPPOSED_TERMS = (
    ("稳固", "破裂"),
    ("互信", "叛逃"),
    ("人工", "自动"),
    ("误操作", "自动触发"),
    ("南侧", "北侧"),
    ("南方", "北方"),
    ("向南", "向北"),
    ("存在", "不存在"),
    ("开启", "关闭"),
    ("成功", "失败"),
)


def _conflict_terms(left: str, right: str) -> list[str]:
    found: list[str] = []
    for first, second in _OPPOSED_TERMS:
        if (first in left and second in right) or (second in left and first in right):
            found.extend((first, second))
    return found


__all__ = ["AuditEvidenceBundle", "build_audit_evidence_bundle"]
