"""Stable evidence pointers with pluggable resolvers.

Context compaction must never delete evidence: it only replaces raw payloads
with ``scheme://identifier`` references. Concrete database resolvers
(thread://message/{id}, taskrun://{id}, patchset://{id}) are adapters owned by
the Worker transaction boundary and get registered when a stage first consumes
them; this module provides the versioned pointer contract and registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from casefile.agent_runtime.context.models import ContextDecision

_SCHEME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Lossless pointer to immutable evidence stored outside working context."""

    scheme: str
    identifier: str
    description: str = ""

    def to_uri(self) -> str:
        return f"{self.scheme}://{self.identifier}"

    @classmethod
    def parse(cls, value: str) -> EvidenceRef | None:
        if "://" not in value:
            return None
        scheme, identifier = value.split("://", 1)
        scheme = scheme.strip().lower()
        identifier = identifier.strip()
        if _SCHEME.fullmatch(scheme) is None or not identifier:
            return None
        return cls(scheme=scheme, identifier=identifier)


@runtime_checkable
class EvidenceResolver(Protocol):
    """Pure resolver for one evidence scheme."""

    scheme: str

    def resolve(self, ref: EvidenceRef) -> Any | None: ...

    def describe(self, ref: EvidenceRef) -> str: ...


@dataclass(slots=True)
class EvidenceRegistry:
    """Scheme-addressed resolver table; one resolver per scheme."""

    _resolvers: dict[str, EvidenceResolver] = field(default_factory=dict)

    def register(self, resolver: EvidenceResolver) -> None:
        scheme = resolver.scheme.strip().lower()
        if _SCHEME.fullmatch(scheme) is None:
            raise ValueError(f"Invalid evidence resolver scheme: {resolver.scheme!r}")
        if scheme in self._resolvers:
            raise ValueError(f"Evidence resolver for scheme {scheme!r} already registered")
        self._resolvers[scheme] = resolver

    def resolver_for(self, scheme: str) -> EvidenceResolver | None:
        return self._resolvers.get(scheme.strip().lower())

    def resolve(self, ref: EvidenceRef) -> Any | None:
        resolver = self.resolver_for(ref.scheme)
        if resolver is None:
            return None
        return resolver.resolve(ref)

    def describe(self, ref: EvidenceRef) -> str:
        resolver = self.resolver_for(ref.scheme)
        if resolver is None:
            return ref.description or ref.to_uri()
        description = resolver.describe(ref)
        return description or ref.description or ref.to_uri()

    def validate_refs(self, refs: Any) -> tuple[ContextDecision, ...]:
        """Return one decision per invalid, missing or unresolvable reference.

        Resolvable references intentionally produce no decision so the context
        manifest only carries problems worth auditing.
        """

        decisions: list[ContextDecision] = []
        for raw in refs:
            if not isinstance(raw, str):
                decisions.append(
                    ContextDecision(
                        stage="evidence.pointer",
                        code="evidence_ref_invalid",
                        detail=f"reference must be a string: {raw!r}",
                    )
                )
                continue
            ref = EvidenceRef.parse(raw)
            if ref is None:
                decisions.append(
                    ContextDecision(
                        stage="evidence.pointer",
                        code="evidence_ref_invalid",
                        detail=f"invalid evidence reference: {raw!r}",
                    )
                )
                continue
            if self.resolver_for(ref.scheme) is None:
                decisions.append(
                    ContextDecision(
                        stage="evidence.pointer",
                        code="evidence_resolver_missing",
                        detail=f"no resolver registered for scheme {ref.scheme!r}",
                    )
                )
                continue
            if self.resolve(ref) is None:
                decisions.append(
                    ContextDecision(
                        stage="evidence.pointer",
                        code="evidence_ref_unresolvable",
                        detail=f"resolver found no payload for {ref.to_uri()}",
                    )
                )
        return tuple(decisions)


__all__ = [
    "EvidenceRef",
    "EvidenceRegistry",
    "EvidenceResolver",
]
