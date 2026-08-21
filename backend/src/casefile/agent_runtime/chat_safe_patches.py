"""Deterministic safe-patch handoff for CaseFile Chat audit finalizers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SafePatchCandidate:
    """One patch value proven safe by a frozen simulation ledger entry."""

    patch_id: str
    object_id: str
    path: str
    value_json: str
    canonical_value_json: str
    source_ordinal: int

    @property
    def target(self) -> tuple[str, str]:
        return self.object_id, self.path

    def as_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "object_id": self.object_id,
            "path": self.path,
            "value_json": self.value_json,
            "source_ordinal": self.source_ordinal,
        }


@dataclass(frozen=True, slots=True)
class SafePatchRegistry:
    """Server-owned safe patch candidates derived from one frozen ledger."""

    input_hash: str
    ledger_hash: str
    candidates: tuple[SafePatchCandidate, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "ledger_hash": self.ledger_hash,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }

    def candidates_for_target(
        self,
        object_id: str,
        path: str,
    ) -> tuple[SafePatchCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.target == (object_id, path)
        )

    def exact_candidate(
        self,
        object_id: str,
        path: str,
        value_json: object,
    ) -> SafePatchCandidate | None:
        canonical = canonicalize_value_json(value_json)
        if canonical is None:
            return None
        return next(
            (
                candidate
                for candidate in self.candidates_for_target(object_id, path)
                if candidate.canonical_value_json == canonical
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class PatchMaterialization:
    """One deterministic replacement of model-authored patch text."""

    suggestion_index: int
    target: str
    patch_id: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "suggestion_index": self.suggestion_index,
            "target": self.target,
            "patch_id": self.patch_id,
            "reason": self.reason,
        }


def canonicalize_value_json(value_json: object) -> str | None:
    """Parse and canonicalize a JSON-encoded patch value for equality checks."""

    if not isinstance(value_json, str) or not value_json.strip():
        return None
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compile_safe_patch_registry(
    ledger: dict[str, Any] | None,
    *,
    expected_input_hash: str | None = None,
) -> SafePatchRegistry:
    """Compile successful, non-regressing simulations into stable candidates."""

    if not isinstance(ledger, dict):
        return SafePatchRegistry(input_hash="", ledger_hash="")
    input_hash = str(ledger.get("input_hash") or "")
    ledger_hash = str(ledger.get("ledger_hash") or "")
    if expected_input_hash is not None and input_hash != expected_input_hash:
        return SafePatchRegistry(input_hash=input_hash, ledger_hash=ledger_hash)
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return SafePatchRegistry(input_hash=input_hash, ledger_hash=ledger_hash)

    candidates: list[SafePatchCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for fallback_ordinal, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        if entry.get("tool_name") != "simulate_patch_application":
            continue
        if entry.get("status") != "ok":
            continue
        arguments = entry.get("sanitized_arguments")
        result = entry.get("bounded_result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        counts = result.get("counts")
        if not isinstance(counts, dict):
            continue
        if result.get("valid") is not True:
            continue
        if result.get("advice") == "introduces_new_issues":
            continue
        new_count = counts.get("new")
        if isinstance(new_count, bool) or not isinstance(new_count, int) or new_count != 0:
            continue
        object_id = arguments.get("object_id")
        path = arguments.get("path")
        value_json = arguments.get("value_json")
        if not isinstance(object_id, str) or not object_id:
            continue
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(value_json, str) or not value_json:
            continue
        canonical = canonicalize_value_json(value_json)
        if canonical is None:
            continue
        key = (object_id, path, canonical)
        if key in seen:
            continue
        seen.add(key)
        raw_ordinal = entry.get("ordinal")
        ordinal = (
            raw_ordinal
            if isinstance(raw_ordinal, int) and not isinstance(raw_ordinal, bool)
            else fallback_ordinal
        )
        candidates.append(
            SafePatchCandidate(
                patch_id=f"P{ordinal}",
                object_id=object_id,
                path=path,
                value_json=value_json,
                canonical_value_json=canonical,
                source_ordinal=ordinal,
            )
        )
    return SafePatchRegistry(
        input_hash=input_hash,
        ledger_hash=ledger_hash,
        candidates=tuple(candidates),
    )


def safe_patch_registry_from_dict(payload: dict[str, Any]) -> SafePatchRegistry:
    """Validate the small internal registry view received through a request."""

    candidates: list[SafePatchCandidate] = []
    for item in payload.get("candidates", []):
        if not isinstance(item, dict):
            continue
        canonical = canonicalize_value_json(item.get("value_json"))
        source_ordinal = item.get("source_ordinal")
        if canonical is None or not isinstance(source_ordinal, int):
            continue
        candidates.append(
            SafePatchCandidate(
                patch_id=str(item.get("patch_id") or ""),
                object_id=str(item.get("object_id") or ""),
                path=str(item.get("path") or ""),
                value_json=str(item.get("value_json") or ""),
                canonical_value_json=canonical,
                source_ordinal=source_ordinal,
            )
        )
    return SafePatchRegistry(
        input_hash=str(payload.get("input_hash") or ""),
        ledger_hash=str(payload.get("ledger_hash") or ""),
        candidates=tuple(candidates),
    )


def materialize_unique_safe_patches(
    suggestions: list[dict[str, Any]],
    registry: SafePatchRegistry,
) -> tuple[list[dict[str, Any]], tuple[PatchMaterialization, ...]]:
    """Replace model patch text only when the frozen safe choice is unambiguous."""

    materialized: list[dict[str, Any]] = []
    changes: list[PatchMaterialization] = []
    for index, suggestion in enumerate(suggestions):
        item = dict(suggestion)
        object_id = str(item.get("object_id") or "")
        path = str(item.get("path") or "")
        candidates = registry.candidates_for_target(object_id, path)
        exact = registry.exact_candidate(object_id, path, item.get("value_json"))
        selected = exact or (candidates[0] if len(candidates) == 1 else None)
        if selected is not None and item.get("value_json") != selected.value_json:
            item["value_json"] = selected.value_json
            changes.append(
                PatchMaterialization(
                    suggestion_index=index,
                    target=f"{object_id}:{path}",
                    patch_id=selected.patch_id,
                    reason="canonical_match" if exact is not None else "unique_safe_target",
                )
            )
        materialized.append(item)
    return materialized, tuple(changes)


__all__ = [
    "PatchMaterialization",
    "SafePatchCandidate",
    "SafePatchRegistry",
    "canonicalize_value_json",
    "compile_safe_patch_registry",
    "materialize_unique_safe_patches",
    "safe_patch_registry_from_dict",
]
