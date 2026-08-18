"""Evidence pointer contract and resolver registry tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from casefile.agent_runtime.context import EvidenceRef, EvidenceRegistry


@dataclass(slots=True)
class _ThreadResolver:
    scheme: str = "thread"
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def resolve(self, ref: EvidenceRef) -> dict[str, Any] | None:
        return self.records.get(ref.identifier)

    def describe(self, ref: EvidenceRef) -> str:
        record = self.records.get(ref.identifier)
        if record is None:
            return ""
        return str(record.get("summary") or "")


def _registry_with_thread_resolver() -> tuple[EvidenceRegistry, _ThreadResolver]:
    resolver = _ThreadResolver(
        records={
            "message/7": {"summary": "已批准 Lucy 描述修改"},
        }
    )
    registry = EvidenceRegistry()
    registry.register(resolver)
    return registry, resolver


def test_evidence_ref_parse_and_roundtrip() -> None:
    ref = EvidenceRef.parse("thread://message/7")
    assert ref is not None
    assert ref.scheme == "thread"
    assert ref.identifier == "message/7"
    assert ref.to_uri() == "thread://message/7"


def test_evidence_ref_rejects_malformed_values() -> None:
    assert EvidenceRef.parse("missing-separator") is None
    assert EvidenceRef.parse("thread://") is None
    assert EvidenceRef.parse("://message/7") is None


def test_registry_describes_and_resolves_registered_scheme() -> None:
    registry, _resolver = _registry_with_thread_resolver()
    ref = EvidenceRef.parse("thread://message/7")
    assert ref is not None
    assert registry.resolve(ref) == {"summary": "已批准 Lucy 描述修改"}
    assert registry.describe(ref) == "已批准 Lucy 描述修改"


def test_registry_falls_back_to_uri_description_without_resolver() -> None:
    registry = EvidenceRegistry()
    ref = EvidenceRef.parse("taskrun://17")
    assert ref is not None
    assert registry.resolve(ref) is None
    assert registry.describe(ref) == "taskrun://17"


def test_registry_rejects_duplicate_or_invalid_scheme() -> None:
    registry = EvidenceRegistry()
    registry.register(_ThreadResolver())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_ThreadResolver())
    with pytest.raises(ValueError, match="Invalid evidence resolver scheme"):
        registry.register(_ThreadResolver(scheme="bad scheme"))


def test_validate_refs_reports_only_problem_references() -> None:
    registry, _resolver = _registry_with_thread_resolver()
    decisions = registry.validate_refs(
        [
            "thread://message/7",
            "thread://message/404",
            "patchset://9",
            "not-a-pointer",
            {"not": "a string"},
        ]
    )
    assert [decision.code for decision in decisions] == [
        "evidence_ref_unresolvable",
        "evidence_resolver_missing",
        "evidence_ref_invalid",
        "evidence_ref_invalid",
    ]
