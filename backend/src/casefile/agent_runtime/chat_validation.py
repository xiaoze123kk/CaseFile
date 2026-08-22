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
    replace: tuple[Mapping[str, Any], ...] = ()
    fix: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "preserve": list(self.preserve),
            "add": list(self.add),
            "remove": list(self.remove),
            "replace": [dict(item) for item in self.replace],
            "fix": [dict(item) for item in self.fix],
        }

    def is_empty(self) -> bool:
        return not (self.preserve or self.add or self.remove or self.replace or self.fix)


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
    replacements: list[Mapping[str, Any]] = []
    fixes: list[Mapping[str, Any]] = []
    for issue in issues:
        if not issue.repairable:
            continue
        details = issue.details
        preserve.update(str(item) for item in details.get("preserve", ()))
        add.update(str(item) for item in details.get("missing", ()))
        remove.update(str(item) for item in details.get("extra", ()))
        replacements.extend(
            dict(item) for item in details.get("replace", ()) if isinstance(item, Mapping)
        )
        fixes.append(
            {
                "code": issue.code,
                "path": issue.path,
                **({"details": dict(details)} if details else {}),
            }
        )
    # A target can be rejected for its current value and simultaneously be
    # required by the audit capability contract.  The next Finalizer must
    # rebuild that target, not receive contradictory add/remove instructions.
    remove.difference_update(add)
    return RepairPlan(
        preserve=tuple(sorted(preserve)),
        add=tuple(sorted(add)),
        remove=tuple(sorted(remove)),
        replace=tuple(replacements),
        fix=tuple(fixes),
    )


def resolve_authoritative_repair_target(
    *,
    bundle: Mapping[str, Any],
    findings: tuple[Mapping[str, Any], ...],
    issues: tuple[ValidationIssue, ...],
    repair_plan: RepairPlan,
) -> dict[str, Any] | None:
    """Resolve one server-owned audit target without trusting model identity."""

    if not issues or any(not issue.repairable for issue in issues):
        return None
    expectation = bundle.get("repair_expectation")
    targets = (
        expectation.get("candidate_patch_targets")
        if isinstance(expectation, Mapping)
        else None
    )
    if not isinstance(targets, list):
        return None
    expected_by_label = {
        target_label(item.get("object_id"), item.get("path")): item
        for item in targets
        if isinstance(item, Mapping)
        and isinstance(item.get("object_id"), str)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("current_value_json"), str)
        and isinstance(item.get("value_type"), str)
    }
    required = set(repair_plan.add) & set(expected_by_label)
    if len(required) != 1 or repair_plan.replace:
        return None
    label = next(iter(required))
    locked_target = expected_by_label[label]
    object_id = locked_target["object_id"]
    path = locked_target["path"]
    finding_refs = {
        str(issue.details["finding_ref"])
        for issue in issues
        if issue.details.get("object_id") == object_id
        and issue.details.get("path") == path
        and isinstance(issue.details.get("finding_ref"), str)
    }
    if len(finding_refs) != 1:
        return None
    finding_ref = next(iter(finding_refs))
    if not any(
        item.get("finding_id") == finding_ref and not item.get("needs_manual_review")
        for item in findings
    ):
        return None
    failure_issue = next(
        (
            issue
            for issue in issues
            if issue.details.get("object_id") == object_id
            and issue.details.get("path") == path
            and issue.details.get("reason_code")
        ),
        None,
    )
    return {
        "issue_code": issues[0].code,
        "object_id": object_id,
        "path": path,
        "finding_ref": finding_ref,
        "preserve": list(repair_plan.preserve),
        "remove": list(repair_plan.remove),
        "current_value_json": locked_target["current_value_json"],
        "value_type": locked_target["value_type"],
        "previous_failure": {
            "value_json": failure_issue.details.get("value_json") if failure_issue else None,
            "reason_code": failure_issue.details.get("reason_code") if failure_issue else None,
            "issue_codes": sorted({issue.code for issue in issues}),
        },
    }


def select_semantic_repair_mode(
    *,
    attempt: int,
    repair_plan: RepairPlan,
    has_authoritative_target: bool,
    currently_target_locked: bool,
    no_progress: bool,
    max_attempts: int = 4,
) -> Literal["minimal", "target_locked"] | None:
    """Choose the next bounded repair mode without invoking a provider."""

    if attempt >= max_attempts:
        return None
    if currently_target_locked:
        return "target_locked" if attempt < max_attempts else None
    if repair_plan.is_empty():
        return None
    if attempt == 1:
        return "minimal"
    if has_authoritative_target:
        return "target_locked"
    if attempt == 2 and not no_progress:
        return "minimal"
    return None


__all__ = [
    "RepairPlan",
    "ValidationIssue",
    "ValidationReport",
    "plan_repairs",
    "resolve_authoritative_repair_target",
    "select_semantic_repair_mode",
    "target_label",
]
