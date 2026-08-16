"""Pure helpers for frozen Agent suggestions and post-apply review warnings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CASEFILE_OBJECT_COLLECTIONS = (
    "resolution_specs",
    "entities",
    "relationships",
    "locations",
    "events",
    "information_units",
    "claims",
    "hypotheses",
    "reasoning_paths",
    "constraints",
    "structure_locks",
)


def auto_thread_title(content: str) -> str:
    normalized = " ".join(content.split())
    return normalized if len(normalized) <= 48 else f"{normalized[:47]}…"


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def frozen_object_ids(casefile: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for collection in CASEFILE_OBJECT_COLLECTIONS:
        values = casefile.get(collection, [])
        if not isinstance(values, list):
            continue
        result.update(
            str(value["id"])
            for value in values
            if isinstance(value, dict) and isinstance(value.get("id"), str)
        )
    return result


def freeze_agent_focus(
    casefile: dict[str, Any],
    focus: dict[str, Any] | None,
    known_validation_issue_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Freeze the workbench selection sent with a message, pruning dangling refs.

    The trimmed references are kept in the returned ``pruned`` section so the
    frozen TaskRun input records both what survived and what was dropped.
    """

    if not isinstance(focus, dict):
        focus = {}
    object_ids = unique_strings([str(value) for value in focus.get("object_ids", []) or []])[:50]
    event_ids = unique_strings([str(value) for value in focus.get("event_ids", []) or []])[:50]
    validation_issue_ids = unique_strings(
        [str(value) for value in focus.get("validation_issue_ids", []) or []]
    )[:50]
    view = str(focus.get("view") or "").strip()[:64] or None

    known_object_ids = frozen_object_ids(casefile)
    known_event_ids = {
        str(value["id"])
        for value in casefile.get("events", [])
        if isinstance(value, dict) and isinstance(value.get("id"), str)
    }
    kept_object_ids = [value for value in object_ids if value in known_object_ids]
    kept_event_ids = [value for value in event_ids if value in known_event_ids]
    kept_issue_ids = (
        validation_issue_ids
        if known_validation_issue_ids is None
        else [value for value in validation_issue_ids if value in known_validation_issue_ids]
    )

    return {
        "object_ids": kept_object_ids,
        "event_ids": kept_event_ids,
        "validation_issue_ids": kept_issue_ids,
        "view": view,
        "pruned": {
            "object_ids": [value for value in object_ids if value not in kept_object_ids],
            "event_ids": [value for value in event_ids if value not in kept_event_ids],
            "validation_issue_ids": [
                value for value in validation_issue_ids if value not in kept_issue_ids
            ],
        },
    }


def find_frozen_object(casefile: dict[str, Any], object_id: str) -> dict[str, Any]:
    for collection in CASEFILE_OBJECT_COLLECTIONS:
        values = casefile.get(collection, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value.get("id") == object_id:
                return value
    raise RuntimeError(f"Frozen CaseFile object is missing: {object_id}")


def focused_patch_target_ids(focus: dict[str, Any] | None) -> set[str] | None:
    """Return the allowed suggestion targets when a validation issue is focused.

    ``None`` means the message had no issue focus, so no extra restriction
    applies. A non-``None`` empty set means the focus contained an issue but no
    bound workbench object survived pruning; suggestions are then forbidden.
    """

    if not isinstance(focus, dict):
        return None
    issue_ids = focus.get("validation_issue_ids", []) or []
    if not issue_ids:
        return None
    result: set[str] = set()
    for key in ("object_ids", "event_ids"):
        values = focus.get(key, []) or []
        for value in values:
            if isinstance(value, str) and value:
                result.add(value)
    return result


def pointer_top_field(path: str) -> str:
    return _pointer_parts(path)[0]


def frozen_pointer_value(value: dict[str, Any], path: str) -> Any:
    if path == "/description" and "description" not in value:
        return None
    current: Any = value
    for part in _pointer_parts(path):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Suggestion path is missing: {path}") from error
    return deepcopy(current)


def nonblocking_validator_issues(
    document: dict[str, Any],
    applied_operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return semantic warnings that do not invalidate the persisted Draft."""

    issues: list[dict[str, Any]] = []
    for claim in document["claims"]:
        if (
            claim["status"] == "supported"
            and claim["materiality"] == "critical"
            and not claim["support_refs"]
        ):
            issues.append(
                {
                    "rule_id": "CF-W-CLAIM-001",
                    "severity": "S1",
                    "title": "关键主张缺少支撑信息",
                    "message": "该关键主张被标记为已支持，但尚未关联任何支撑信息。",
                    "object_refs": [
                        {"object_type": "claim", "object_id": claim["id"]}
                    ],
                    "field_path": "/support_refs",
                }
            )

    locks_by_target: dict[str, list[dict[str, Any]]] = {}
    for lock in document["structure_locks"]:
        target = lock["object_ref"]["object_id"]
        locks_by_target.setdefault(target, []).append(lock)
    seen_locks: set[tuple[str, str, str]] = set()
    for operation in applied_operations:
        object_id = str(operation["object_id"])
        field_path = str(operation["field_path"])
        for lock in locks_by_target.get(object_id, []):
            for locked_path in lock["field_paths"]:
                if not _paths_overlap(field_path, locked_path):
                    continue
                key = (lock["id"], object_id, locked_path)
                if key in seen_locks:
                    continue
                seen_locks.add(key)
                issues.append(
                    {
                        "rule_id": "CF-W-LOCK-001",
                        "severity": "S1",
                        "title": "修改触及结构锁",
                        "message": "本批修改触及已锁定的对象字段，请确认是否保留。",
                        "object_refs": [
                            {
                                "object_type": operation.get("object_type", "casefile"),
                                "object_id": object_id,
                            },
                            {
                                "object_type": "structure_lock",
                                "object_id": lock["id"],
                            },
                        ],
                        "field_path": field_path,
                    }
                )
    return issues


def _pointer_parts(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise RuntimeError(f"Invalid suggestion path: {path}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _paths_overlap(path: str, locked_path: str) -> bool:
    return (
        path == locked_path
        or path.startswith(f"{locked_path}/")
        or locked_path.startswith(f"{path}/")
    )


__all__ = [
    "auto_thread_title",
    "find_frozen_object",
    "focused_patch_target_ids",
    "frozen_object_ids",
    "frozen_pointer_value",
    "nonblocking_validator_issues",
    "pointer_top_field",
    "unique_strings",
]
