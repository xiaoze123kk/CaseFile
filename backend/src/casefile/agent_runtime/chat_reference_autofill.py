"""Conservative, deterministic reference-slot autofill for CaseFile chat.

The Grader intentionally scores ``referenced_*_ids`` instead of reading the
answer text, but some models answer correctly and leave the slots empty.  This
module closes only that exact gap: when a slot is missing the candidate is
never deleted, never re-ranked, and an ID is added only when the answer
contains a record label that maps to exactly one CaseFile record ID.

The data source is intentionally narrow: record ``id`` plus ``name``/``title``
from the frozen CaseFile (the same ``casefile.records`` surface the model sees)
plus the full records referenced by the current focus.  Tool results, source
IDs and any other identifier space are never used.
"""

from __future__ import annotations

import os
from typing import Any

from casefile.agent_runtime.chat_tools import CASEFILE_COLLECTIONS

REFERENCE_AUTOFILL_ENV = "CASEFILE_CHAT_REFERENCE_AUTOFILL"
_CHAT_CONTEXT_ROLLOUT_ENV = "CASEFILE_CHAT_CONTEXT_ROLLOUT"
_V3_ROLLOUT = "casefile-chat-context-v3"

_OBJECT_COLLECTIONS = tuple(
    collection for collection in CASEFILE_COLLECTIONS if collection != "events"
)
_LABEL_FIELDS = ("name", "title")
_MIN_LABEL_CHARS = 2


def reference_autofill_enabled() -> bool:
    """Resolve the reference autofill switch.

    The explicit ``CASEFILE_CHAT_REFERENCE_AUTOFILL`` switch always wins
    (``1``/``true``/``on``/``yes`` enables, anything else disables).  When
    it is unset, the v3 context rollout enables the safety net by default so
    production behavior matches the accepted live acceptance arm; all other
    rollouts keep it off (fail closed).
    """

    value = os.environ.get(REFERENCE_AUTOFILL_ENV, "").strip()
    if value:
        return value.lower() in {"1", "true", "on", "yes"}
    return os.environ.get(_CHAT_CONTEXT_ROLLOUT_ENV, "") == _V3_ROLLOUT


def _normalise(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _record_labels(item: dict[str, Any]) -> tuple[str, ...]:
    labels: list[str] = []
    for field in _LABEL_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            label = _normalise(value)
            if label not in labels:
                labels.append(label)
    return tuple(labels)


def _index_collection(
    casefile: dict[str, Any],
    collection: str,
) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    values = casefile.get(collection)
    if not isinstance(values, list):
        return index
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        object_id = str(item["id"]).strip()
        if not object_id:
            continue
        for label in _record_labels(item):
            index.setdefault(label, [])
            if object_id not in index[label]:
                index[label].append(object_id)
    return index


def build_reference_autofill_index(
    casefile: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build label -> CaseFile ID indexes for object and event reference slots."""

    object_index: dict[str, list[str]] = {}
    event_index: dict[str, list[str]] = {}
    for collection in _OBJECT_COLLECTIONS:
        for label, ids in _index_collection(casefile, collection).items():
            object_index.setdefault(label, [])
            for object_id in ids:
                if object_id not in object_index[label]:
                    object_index[label].append(object_id)
    event_index = _index_collection(casefile, "events")
    return object_index, event_index


def autofill_reference_ids(
    answer: str,
    index: dict[str, list[str]],
) -> list[str]:
    """Return IDs whose labels appear uniquely in ``answer``.

    A label is eligible only when it is at least two characters long and maps
    to exactly one ID.  When one matched label is a substring of another
    matched label, only the shorter label is discarded: the longer label is
    the more specific record name and is not a guess.
    """

    answer_text = _normalise(answer)
    if not answer_text:
        return []
    unique_labels = sorted(
        {
            label
            for label, ids in index.items()
            if len(label) >= _MIN_LABEL_CHARS and len(ids) == 1
        },
        key=len,
        reverse=True,
    )
    matched = [label for label in unique_labels if label in answer_text]
    ambiguous = {
        label
        for label in matched
        if any(
            label != other and label in other and other in answer_text
            for other in unique_labels
        )
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for label in sorted(
        (label for label in matched if label not in ambiguous),
        key=lambda value: answer_text.find(value),
    ):
        object_id = index[label][0]
        if object_id not in seen:
            seen.add(object_id)
            resolved.append(object_id)
    return resolved


def autofill_chat_references(
    answer: str,
    casefile: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Autofill both reference slots from frozen CaseFile records only."""

    object_index, event_index = build_reference_autofill_index(casefile)
    return (
        autofill_reference_ids(answer, object_index),
        autofill_reference_ids(answer, event_index),
    )


__all__ = [
    "REFERENCE_AUTOFILL_ENV",
    "autofill_chat_references",
    "autofill_reference_ids",
    "build_reference_autofill_index",
    "reference_autofill_enabled",
]
