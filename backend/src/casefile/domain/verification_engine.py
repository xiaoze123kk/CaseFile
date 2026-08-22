"""Pure verification core for CaseFile chat findings and patch batches.

This module deliberately knows nothing about HTTP, SQLAlchemy, workers, or
provider SDKs.  It accepts an immutable CaseFile snapshot and small plain
Python values so the same rules can be used by chat completion, patch review,
and synchronous preflight checks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

import rfc8785

from casefile.contracts import (
    ContractValidationError,
    public_validation_issues,
    validate_casefile,
    validate_casefile_semantics,
)

FindingKind = Literal["deterministic", "llm"]
FindingSeverity = Literal["info", "warning", "error", "blocker"]
FindingStatus = Literal["open", "resolved", "reopened", "dismissed"]

SEVERITY_RANK: dict[str, int] = {
    "info": 1,
    "warning": 2,
    "error": 3,
    "blocker": 4,
}
LEGACY_SEVERITY_MAP = {"S1": "blocker", "S2": "error", "S3": "warning"}
MAX_FINDINGS = 100
MAX_OPERATIONS = 100

_COLLECTION_BY_TYPE = {
    "resolution_spec": "resolution_specs",
    "entity": "entities",
    "relationship": "relationships",
    "location": "locations",
    "event": "events",
    "information_unit": "information_units",
    "claim": "claims",
    "hypothesis": "hypotheses",
    "reasoning_path": "reasoning_paths",
    "constraint": "constraints",
    "structure_lock": "structure_locks",
}
_STRUCTURAL_REFERENCE_TYPES = frozenset(_COLLECTION_BY_TYPE)
_MISSING = object()


@dataclass(frozen=True)
class FindingRef:
    """A normalized evidence, target, or related-domain reference."""

    ref_kind: str
    ref_key: str
    role: str = "evidence"

    def as_dict(self) -> dict[str, str]:
        return {
            "ref_kind": self.ref_kind,
            "ref_key": self.ref_key,
            "role": self.role,
        }


@dataclass(frozen=True)
class VerificationFinding:
    """The stable, JSON-safe finding contract emitted by the engine."""

    finding_key: str
    kind: FindingKind
    severity: FindingSeverity
    status: FindingStatus
    title: str
    message: str
    rule_code: str
    draft_revision: int = 1
    suggested_fix: str | None = None
    confidence: float | None = None
    refs: tuple[FindingRef, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_key": self.finding_key,
            "kind": self.kind,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
            "rule_code": self.rule_code,
            "confidence": self.confidence,
            "draft_revision": self.draft_revision,
            "refs": [ref.as_dict() for ref in self.refs],
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class PatchOperation:
    """One ordered field replacement in a proposed batch."""

    operation_id: str
    object_id: str
    field_path: str
    new_value: Any
    old_value: Any = _MISSING
    object_type: str | None = None
    operation_type: str = "replace"
    expected_object_revision: int | None = None


@dataclass(frozen=True)
class ImpactSummary:
    collections: tuple[str, ...]
    counts: Mapping[str, int]
    full_rebuild: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "collections": list(self.collections),
            "counts": dict(self.counts),
            "full_rebuild": self.full_rebuild,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class OperationDelta:
    ordinal: int
    operation_id: str
    object_id: str
    field_path: str
    old_value: Any
    new_value: Any
    object_type: str | None
    impact: ImpactSummary

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "operation_id": self.operation_id,
            "object_id": self.object_id,
            "field_path": self.field_path,
            "old_value": deepcopy(self.old_value),
            "new_value": deepcopy(self.new_value),
            "object_type": self.object_type,
            "impact": self.impact.as_dict(),
        }


@dataclass(frozen=True)
class BatchSimulation:
    valid: bool
    can_apply: bool
    reason_code: str | None
    document: Mapping[str, Any]
    deltas: tuple[OperationDelta, ...]
    baseline_findings: tuple[VerificationFinding, ...]
    final_findings: tuple[VerificationFinding, ...]
    fixed_finding_keys: tuple[str, ...]
    residual_finding_keys: tuple[str, ...]
    new_finding_keys: tuple[str, ...]
    pending_recheck_finding_keys: tuple[str, ...]
    severity_delta: Mapping[str, int]
    structure_lock_conflicts: tuple[str, ...]
    impact: ImpactSummary

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "can_apply": self.can_apply,
            "reason_code": self.reason_code,
            "deltas": [delta.as_dict() for delta in self.deltas],
            "baseline_findings": [item.as_dict() for item in self.baseline_findings],
            "final_findings": [item.as_dict() for item in self.final_findings],
            "fixed_finding_keys": list(self.fixed_finding_keys),
            "residual_finding_keys": list(self.residual_finding_keys),
            "new_finding_keys": list(self.new_finding_keys),
            "pending_recheck_finding_keys": list(self.pending_recheck_finding_keys),
            "severity_delta": dict(self.severity_delta),
            "structure_lock_conflicts": list(self.structure_lock_conflicts),
            "impact": self.impact.as_dict(),
        }


@dataclass(frozen=True)
class VerificationResult:
    findings: tuple[VerificationFinding, ...]
    structural_valid: bool
    engine_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "structural_valid": self.structural_valid,
            "findings": [finding.as_dict() for finding in self.findings],
        }


class VerificationEngine:
    """Run deterministic checks, normalize LLM findings, and simulate batches."""

    VERSION = "verification-engine-v1"

    def __init__(
        self,
        *,
        profile: Literal["fast", "balanced", "strict"] = "fast",
        draft_revision: int = 1,
        editable_fields_by_type: Mapping[str, Collection[str]] | None = None,
    ) -> None:
        if profile not in {"fast", "balanced", "strict"}:
            raise ValueError(f"Unsupported verification profile: {profile}")
        if draft_revision < 1:
            raise ValueError("draft_revision must be positive")
        self.profile = profile
        self.draft_revision = draft_revision
        self.editable_fields_by_type = {
            key: frozenset(value) for key, value in (editable_fields_by_type or {}).items()
        }

    def verify(
        self,
        document: Mapping[str, Any],
        *,
        llm_findings: Sequence[Mapping[str, Any]] = (),
    ) -> VerificationResult:
        """Return deterministic findings plus optional evidence-bound LLM findings."""

        deterministic, structural_valid = self._deterministic_findings(document)
        merged: dict[str, VerificationFinding] = {
            finding.finding_key: finding for finding in deterministic
        }
        if self.profile in {"balanced", "strict"}:
            for finding in self.normalize_llm_findings(llm_findings):
                if finding.finding_key not in merged:
                    merged[finding.finding_key] = finding
        if len(merged) > MAX_FINDINGS:
            raise ValueError(f"finding_limit_exceeded:{len(merged)}>{MAX_FINDINGS}")
        return VerificationResult(
            findings=tuple(merged.values()),
            structural_valid=structural_valid,
            engine_version=self.VERSION,
        )

    def normalize_llm_findings(
        self,
        findings: Sequence[Mapping[str, Any]],
    ) -> tuple[VerificationFinding, ...]:
        """Convert legacy casefile-chat findings to the shared finding contract."""

        result: list[VerificationFinding] = []
        seen: set[str] = set()
        for raw in findings:
            if not isinstance(raw, Mapping):
                raise ValueError("finding_item_must_be_object")
            kind = str(raw.get("kind", "")).strip()
            severity = str(raw.get("severity", "")).strip()
            title = str(raw.get("title", "")).strip()
            message = str(raw.get("statement", raw.get("message", ""))).strip()
            if not kind or not severity or not title or not message:
                raise ValueError("llm_finding_required_field_missing")
            normalized_severity = LEGACY_SEVERITY_MAP.get(severity, severity)
            if normalized_severity not in SEVERITY_RANK:
                raise ValueError(f"finding_severity_invalid:{severity}")
            refs = self._legacy_refs(raw)
            fingerprint = _fingerprint(
                {
                    "kind": kind,
                    "rule_code": raw.get("rule_code", f"llm.{kind}"),
                    "refs": [ref.as_dict() for ref in refs],
                }
            )[:24]
            finding_key = f"llm:{fingerprint}"
            if finding_key in seen:
                raise ValueError(f"finding_duplicate:{finding_key}")
            seen.add(finding_key)
            confidence = raw.get("confidence")
            if confidence is not None and (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= float(confidence) <= 1
            ):
                raise ValueError("finding_confidence_invalid")
            result.append(
                VerificationFinding(
                    finding_key=finding_key,
                    kind="llm",
                    severity=normalized_severity,  # type: ignore[arg-type]
                    status="open",
                    title=title,
                    message=message,
                    suggested_fix=(
                        str(raw["suggested_fix"]).strip()
                        if raw.get("suggested_fix") is not None
                        else None
                    ),
                    rule_code=str(raw.get("rule_code", f"llm.{kind}")),
                    confidence=None if confidence is None else float(confidence),
                    draft_revision=self.draft_revision,
                    refs=refs,
                    payload={
                        "legacy_finding_id": raw.get("finding_id"),
                        "legacy_kind": kind,
                        "needs_manual_review": bool(raw.get("needs_manual_review")),
                    },
                )
            )
        return tuple(result)

    def simulate_patch_operation_batch(
        self,
        document: Mapping[str, Any],
        operations: Sequence[PatchOperation],
        *,
        object_revisions: Mapping[str, int] | None = None,
        target_finding_keys: Sequence[str] = (),
        baseline_findings: Sequence[VerificationFinding] | None = None,
    ) -> BatchSimulation:
        """Apply ordered replacements to an in-memory copy and verify the result."""

        if len(operations) > MAX_OPERATIONS:
            return self._failed_batch("operation_limit_exceeded", document)
        baseline_result = self.verify(document)
        baseline = tuple(baseline_findings or baseline_result.findings)
        structural_errors = self._structural_errors(document)
        if structural_errors:
            return self._failed_batch("base_document_invalid", document, baseline)

        working = deepcopy(dict(document))
        revisions = object_revisions or {}
        deltas: list[OperationDelta] = []
        try:
            for ordinal, operation in enumerate(operations, start=1):
                self._validate_operation_shape(operation)
                if operation.operation_type != "replace":
                    raise _BatchError("operation_type_not_supported")
                if (
                    operation.expected_object_revision is not None
                    and revisions.get(operation.object_id) != operation.expected_object_revision
                ):
                    raise _BatchError("object_revision_conflict")
                found = _find_object(working, operation.object_id)
                if found is None:
                    raise _BatchError("object_not_found")
                object_type, item = found
                if operation.object_type is not None and operation.object_type != object_type:
                    raise _BatchError("object_type_conflict")
                top_field = _top_level_field(operation.field_path)
                allowed = self.editable_fields_by_type.get(object_type)
                if allowed is not None and top_field not in allowed:
                    raise _BatchError("field_not_editable")
                old_value = _pointer_get(item, operation.field_path)
                if old_value is _MISSING and operation.field_path == "/description":
                    old_value = None
                if old_value is _MISSING:
                    raise _BatchError("path_not_found")
                if operation.old_value is not _MISSING and old_value != operation.old_value:
                    raise _BatchError("old_value_conflict")
                if operation.field_path == "/description" and operation.new_value is None:
                    item.pop("description", None)
                else:
                    _pointer_set(item, operation.field_path, deepcopy(operation.new_value))
                impact = ImpactPlanner.plan(
                    operation.object_id,
                    object_type,
                    operation.field_path,
                    working,
                )
                deltas.append(
                    OperationDelta(
                        ordinal=ordinal,
                        operation_id=operation.operation_id,
                        object_id=operation.object_id,
                        field_path=operation.field_path,
                        old_value=deepcopy(old_value),
                        new_value=(
                            None
                            if operation.field_path == "/description"
                            and operation.new_value is None
                            else deepcopy(_pointer_get(item, operation.field_path))
                        ),
                        object_type=object_type,
                        impact=impact,
                    )
                )
        except _BatchError as error:
            return self._failed_batch(error.reason, document, baseline, tuple(deltas))

        final_result = self.verify(working)
        baseline_by_key = {finding.finding_key: finding for finding in baseline}
        final_by_key = {finding.finding_key: finding for finding in final_result.findings}
        fixed = tuple(sorted(set(baseline_by_key) - set(final_by_key)))
        target_set = set(target_finding_keys)
        pending_recheck = tuple(sorted(key for key in target_set if key.startswith("llm:")))
        deterministic_target_set = target_set - set(pending_recheck)
        residual = tuple(sorted(deterministic_target_set & set(final_by_key)))
        new = tuple(sorted(set(final_by_key) - set(baseline_by_key)))
        deterministic_new = [
            final_by_key[key] for key in new if final_by_key[key].kind == "deterministic"
        ]
        unresolved_deterministic_targets = [
            key
            for key in deterministic_target_set
            if key in final_by_key and final_by_key[key].kind == "deterministic"
        ]
        lock_conflicts = tuple(
            sorted(
                {
                    conflict
                    for delta in deltas
                    for conflict in _structure_lock_conflicts(
                        working, delta.object_id, delta.field_path
                    )
                }
            )
        )
        impact = ImpactPlanner.combine(delta.impact for delta in deltas)
        severity_delta = _severity_delta(baseline, final_result.findings)
        baseline_max = max(
            (SEVERITY_RANK[item.severity] for item in baseline if item.kind == "deterministic"),
            default=0,
        )
        new_same_or_higher = (
            any(SEVERITY_RANK[item.severity] >= baseline_max for item in deterministic_new)
            if baseline_max
            else bool(deterministic_new)
        )
        can_apply = not (
            not final_result.structural_valid
            or residual
            or unresolved_deterministic_targets
            or lock_conflicts
            or new_same_or_higher
        )
        reason_code = None
        if not final_result.structural_valid:
            reason_code = "post_document_invalid"
        elif residual or unresolved_deterministic_targets:
            reason_code = "finding_not_resolved"
        elif lock_conflicts:
            reason_code = "structure_lock_conflict"
        elif new_same_or_higher:
            reason_code = "deterministic_severity_regression"
        return BatchSimulation(
            valid=True,
            can_apply=can_apply,
            reason_code=reason_code,
            document=working,
            deltas=tuple(deltas),
            baseline_findings=baseline,
            final_findings=final_result.findings,
            fixed_finding_keys=fixed,
            residual_finding_keys=residual,
            new_finding_keys=new,
            pending_recheck_finding_keys=pending_recheck,
            severity_delta=severity_delta,
            structure_lock_conflicts=lock_conflicts,
            impact=impact,
        )

    def _deterministic_findings(
        self,
        document: Mapping[str, Any],
    ) -> tuple[tuple[VerificationFinding, ...], bool]:
        raw_errors: list[dict[str, Any]] = []
        try:
            validate_casefile(dict(document))
        except ContractValidationError as error:
            raw_errors.extend(error.errors)
        structural_valid = not raw_errors
        semantic_errors = [] if raw_errors else validate_casefile_semantics(dict(document))
        findings: list[VerificationFinding] = []
        for issue in [*raw_errors, *semantic_errors]:
            code = str(issue.get("code", "validation_failed"))
            path = str(issue.get("path", ""))
            refs = _refs_from_issue(issue, document)
            public_issues = public_validation_issues([issue])
            key = (
                f"det:{code}:"
                f"{_fingerprint({'path': path, 'refs': [r.as_dict() for r in refs]})[:24]}"
            )
            severity = LEGACY_SEVERITY_MAP.get(str(issue.get("severity", "S2")), "error")
            findings.append(
                VerificationFinding(
                    finding_key=key,
                    kind="deterministic",
                    severity=severity,  # type: ignore[arg-type]
                    status="open",
                    title=str(issue.get("title", code)),
                    message=str(issue.get("message", issue.get("explanation", code))),
                    suggested_fix=(str(issue["fix_hint"]) if issue.get("fix_hint") else None),
                    rule_code=code,
                    draft_revision=self.draft_revision,
                    refs=refs,
                    payload={
                        "path": path,
                        "public_issue": public_issues[0] if public_issues else None,
                        "impact_refs": deepcopy(issue.get("impact_refs", [])),
                    },
                )
            )
        return tuple(findings), structural_valid

    def _structural_errors(self, document: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            validate_casefile(dict(document))
        except ContractValidationError as error:
            return error.errors
        return []

    def _legacy_refs(self, finding: Mapping[str, Any]) -> tuple[FindingRef, ...]:
        refs: list[FindingRef] = []
        for key, ref_kind in (
            ("evidence_object_ids", "object"),
            ("evidence_event_ids", "event"),
            ("evidence_validation_issue_ids", "validation_issue"),
        ):
            values = finding.get(key) or []
            if not isinstance(values, list):
                raise ValueError(f"finding_evidence_invalid:{key}")
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"finding_evidence_invalid:{key}")
                refs.append(FindingRef(ref_kind, value.strip()))
        return tuple(_dedupe_refs(refs))

    def _validate_operation_shape(self, operation: PatchOperation) -> None:
        if not operation.operation_id.strip():
            raise _BatchError("operation_id_missing")
        if not operation.object_id.strip():
            raise _BatchError("object_id_missing")
        if not operation.field_path.startswith("/") or operation.field_path == "/":
            raise _BatchError("field_path_invalid")

    def _failed_batch(
        self,
        reason: str,
        document: Mapping[str, Any],
        baseline: Sequence[VerificationFinding] = (),
        deltas: Sequence[OperationDelta] = (),
    ) -> BatchSimulation:
        return BatchSimulation(
            valid=False,
            can_apply=False,
            reason_code=reason,
            document=deepcopy(dict(document)),
            deltas=tuple(deltas),
            baseline_findings=tuple(baseline),
            final_findings=tuple(baseline),
            fixed_finding_keys=(),
            residual_finding_keys=(),
            new_finding_keys=(),
            pending_recheck_finding_keys=(),
            severity_delta={},
            structure_lock_conflicts=(),
            impact=ImpactPlanner.combine(()),
        )


class ImpactPlanner:
    """Map field changes to Workbench refresh domains."""

    _ALL = ("timeline", "map", "relations", "reasoning", "evidence")

    @classmethod
    def plan(
        cls,
        object_id: str,
        object_type: str | None,
        field_path: str,
        document: Mapping[str, Any],
    ) -> ImpactSummary:
        del object_id, document
        field = _top_level_field(field_path)
        domains: set[str] = set()
        reasons: list[str] = []
        if object_type == "event" or field in {"time", "participant_refs", "location_ref"}:
            domains.update({"timeline", "map", "relations"})
        if object_type == "location" or field in {
            "spatial_position",
            "adjacency_refs",
            "travel_times",
        }:
            domains.update({"map", "relations"})
        if object_type in {"relationship"} or field.endswith("_refs"):
            domains.add("relations")
        if object_type in {"hypothesis", "reasoning_path", "resolution_spec", "constraint"}:
            domains.add("reasoning")
        if object_type in {"information_unit", "claim", "testimony"} or "evidence" in field:
            domains.add("evidence")
        if object_type is None or not domains:
            domains.update(cls._ALL)
            reasons.append("impact_unknown_requires_full_rebuild")
        if object_type == "resolution_spec" or field in {"conclusion", "structure_locks"}:
            domains.update(cls._ALL)
            reasons.append("structure_or_conclusion_change")
        return ImpactSummary(
            collections=tuple(domain for domain in cls._ALL if domain in domains),
            counts={domain: 1 for domain in cls._ALL if domain in domains},
            full_rebuild=set(cls._ALL).issubset(domains),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    @classmethod
    def combine(cls, impacts: Iterable[ImpactSummary]) -> ImpactSummary:
        domains: set[str] = set()
        reasons: list[str] = []
        counts: dict[str, int] = {}
        for impact in impacts:
            domains.update(impact.collections)
            reasons.extend(impact.reasons)
            for domain, count in impact.counts.items():
                counts[domain] = counts.get(domain, 0) + count
        return ImpactSummary(
            collections=tuple(domain for domain in cls._ALL if domain in domains),
            counts=counts,
            full_rebuild=set(cls._ALL).issubset(domains),
            reasons=tuple(dict.fromkeys(reasons)),
        )


class _BatchError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _find_object(document: Mapping[str, Any], object_id: str) -> tuple[str, dict[str, Any]] | None:
    for collection, values in document.items():
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value.get("id") == object_id:
                object_type = next(
                    (
                        candidate
                        for candidate, name in _COLLECTION_BY_TYPE.items()
                        if name == collection
                    ),
                    None,
                )
                return object_type or collection.rstrip("s"), value
    return None


def _pointer_parts(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _pointer_get(value: Any, path: str) -> Any:
    current = value
    for part in _pointer_parts(path):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError):
            return _MISSING
    return current


def _pointer_set(value: Any, path: str, new_value: Any) -> None:
    parts = _pointer_parts(path)
    current = value
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = new_value
    else:
        current[last] = new_value


def _top_level_field(path: str) -> str:
    if not path.startswith("/") or path == "/":
        return ""
    return _pointer_parts(path)[0]


def _refs_from_issue(
    issue: Mapping[str, Any], document: Mapping[str, Any]
) -> tuple[FindingRef, ...]:
    refs: list[FindingRef] = []
    for raw in issue.get("evidence_refs", []) or []:
        if isinstance(raw, Mapping):
            object_id = raw.get("object_id")
            object_type = raw.get("object_type")
            if isinstance(object_id, str) and isinstance(object_type, str):
                refs.append(
                    FindingRef(
                        "event" if object_type == "event" else "object",
                        object_id,
                        "evidence",
                    )
                )
    for raw in issue.get("impact_refs", []) or []:
        if isinstance(raw, Mapping):
            object_id = raw.get("object_id")
            object_type = raw.get("object_type")
            if isinstance(object_id, str) and isinstance(object_type, str):
                refs.append(
                    FindingRef(
                        "event" if object_type == "event" else "object",
                        object_id,
                        "related",
                    )
                )
    path = issue.get("path")
    if isinstance(path, str) and path:
        target = _target_from_path(document, path)
        if target is not None:
            refs.append(
                FindingRef(
                    "event" if target[0] == "event" else "object",
                    target[1],
                    "target",
                )
            )
    return tuple(_dedupe_refs(refs))


def _target_from_path(document: Mapping[str, Any], path: str) -> tuple[str, str] | None:
    parts = _pointer_parts(path) if path.startswith("/") and path != "/" else []
    if len(parts) < 2:
        return None
    collection, index = parts[0], parts[1]
    values = document.get(collection)
    if not isinstance(values, list):
        return None
    try:
        item = values[int(index)]
    except (IndexError, ValueError):
        return None
    if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
        return None
    object_type = next(
        (candidate for candidate, name in _COLLECTION_BY_TYPE.items() if name == collection),
        collection.rstrip("s"),
    )
    return object_type, str(item["id"])


def _structure_lock_conflicts(document: Mapping[str, Any], object_id: str, path: str) -> set[str]:
    conflicts: set[str] = set()
    locks = document.get("structure_locks")
    if not isinstance(locks, list):
        return conflicts
    for lock in locks:
        if not isinstance(lock, Mapping):
            continue
        target = lock.get("object_ref")
        if not isinstance(target, Mapping) or target.get("object_id") != object_id:
            continue
        paths = lock.get("field_paths")
        if not isinstance(paths, list):
            continue
        if any(isinstance(locked, str) and _paths_overlap(path, locked) for locked in paths):
            lock_id = lock.get("id")
            if isinstance(lock_id, str):
                conflicts.add(lock_id)
    return conflicts


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _severity_delta(
    before: Sequence[VerificationFinding],
    after: Sequence[VerificationFinding],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for finding in before:
        result[finding.severity] = result.get(finding.severity, 0) - 1
    for finding in after:
        result[finding.severity] = result.get(finding.severity, 0) + 1
    return result


def _dedupe_refs(refs: Sequence[FindingRef]) -> list[FindingRef]:
    seen: set[tuple[str, str, str]] = set()
    result: list[FindingRef] = []
    for ref in refs:
        key = (ref.ref_kind, ref.ref_key, ref.role)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


__all__ = [
    "BatchSimulation",
    "FindingRef",
    "FindingSeverity",
    "ImpactPlanner",
    "ImpactSummary",
    "OperationDelta",
    "PatchOperation",
    "VerificationEngine",
    "VerificationFinding",
    "VerificationResult",
]
