"""Deterministic safe-patch handoff for CaseFile Chat audit finalizers."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, cast

from casefile.agent_runtime.chat_tools import (
    check_patch_proposal,
    patch_target_string_value,
    simulate_patch_delta,
)
from casefile.agent_runtime.chat_validation_contracts import (
    ChatCompletionValidationError,
    resolve_authoritative_repair_target,
    target_label,
)
from casefile.agent_runtime.models import (
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatResult,
    CaseFileChatSuggestionCandidateV2,
    CaseFileChatTargetLockedRepairOutput,
)


@dataclass(frozen=True, slots=True)
class SafePatchCandidate:
    """One patch value proven safe by the server deterministic gate."""

    patch_id: str
    object_id: str
    path: str
    value_json: str
    canonical_value_json: str
    source_ordinal: int
    finding_ref: str | None = None
    validation_passed: bool = True
    simulation_passed: bool = True
    new_issue_count: int = 0
    source: str = "server_post_finalizer_gate"

    @property
    def target(self) -> tuple[str, str]:
        return self.object_id, self.path

    def as_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "object_id": self.object_id,
            "path": self.path,
            "value_json": self.value_json,
            "canonical_value_json": self.canonical_value_json,
            "source_ordinal": self.source_ordinal,
            "finding_ref": self.finding_ref,
            "validation_passed": self.validation_passed,
            "simulation_passed": self.simulation_passed,
            "new_issue_count": self.new_issue_count,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SafePatchRegistry:
    """Server-owned record of patch values proved safe for one frozen input."""

    input_hash: str
    ledger_hash: str
    candidates: tuple[SafePatchCandidate, ...] = ()
    source: str = "server_post_finalizer_gate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "ledger_hash": self.ledger_hash,
            "source": self.source,
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


@dataclass(frozen=True, slots=True)
class SafePatchGateFailure:
    """One finalizer proposal rejected by the server deterministic gate."""

    suggestion_index: int
    object_id: str
    path: str
    reason_code: str
    validation: dict[str, Any]
    simulation: dict[str, Any]

    @property
    def target(self) -> str:
        return f"{self.object_id}:{self.path}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "suggestion_index": self.suggestion_index,
            "object_id": self.object_id,
            "path": self.path,
            "target": self.target,
            "reason_code": self.reason_code,
            "validation": self.validation,
            "simulation": self.simulation,
        }


@dataclass(frozen=True, slots=True)
class SafePatchGateDiscard:
    """One non-blocking redundant proposal removed by the server gate."""

    suggestion_index: int
    object_id: str
    path: str
    reason_code: str
    retained_patch_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "suggestion_index": self.suggestion_index,
            "object_id": self.object_id,
            "path": self.path,
            "target": f"{self.object_id}:{self.path}",
            "reason_code": self.reason_code,
            "retained_patch_id": self.retained_patch_id,
        }


@dataclass(frozen=True, slots=True)
class SafePatchGateResult:
    """Proof record and rejections produced after one audit finalizer run."""

    registry: SafePatchRegistry
    failures: tuple[SafePatchGateFailure, ...] = ()
    discards: tuple[SafePatchGateDiscard, ...] = ()


def server_gate_audit_suggestions(
    request: CaseFileChatRequest,
    suggestions: list[dict[str, Any]],
) -> SafePatchGateResult:
    """Prove audit proposals safe after finalization without a model tool call.

    The check reuses the exact patch whitelist and dry-run delta used by
    ``simulate_patch_application``.  The ledger remains evidence only; it is
    no longer an accidental source of safe patch supply.
    """

    candidates: list[SafePatchCandidate] = []
    failures: list[SafePatchGateFailure] = []
    discards: list[SafePatchGateDiscard] = []
    seen: set[tuple[str, str, str]] = set()
    approved_targets: dict[tuple[str, str], SafePatchCandidate] = {}
    for index, suggestion in enumerate(suggestions):
        object_id = suggestion.get("object_id")
        path = suggestion.get("path")
        value_json = suggestion.get("value_json")
        if not isinstance(object_id, str) or not isinstance(path, str) or not isinstance(
            value_json, str
        ):
            failures.append(
                SafePatchGateFailure(
                    suggestion_index=index,
                    object_id=str(object_id or ""),
                    path=str(path or ""),
                    reason_code="proposal_shape_invalid",
                    validation={},
                    simulation={},
                )
            )
            continue
        retained = approved_targets.get((object_id, path))
        if retained is not None:
            discards.append(
                SafePatchGateDiscard(
                    suggestion_index=index,
                    object_id=object_id,
                    path=path,
                    reason_code="duplicate_patch_target",
                    retained_patch_id=retained.patch_id,
                )
            )
            continue
        value_json = _normalize_string_value_json(request, object_id, path, value_json)
        check = check_patch_proposal(
            request,
            object_id,
            path,
            value_json,
            require_path_exists=True,
        )
        if check.reason_code is not None:
            failures.append(
                SafePatchGateFailure(
                    suggestion_index=index,
                    object_id=object_id,
                    path=path,
                    reason_code=check.reason_code,
                    validation={
                        "reason_code": check.reason_code,
                        "allowed_fields": list(check.allowed_fields),
                    },
                    simulation={},
                )
            )
            continue
        simulation = simulate_patch_delta(
            request.casefile,
            request.validation_issues,
            object_id,
            path,
            value_json,
        )
        counts = simulation.get("counts")
        raw_new_issue_count = counts.get("new") if isinstance(counts, dict) else None
        new_issue_count = (
            int(raw_new_issue_count)
            if isinstance(raw_new_issue_count, int)
            and not isinstance(raw_new_issue_count, bool)
            else -1
        )
        safe = (
            simulation.get("valid") is True
            and simulation.get("advice") != "introduces_new_issues"
            and new_issue_count == 0
        )
        if not safe:
            failures.append(
                SafePatchGateFailure(
                    suggestion_index=index,
                    object_id=object_id,
                    path=path,
                    reason_code=str(simulation.get("reason_code") or "simulation_failed"),
                    validation={"reason_code": None, "allowed_fields": list(check.allowed_fields)},
                    simulation=simulation,
                )
            )
            continue
        canonical = canonicalize_value_json(value_json)
        if canonical is None:
            raise RuntimeError("validated patch value failed canonicalization")
        key = (object_id, path, canonical)
        if key in seen:
            continue
        seen.add(key)
        finding_ref = suggestion.get("finding_ref")
        candidate = SafePatchCandidate(
            patch_id=f"P{index + 1}",
            object_id=object_id,
            path=path,
            value_json=canonical,
            canonical_value_json=canonical,
            source_ordinal=index + 1,
            finding_ref=finding_ref if isinstance(finding_ref, str) else None,
            validation_passed=True,
            simulation_passed=True,
            new_issue_count=new_issue_count,
        )
        candidates.append(candidate)
        approved_targets[(object_id, path)] = candidate
    return SafePatchGateResult(
        registry=SafePatchRegistry(
            input_hash=request.input_hash,
            ledger_hash="",
            candidates=tuple(candidates),
        ),
        failures=tuple(failures),
        discards=tuple(discards),
    )


def _normalize_string_value_json(
    request: CaseFileChatRequest,
    object_id: str,
    path: str,
    value_json: str,
) -> str:
    """Encode an unquoted plain-text proposal only for a frozen string field."""

    if canonicalize_value_json(value_json) is not None:
        return value_json
    if patch_target_string_value(request, object_id, path) is None:
        return value_json
    stripped = value_json.strip()
    if not stripped or stripped.startswith("```"):
        return value_json
    return json.dumps(stripped, ensure_ascii=False, separators=(",", ":"))


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
        return SafePatchRegistry(input_hash="", ledger_hash="", source="ledger_simulation")
    input_hash = str(ledger.get("input_hash") or "")
    ledger_hash = str(ledger.get("ledger_hash") or "")
    if expected_input_hash is not None and input_hash != expected_input_hash:
        return SafePatchRegistry(
            input_hash=input_hash,
            ledger_hash=ledger_hash,
            source="ledger_simulation",
        )
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return SafePatchRegistry(
            input_hash=input_hash,
            ledger_hash=ledger_hash,
            source="ledger_simulation",
        )

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
                source="ledger_simulation",
            )
        )
    return SafePatchRegistry(
        input_hash=input_hash,
        ledger_hash=ledger_hash,
        candidates=tuple(candidates),
        source="ledger_simulation",
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
                finding_ref=(
                    str(item.get("finding_ref"))
                    if isinstance(item.get("finding_ref"), str)
                    else None
                ),
                validation_passed=bool(item.get("validation_passed", True)),
                simulation_passed=bool(item.get("simulation_passed", True)),
                new_issue_count=(
                    int(item.get("new_issue_count", 0))
                    if isinstance(item.get("new_issue_count", 0), int)
                    else 0
                ),
                source=str(item.get("source") or "server_post_finalizer_gate"),
            )
        )
    return SafePatchRegistry(
        input_hash=str(payload.get("input_hash") or ""),
        ledger_hash=str(payload.get("ledger_hash") or ""),
        candidates=tuple(candidates),
        source=str(payload.get("source") or "server_post_finalizer_gate"),
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




def target_locked_repair_contract(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    validation: ChatCompletionValidationError,
) -> dict[str, Any] | None:
    """Return a server-owned hard-repair contract only for one exact audit delta."""

    route = request.route
    if (
        request.prompt_version != "casefile-chat-v15"
        or route is None
        or route.execution_profile.get("primary_intent") != "logic_audit"
        or not isinstance(result.candidate, CaseFileChatCandidateV2)
    ):
        return None
    bundle = request.validation.get("audit_evidence_bundle")
    if not isinstance(bundle, dict):
        return None
    return resolve_authoritative_repair_target(
        bundle=bundle,
        findings=tuple(
            finding.model_dump(mode="json")
            for finding in result.candidate.audit_findings
        ),
        issues=validation.issues,
        repair_plan=validation.repair_plan,
    )


def materialize_target_locked_repair(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Compose a full audit candidate from a locked target and minimal model output."""

    contract = request.target_locked_repair
    repair_output = cast(CaseFileChatTargetLockedRepairOutput, result.candidate)
    if not isinstance(contract, dict) or not isinstance(
        repair_output, CaseFileChatTargetLockedRepairOutput
    ):
        raise ChatCompletionValidationError(code="audit_target_locked_repair_output_invalid")
    object_id = contract.get("object_id")
    path = contract.get("path")
    finding_ref = contract.get("finding_ref")
    previous = request.previous_candidate
    if not (
        isinstance(object_id, str)
        and isinstance(path, str)
        and isinstance(finding_ref, str)
        and isinstance(previous, dict)
    ):
        raise ChatCompletionValidationError(code="audittarget_locked_repair_contract_invalid")
    try:
        candidate = CaseFileChatCandidateV2.model_validate(previous)
    except ValueError as error:
        raise ChatCompletionValidationError(
            code="audittarget_locked_repair_contract_invalid"
        ) from error
    if not any(
        finding.finding_id == finding_ref and not finding.needs_manual_review
        for finding in candidate.audit_findings
    ):
        raise ChatCompletionValidationError(code="audittarget_locked_repair_contract_invalid")
    canonical_value = canonicalize_value_json(repair_output.value_json)
    if canonical_value is None:
        raise ChatCompletionValidationError(code="audit_target_locked_repair_value_invalid")
    previous_failure = contract.get("previous_failure")
    previous_value = (
        previous_failure.get("value_json")
        if isinstance(previous_failure, dict)
        else None
    )
    if (
        previous_value is not None
        and canonicalize_value_json(previous_value) == canonical_value
    ):
        raise ChatCompletionValidationError(
            code="audit_target_locked_repair_no_progress"
        )
    remove = {
        value for value in contract.get("remove", ()) if isinstance(value, str)
    }
    target = target_label(object_id, path)
    remove.add(target)
    suggestions = [
        suggestion
        for suggestion in candidate.suggestions
        if target_label(suggestion.object_id, suggestion.path) not in remove
    ]
    suggestions.append(
        CaseFileChatSuggestionCandidateV2(
            object_id=object_id,
            path=path,
            value_json=repair_output.value_json,
            reason=repair_output.reason,
            finding_ref=finding_ref,
        )
    )
    return replace(result, candidate=candidate.model_copy(update={"suggestions": suggestions}))

__all__ = [
    "PatchMaterialization",
    "SafePatchCandidate",
    "SafePatchGateDiscard",
    "SafePatchRegistry",
    "canonicalize_value_json",
    "compile_safe_patch_registry",
    "materialize_target_locked_repair",
    "materialize_unique_safe_patches",
    "safe_patch_registry_from_dict",
    "target_locked_repair_contract",
]
