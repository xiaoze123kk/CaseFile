"""Pure validation and repair contracts for the CaseFile Chat pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

ValidationStage = Literal["schema", "reference", "audit", "edit", "patch"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable, JSON-safe validation problem."""

    code: str
    stage: ValidationStage
    path: str
    message: str
    repairable: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "path": self.path,
            "message": self.message,
            "repairable": self.repairable,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """Exact delta that a structured finalizer may apply once."""

    preserve: tuple[str, ...] = ()
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    fix: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "preserve": list(self.preserve),
            "add": list(self.add),
            "remove": list(self.remove),
            "fix": [dict(item) for item in self.fix],
        }

    def is_empty(self) -> bool:
        return not (self.preserve or self.add or self.remove or self.fix)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Normalized candidate plus every issue discovered in one pass."""

    normalized_candidate: Mapping[str, Any] | None
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues


def target_label(object_id: object, path: object) -> str:
    return f"{object_id}:{path}"


def plan_repairs(issues: tuple[ValidationIssue, ...]) -> RepairPlan:
    """Deterministically merge repair deltas supplied by validators."""

    preserve: set[str] = set()
    add: set[str] = set()
    remove: set[str] = set()
    fixes: list[Mapping[str, Any]] = []
    for issue in issues:
        if not issue.repairable:
            continue
        details = issue.details
        preserve.update(str(item) for item in details.get("preserve", ()))
        add.update(str(item) for item in details.get("missing", ()))
        remove.update(str(item) for item in details.get("extra", ()))
        fixes.append(
            {
                "code": issue.code,
                "path": issue.path,
                **({"details": dict(details)} if details else {}),
            }
        )
    return RepairPlan(
        preserve=tuple(sorted(preserve)),
        add=tuple(sorted(add)),
        remove=tuple(sorted(remove)),
        fix=tuple(fixes),
    )


__all__ = [
    "RepairPlan",
    "ValidationIssue",
    "ValidationReport",
    "plan_repairs",
    "target_label",
]
