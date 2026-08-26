"""Normalize CaseFile Chat reference slots before validation.

Owns deterministic slot normalization and conservative autofill. Does not own
provider calls, repair policy, persistence, or patch validation.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from casefile.agent_runtime.chat_audit_validation import rank_and_dedupe_audit_findings
from casefile.agent_runtime.chat_reference_autofill import autofill_chat_references
from casefile.agent_runtime.chat_validation import chat_record_ids
from casefile.agent_runtime.chat_versions import SAFE_PATCH_PROMPT_VERSIONS
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult


def normalize_finding_reference_slots(
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


def autofill_pair_evidence(
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


def normalize_reference_slots(
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
    object_ids, event_ids = chat_record_ids(request.casefile)
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
        normalized_findings = normalize_finding_reference_slots(
            findings, object_ids, event_ids
        )
        deterministic_pairs = (
            request.validation.get("audit_evidence_bundle", {}).get("candidate_pairs")
            if isinstance(request.validation.get("audit_evidence_bundle"), dict)
            else None
        )
        if request.prompt_version in SAFE_PATCH_PROMPT_VERSIONS:
            normalized_findings = autofill_pair_evidence(
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
            require_deterministic_pair=request.prompt_version in SAFE_PATCH_PROMPT_VERSIONS,
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
        if request.prompt_version in SAFE_PATCH_PROMPT_VERSIONS and isinstance(
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


__all__ = ["normalize_reference_slots"]
