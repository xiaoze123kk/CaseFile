"""Deterministic release benchmark for the CaseFile validator stack.

The benchmark is deliberately separate from the live Chat T1 suite.  It measures
three deterministic layers:

V0  Verification rules + legacy LLM finding normalization.
V1  Patch simulation/gating using :class:`VerificationEngine`.
V2  ValidationIssue -> RepairPlan compilation.

No provider/network call is allowed in this module.  A failing row therefore
means a validator/fixture contract mismatch rather than model sampling noise.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from casefile.agent_runtime.chat_safe_patches import server_gate_audit_suggestions
from casefile.agent_runtime.chat_validation import (
    ValidationIssue,
    plan_repairs,
    resolve_authoritative_repair_target,
    select_semantic_repair_mode,
)
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.application.v1_editing import EDITABLE_FIELDS, editable_fields_by_collection
from casefile.application.verification_engine import PatchOperation, VerificationEngine

REPORT_SCHEMA_VERSION = "casefile-validator-benchmark-report-v1"
SUITE_SCHEMA_VERSION = "casefile-validator-benchmark-v1"
DEFAULT_SUITE_RELATIVE = Path("fixtures/validator_benchmark")


@dataclass(frozen=True, slots=True)
class CaseVerdict:
    case_id: str
    layer: str
    passed: bool
    expected: Mapping[str, Any]
    actual: Mapping[str, Any]
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "layer": self.layer,
            "passed": self.passed,
            "expected": dict(self.expected),
            "actual": dict(self.actual),
            "failures": list(self.failures),
        }


def _repo_root_from_module() -> Path:
    # backend/src/casefile/benchmark/validator_eval.py -> repo root
    return Path(__file__).resolve().parents[4]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark fixture must be an object: {path}")
    if payload.get("schema_version") != SUITE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported validator benchmark schema in {path}: "
            f"{payload.get('schema_version')!r}"
        )
    return payload


def _fixture_document(repo_root: Path, relative_path: str) -> dict[str, Any]:
    path = (repo_root / relative_path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"CaseFile fixture must be an object: {path}")
    return payload


def _pointer_parts(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _pointer_parts(pointer)
    if not parts:
        raise ValueError("root mutation is not supported")
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"pointer parent is not traversable: {pointer}")
    return current, parts[-1]


def apply_mutations(
    document: dict[str, Any],
    mutations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the tiny JSON-Patch subset used by benchmark fixtures."""

    result = deepcopy(document)
    for mutation in mutations:
        op = str(mutation.get("op") or "")
        pointer = str(mutation.get("path") or "")
        parent, key = _pointer_parent(result, pointer)
        if op in {"add", "replace"}:
            value = deepcopy(mutation.get("value"))
            if isinstance(parent, list):
                if op == "add" and key == "-":
                    parent.append(value)
                else:
                    parent[int(key)] = value
            elif isinstance(parent, dict):
                if op == "replace" and key not in parent:
                    raise ValueError(f"replace target is missing: {pointer}")
                parent[key] = value
            else:
                raise ValueError(f"mutation target is not writable: {pointer}")
        elif op == "remove":
            if isinstance(parent, list):
                parent.pop(int(key))
            elif isinstance(parent, dict):
                del parent[key]
            else:
                raise ValueError(f"mutation target is not removable: {pointer}")
        else:
            raise ValueError(f"unsupported benchmark mutation op: {op}")
    return result


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _exact_rule_codes(expected: Mapping[str, Any]) -> set[str] | None:
    raw = expected.get("exact_rule_codes")
    if raw is None:
        return None
    return {str(value) for value in raw}


def _run_v0_document_case(
    repo_root: Path,
    base_fixture: str,
    case: Mapping[str, Any],
) -> CaseVerdict:
    case_id = str(case["case_id"])
    document = apply_mutations(
        _fixture_document(repo_root, str(case.get("base_fixture") or base_fixture)),
        case.get("mutations", ()),
    )
    expected = case.get("expected") or {}
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case_id}: expected must be an object")

    engine = VerificationEngine(profile="fast")
    first = engine.verify(document)
    second = engine.verify(document)
    actual_codes = [finding.rule_code for finding in first.findings]
    actual_code_set = set(actual_codes)
    required = {str(value) for value in expected.get("required_rule_codes", ())}
    forbidden = {str(value) for value in expected.get("forbidden_rule_codes", ())}
    exact = _exact_rule_codes(expected)
    min_findings = int(expected.get("min_findings", 0))
    expected_structural = bool(expected.get("structural_valid", True))
    identity_stable = [finding.finding_key for finding in first.findings] == [
        finding.finding_key for finding in second.findings
    ]

    failures: list[str] = []
    if first.structural_valid != expected_structural:
        failures.append("structural_valid")
    if not required.issubset(actual_code_set):
        failures.append("missing_required_rule")
    if forbidden & actual_code_set:
        failures.append("forbidden_rule")
    if exact is not None and actual_code_set != exact:
        failures.append("rule_code_exact_match")
    if len(first.findings) < min_findings:
        failures.append("minimum_finding_count")
    if not identity_stable:
        failures.append("finding_identity_stability")

    return CaseVerdict(
        case_id=case_id,
        layer="V0",
        passed=not failures,
        expected=expected,
        actual={
            "structural_valid": first.structural_valid,
            "finding_count": len(first.findings),
            "rule_codes": actual_codes,
            "finding_keys": [finding.finding_key for finding in first.findings],
            "identity_stable": identity_stable,
        },
        failures=tuple(failures),
    )


def _run_v0_normalization_case(case: Mapping[str, Any]) -> CaseVerdict:
    case_id = str(case["case_id"])
    expected = case.get("expected") or {}
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case_id}: expected must be an object")
    engine = VerificationEngine(profile="balanced")
    error: str | None = None
    normalized: list[dict[str, Any]] = []
    try:
        normalized = [
            finding.as_dict()
            for finding in engine.normalize_llm_findings(case.get("findings", ()))
        ]
    except ValueError as exc:
        error = str(exc)

    failures: list[str] = []
    expected_error_prefix = expected.get("error_prefix")
    if expected_error_prefix is not None:
        if error is None or not error.startswith(str(expected_error_prefix)):
            failures.append("expected_error")
    elif error is not None:
        failures.append("unexpected_error")

    if error is None:
        if "count" in expected and len(normalized) != int(expected["count"]):
            failures.append("normalized_count")
        if normalized:
            first = normalized[0]
            if "severity" in expected and first.get("severity") != expected["severity"]:
                failures.append("severity_mapping")
            if "ref_keys" in expected:
                actual_ref_keys = sorted(
                    str(ref.get("ref_key")) for ref in first.get("refs", ())
                )
                if actual_ref_keys != sorted(str(value) for value in expected["ref_keys"]):
                    failures.append("reference_preservation")

    return CaseVerdict(
        case_id=case_id,
        layer="V0",
        passed=not failures,
        expected=expected,
        actual={"error": error, "normalized": normalized},
        failures=tuple(failures),
    )


def run_v0(repo_root: Path, suite_path: Path) -> dict[str, Any]:
    suite = _load_json(suite_path)
    base_fixture = str(suite["base_fixture"])
    document_rows = [
        _run_v0_document_case(repo_root, base_fixture, case)
        for case in suite.get("document_cases", ())
    ]
    normalization_rows = [
        _run_v0_normalization_case(case) for case in suite.get("normalization_cases", ())
    ]
    rows = [*document_rows, *normalization_rows]

    required_total = 0
    required_hits = 0
    clean_total = 0
    clean_false_positives = 0
    identity_total = 0
    identity_hits = 0
    for case, verdict in zip(suite.get("document_cases", ()), document_rows, strict=True):
        expected = case.get("expected") or {}
        required = {str(value) for value in expected.get("required_rule_codes", ())}
        actual = set(verdict.actual.get("rule_codes", ()))
        required_total += len(required)
        required_hits += len(required & actual)
        if expected.get("clean") is True:
            clean_total += 1
            if verdict.actual.get("finding_count", 0):
                clean_false_positives += 1
        identity_total += 1
        identity_hits += int(bool(verdict.actual.get("identity_stable")))

    metrics = {
        "case_pass_rate": _ratio(sum(row.passed for row in rows), len(rows)),
        "required_rule_recall": _ratio(required_hits, required_total),
        "clean_false_positive_rate": _ratio(clean_false_positives, clean_total)
        if clean_total
        else 0.0,
        "finding_identity_stability": _ratio(identity_hits, identity_total),
    }
    gates = {
        "case_pass_rate": metrics["case_pass_rate"] == 1.0,
        "required_rule_recall": metrics["required_rule_recall"] == 1.0,
        "clean_false_positive_rate": metrics["clean_false_positive_rate"] == 0.0,
        "finding_identity_stability": metrics["finding_identity_stability"] == 1.0,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "metrics": metrics,
        "gates": gates,
        "rows": [row.as_dict() for row in rows],
    }


def _operation_from_payload(payload: Mapping[str, Any]) -> PatchOperation:
    kwargs: dict[str, Any] = {
        "operation_id": str(payload.get("operation_id") or ""),
        "object_id": str(payload.get("object_id") or ""),
        "field_path": str(payload.get("field_path") or ""),
        "new_value": deepcopy(payload.get("new_value")),
        "object_type": payload.get("object_type"),
        "operation_type": str(payload.get("operation_type") or "replace"),
        "expected_object_revision": payload.get("expected_object_revision"),
    }
    if "old_value" in payload:
        kwargs["old_value"] = deepcopy(payload["old_value"])
    return PatchOperation(**kwargs)


def _operations_for_case(case: Mapping[str, Any]) -> list[PatchOperation]:
    if "repeat_operation" in case:
        repeat = case["repeat_operation"]
        if not isinstance(repeat, Mapping):
            raise ValueError("repeat_operation must be an object")
        count = int(repeat["count"])
        template = repeat["operation"]
        if not isinstance(template, Mapping):
            raise ValueError("repeat_operation.operation must be an object")
        result: list[PatchOperation] = []
        for index in range(count):
            item = dict(template)
            item["operation_id"] = f"{item.get('operation_id', 'op')}_{index + 1}"
            result.append(_operation_from_payload(item))
        return result
    raw = case.get("operations")
    if raw is None:
        raw = [case["operation"]]
    return [_operation_from_payload(item) for item in raw]


def _run_v1_case(
    repo_root: Path,
    base_fixture: str,
    case: Mapping[str, Any],
) -> CaseVerdict:
    case_id = str(case["case_id"])
    document = apply_mutations(
        _fixture_document(repo_root, str(case.get("base_fixture") or base_fixture)),
        case.get("document_mutations", ()),
    )
    original = deepcopy(document)
    expected = case.get("expected") or {}
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case_id}: expected must be an object")

    engine = VerificationEngine(profile="fast", editable_fields_by_type=EDITABLE_FIELDS)
    baseline = engine.verify(document)
    target_rule_codes = {str(value) for value in case.get("target_rule_codes", ())}
    target_keys = tuple(
        finding.finding_key
        for finding in baseline.findings
        if finding.rule_code in target_rule_codes
    )
    simulation = engine.simulate_patch_operation_batch(
        document,
        _operations_for_case(case),
        object_revisions=case.get("object_revisions"),
        target_finding_keys=target_keys,
    )
    baseline_by_key = {finding.finding_key: finding.rule_code for finding in baseline.findings}
    fixed_rule_codes = sorted(
        baseline_by_key[key] for key in simulation.fixed_finding_keys if key in baseline_by_key
    )
    unchanged_input = document == original
    actual = {
        "valid": simulation.valid,
        "can_apply": simulation.can_apply,
        "reason_code": simulation.reason_code,
        "fixed_rule_codes": fixed_rule_codes,
        "residual_finding_keys": list(simulation.residual_finding_keys),
        "new_finding_keys": list(simulation.new_finding_keys),
        "structure_lock_conflicts": list(simulation.structure_lock_conflicts),
        "input_unchanged": unchanged_input,
    }

    failures: list[str] = []
    for field in ("valid", "can_apply", "reason_code"):
        if field in expected and actual[field] != expected[field]:
            failures.append(field)
    if "fixed_rule_codes" in expected and fixed_rule_codes != sorted(
        str(value) for value in expected["fixed_rule_codes"]
    ):
        failures.append("fixed_rule_codes")
    if "structure_lock_conflicts" in expected and actual["structure_lock_conflicts"] != sorted(
        str(value) for value in expected["structure_lock_conflicts"]
    ):
        failures.append("structure_lock_conflicts")
    if not unchanged_input:
        failures.append("input_mutated")

    return CaseVerdict(
        case_id=case_id,
        layer="V1",
        passed=not failures,
        expected=expected,
        actual=actual,
        failures=tuple(failures),
    )


def _chat_gate_request(document: dict[str, Any]) -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v15",
        casefile=document,
        history=(),
        message="validator benchmark",
        editable_fields_by_collection=editable_fields_by_collection(),
        input_hash="0" * 64,
        model_id="validator-benchmark",
        api_key=None,
        max_turns=1,
        emit=lambda _event_type, _stage, _payload: None,
        validation_issues=(),
        validation={"issues": []},
        focus={},
    )


def _run_v1_chat_gate_case(
    repo_root: Path,
    base_fixture: str,
    case: Mapping[str, Any],
) -> CaseVerdict:
    case_id = str(case["case_id"])
    document = apply_mutations(
        _fixture_document(repo_root, str(case.get("base_fixture") or base_fixture)),
        case.get("document_mutations", ()),
    )
    original = deepcopy(document)
    expected = case.get("expected") or {}
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case_id}: expected must be an object")
    proposals = [dict(item) for item in case.get("proposals", ())]
    gate = server_gate_audit_suggestions(_chat_gate_request(document), proposals)
    actual = {
        "safe_count": len(gate.registry.candidates),
        "failure_reason_codes": [failure.reason_code for failure in gate.failures],
        "discard_reason_codes": [discard.reason_code for discard in gate.discards],
        "candidate_targets": [
            f"{candidate.object_id}:{candidate.path}" for candidate in gate.registry.candidates
        ],
        "candidate_value_json": [candidate.value_json for candidate in gate.registry.candidates],
        "candidate_finding_refs": [
            candidate.finding_ref for candidate in gate.registry.candidates
        ],
        "input_unchanged": document == original,
    }
    failures: list[str] = []
    for field in (
        "safe_count",
        "failure_reason_codes",
        "discard_reason_codes",
        "candidate_targets",
        "candidate_value_json",
        "candidate_finding_refs",
    ):
        if field in expected and actual[field] != expected[field]:
            failures.append(field)
    if not actual["input_unchanged"]:
        failures.append("input_mutated")
    return CaseVerdict(
        case_id=case_id,
        layer="V1",
        passed=not failures,
        expected=expected,
        actual=actual,
        failures=tuple(failures),
    )


def run_v1(repo_root: Path, suite_path: Path) -> dict[str, Any]:
    suite = _load_json(suite_path)
    base_fixture = str(suite["base_fixture"])
    batch_cases = list(suite.get("batch_cases", suite.get("cases", ())))
    chat_gate_cases = list(suite.get("chat_gate_cases", ()))
    batch_rows = [
        _run_v1_case(repo_root, base_fixture, case) for case in batch_cases
    ]
    chat_rows = [
        _run_v1_chat_gate_case(repo_root, base_fixture, case)
        for case in chat_gate_cases
    ]
    rows = [*batch_rows, *chat_rows]

    safe_total = safe_rejects = unsafe_total = unsafe_accepts = 0
    reason_total = reason_hits = 0
    for case, row in zip(batch_cases, batch_rows, strict=True):
        expected = case.get("expected") or {}
        expected_apply = expected.get("can_apply")
        if expected_apply is True:
            safe_total += 1
            safe_rejects += int(row.actual.get("can_apply") is not True)
        elif expected_apply is False:
            unsafe_total += 1
            unsafe_accepts += int(row.actual.get("can_apply") is True)
        if "reason_code" in expected:
            reason_total += 1
            reason_hits += int(row.actual.get("reason_code") == expected.get("reason_code"))

    chat_safe_total = chat_safe_rejects = 0
    chat_unsafe_total = chat_unsafe_accepts = 0
    chat_reason_total = chat_reason_hits = 0
    for case, row in zip(chat_gate_cases, chat_rows, strict=True):
        expected = case.get("expected") or {}
        expected_safe_count = int(expected.get("safe_count", 0))
        expects_failures = bool(expected.get("failure_reason_codes"))
        if expected_safe_count > 0 and not expects_failures:
            chat_safe_total += 1
            chat_safe_rejects += int(int(row.actual.get("safe_count", 0)) == 0)
        if expects_failures:
            chat_unsafe_total += 1
            chat_unsafe_accepts += int(int(row.actual.get("safe_count", 0)) > 0)
        if "failure_reason_codes" in expected:
            chat_reason_total += 1
            chat_reason_hits += int(
                row.actual.get("failure_reason_codes") == expected.get("failure_reason_codes")
            )

    immutability_hits = sum(bool(row.actual.get("input_unchanged")) for row in rows)
    metrics = {
        "case_pass_rate": _ratio(sum(row.passed for row in rows), len(rows)),
        "batch_safe_false_reject_rate": (
            _ratio(safe_rejects, safe_total) if safe_total else 0.0
        ),
        "batch_unsafe_false_accept_rate": (
            _ratio(unsafe_accepts, unsafe_total) if unsafe_total else 0.0
        ),
        "batch_reason_code_accuracy": _ratio(reason_hits, reason_total),
        "chat_gate_safe_false_reject_rate": (
            _ratio(chat_safe_rejects, chat_safe_total) if chat_safe_total else 0.0
        ),
        "chat_gate_unsafe_false_accept_rate": (
            _ratio(chat_unsafe_accepts, chat_unsafe_total) if chat_unsafe_total else 0.0
        ),
        "chat_gate_reason_code_accuracy": _ratio(chat_reason_hits, chat_reason_total),
        "input_immutability_rate": _ratio(immutability_hits, len(rows)),
    }
    gates = {
        "case_pass_rate": metrics["case_pass_rate"] == 1.0,
        "batch_safe_false_reject_rate": metrics["batch_safe_false_reject_rate"] == 0.0,
        "batch_unsafe_false_accept_rate": metrics["batch_unsafe_false_accept_rate"] == 0.0,
        "batch_reason_code_accuracy": metrics["batch_reason_code_accuracy"] == 1.0,
        "chat_gate_safe_false_reject_rate": (
            metrics["chat_gate_safe_false_reject_rate"] == 0.0
        ),
        "chat_gate_unsafe_false_accept_rate": (
            metrics["chat_gate_unsafe_false_accept_rate"] == 0.0
        ),
        "chat_gate_reason_code_accuracy": metrics["chat_gate_reason_code_accuracy"] == 1.0,
        "input_immutability_rate": metrics["input_immutability_rate"] == 1.0,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "metrics": metrics,
        "gates": gates,
        "rows": [row.as_dict() for row in rows],
    }


def _validation_issue(payload: Mapping[str, Any]) -> ValidationIssue:
    return ValidationIssue(
        code=str(payload["code"]),
        stage=str(payload["stage"]),  # type: ignore[arg-type]
        path=str(payload["path"]),
        message=str(payload.get("message") or payload["code"]),
        repairable=bool(payload.get("repairable", True)),
        details=dict(payload.get("details") or {}),
    )


def _repair_targets(plan: Mapping[str, Any]) -> set[str]:
    targets = {str(value) for key in ("add", "remove") for value in plan.get(key, ())}
    for replacement in plan.get("replace", ()):
        if not isinstance(replacement, Mapping):
            continue
        object_id = replacement.get("object_id")
        path = replacement.get("path")
        if isinstance(object_id, str) and isinstance(path, str):
            targets.add(f"{object_id}:{path}")
    return targets


def _run_v2_case(case: Mapping[str, Any]) -> CaseVerdict:
    case_id = str(case["case_id"])
    expected: Any = case.get("expected_plan") or {}
    if case.get("kind") in {"target_resolution", "state_transition"}:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case_id}: expected_plan must be an object")
    issues = tuple(_validation_issue(item) for item in case.get("issues", ()))
    plan = plan_repairs(issues)
    if case.get("kind") == "target_resolution":
        actual: Any = {
            "target": resolve_authoritative_repair_target(
                bundle=case.get("bundle", {}),
                findings=tuple(case.get("findings", ())),
                issues=issues,
                repair_plan=plan,
            )
        }
        expected = {"target": case.get("expected_target")}
    elif case.get("kind") == "state_transition":
        actual = {
            "mode": select_semantic_repair_mode(
                attempt=int(case.get("attempt", 1)),
                repair_plan=plan,
                has_authoritative_target=bool(case.get("has_authoritative_target")),
                currently_target_locked=bool(case.get("currently_target_locked")),
                no_progress=bool(case.get("no_progress")),
            )
        }
        expected = case.get("expected_state", {})
    else:
        actual = plan.as_dict()
    failures = () if actual == expected else ("repair_contract_exact_match",)
    return CaseVerdict(
        case_id=case_id,
        layer="V2",
        passed=not failures,
        expected=expected,
        actual=actual,
        failures=failures,
    )


def run_v2(suite_path: Path) -> dict[str, Any]:
    suite = _load_json(suite_path)
    cases = list(suite.get("cases", ()))
    rows = [_run_v2_case(case) for case in cases]

    expected_targets: set[str] = set()
    actual_targets: set[str] = set()
    for row in rows:
        expected_targets.update(_repair_targets(row.expected))
        actual_targets.update(_repair_targets(row.actual))
    target_hits = len(expected_targets & actual_targets)
    target_precision = _ratio(target_hits, len(actual_targets))
    target_recall = _ratio(target_hits, len(expected_targets))

    metrics = {
        "case_pass_rate": _ratio(sum(row.passed for row in rows), len(rows)),
        "repair_target_precision": target_precision,
        "repair_target_recall": target_recall,
    }
    gates = {
        "case_pass_rate": metrics["case_pass_rate"] == 1.0,
        "repair_target_precision": target_precision == 1.0,
        "repair_target_recall": target_recall == 1.0,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "metrics": metrics,
        "gates": gates,
        "rows": [row.as_dict() for row in rows],
    }


def run_validator_benchmark(
    *,
    repo_root: Path | None = None,
    suite_dir: Path | None = None,
    layer: str = "all",
) -> dict[str, Any]:
    repo_root = (repo_root or _repo_root_from_module()).resolve()
    suite_dir = suite_dir or (repo_root / DEFAULT_SUITE_RELATIVE)
    if not suite_dir.is_absolute():
        suite_dir = (repo_root / suite_dir).resolve()

    selected = {"v0", "v1", "v2"} if layer == "all" else {layer}
    layers: dict[str, Any] = {}
    if "v0" in selected:
        layers["V0"] = run_v0(repo_root, suite_dir / "v0-rules.json")
    if "v1" in selected:
        layers["V1"] = run_v1(repo_root, suite_dir / "v1-patch-gates.json")
    if "v2" in selected:
        layers["V2"] = run_v2(suite_dir / "v2-repair-contracts.json")

    status = "passed" if all(item["status"] == "passed" for item in layers.values()) else "failed"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "engine_version": VerificationEngine.VERSION,
        "suite_dir": str(suite_dir),
        "layers": layers,
    }


def _case_ids(suite_dir: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for layer, filename, keys in (
        ("V0", "v0-rules.json", ("document_cases", "normalization_cases")),
        ("V1", "v1-patch-gates.json", ("batch_cases", "chat_gate_cases")),
        ("V2", "v2-repair-contracts.json", ("cases",)),
    ):
        payload = _load_json(suite_dir / filename)
        result[layer] = [
            str(case["case_id"])
            for key in keys
            for case in payload.get(key, ())
        ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic CaseFile Validator V0/V1/V2 release benchmarks"
    )
    parser.add_argument("--layer", choices=("all", "v0", "v1", "v2"), default="all")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--suite-dir", type=Path)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()

    repo_root = (args.repo_root or _repo_root_from_module()).resolve()
    suite_dir = args.suite_dir or (repo_root / DEFAULT_SUITE_RELATIVE)
    if not suite_dir.is_absolute():
        suite_dir = (repo_root / suite_dir).resolve()
    if args.list_cases:
        print(json.dumps(_case_ids(suite_dir), ensure_ascii=False, indent=2))
        return

    report = run_validator_benchmark(
        repo_root=repo_root,
        suite_dir=suite_dir,
        layer=args.layer,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
