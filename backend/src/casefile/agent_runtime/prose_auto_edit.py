"""Bounded Writer/Judge/Rewrite-or-Polish/Judge orchestration for product prose."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import PROSE_COUNCIL_MAX_OUTPUT_TOKENS
from casefile.agent_runtime.prose_polisher import (
    ProsePolisherExecution,
    ProsePolisherProvider,
    execute_prose_polisher,
)
from casefile.agent_runtime.prose_rewriter import (
    ProseRewriterExecution,
    ProseRewriterInfrastructureError,
    ProseRewriterProvider,
    ProseRewriterProviderResult,
    ProseRewriterRequest,
    execute_prose_rewriter,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    finalize_scene_render,
    validate_prose_judge_checklist,
    validate_scene_render,
)
from casefile_contracts import ProseRevisionDecisionCandidate

AUTO_EDIT_PROTOCOL_V1 = "prose-auto-edit-json-object-v1"
AUTO_EDIT_PROTOCOL = "prose-auto-edit-json-object-v2"
AUTO_EDIT_JUDGE_PROMPT_VERSION = "prose-auto-edit-judge-v1"
AUTO_EDIT_REWRITER_PROMPT_VERSION = "prose-rewriter-v10"
AUTO_EDIT_POLISHER_PROMPT_VERSION = "prose-polisher-v6"


def auto_edit_protocol_for_runtime(runtime_version: str) -> str:
    """Keep recovery on the protocol frozen by the task's prose runtime."""

    if runtime_version == "prose-shadow-runtime-v13":
        return AUTO_EDIT_PROTOCOL_V1
    if runtime_version in {"prose-shadow-runtime-v14", "prose-shadow-runtime-v15"}:
        return AUTO_EDIT_PROTOCOL
    raise CompilerContractError("compiler_prose_runtime_version_unsupported")


class AutoEditDecisionProvider(Protocol):
    def auto_edit_decide(self, request: ProseRewriterRequest) -> ProseRewriterProviderResult: ...


@dataclass(frozen=True, slots=True)
class AutoEditDecisionExecution:
    status: Literal["completed", "protocol_failed", "inconclusive"]
    report: dict[str, Any] | None
    call: ProseRewriterProviderResult | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AutoEditExecution:
    status: Literal["completed", "completed_with_issues", "review_incomplete", "inconclusive"]
    accepted_render: dict[str, Any] | None
    review: AutoEditDecisionExecution
    modification: ProseRewriterExecution | ProsePolisherExecution | None
    selection: AutoEditDecisionExecution | None
    unresolved_issues: tuple[str, ...]
    error_code: str | None = None


def execute_auto_edit(
    decision_provider: AutoEditDecisionProvider,
    rewriter_provider: ProseRewriterProvider,
    polisher_provider: ProsePolisherProvider,
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    writer_render: dict[str, Any],
    model_id: str,
    api_key: str,
    observe: Callable[[str, Any], None] | None = None,
    previous_edit_issues: list[str] | None = None,
    protocol_version: str = AUTO_EDIT_PROTOCOL,
    rewrite_prompt_version: str = AUTO_EDIT_REWRITER_PROMPT_VERSION,
    plan_context_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> AutoEditExecution:
    """Run one model-owned edit choice and at most one model-owned candidate selection."""

    try:
        checklist_json = validate_prose_judge_checklist(
            checklist,
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            previous_scene_render=previous_scene_render,
        ).model_dump(mode="json")
        original = validate_scene_render(
            writer_render,
            checklist=checklist_json,
            profile=profile,
            enforce_target_length=False,
        ).model_dump(mode="json")
    except CompilerContractError as error:
        failed = AutoEditDecisionExecution("protocol_failed", None, None, str(error))
        return AutoEditExecution("inconclusive", None, failed, None, None, (), str(error))

    review = execute_auto_edit_decision(
        decision_provider,
        stage="review",
        previous_edit_issues=previous_edit_issues,
        checklist=checklist_json,
        profile=profile,
        original_render=original,
        modified_render=None,
        model_id=model_id,
        api_key=api_key,
        protocol_version=protocol_version,
    )
    if observe:
        observe("auto_edit_review", review)
    if review.status != "completed" or review.report is None:
        return _fallback(original, checklist_json, profile, review, None, None, review.error_code)

    action = review.report["action"]
    if action == "retain":
        accepted = _finalize(
            original,
            original,
            checklist_json,
            profile,
            "auto_edit_original",
            review.report,
        )
        issues = tuple(review.report.get("unresolved_issues") or ())
        return AutoEditExecution(
            "completed_with_issues" if issues else "completed",
            accepted,
            review,
            None,
            None,
            issues,
        )

    modification: ProseRewriterExecution | ProsePolisherExecution
    if action in {"local_revision", "full_rewrite"}:
        modification = execute_prose_rewriter(
            rewriter_provider,
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            checklist=checklist_json,
            previous_scene_render=previous_scene_render,
            current_render=original,
            consensus={},
            judge_reports=(),
            model_id=model_id,
            api_key=api_key,
            remaining_scene_call_budget=4,
            auto_edit_review=review.report,
            prompt_version=rewrite_prompt_version,
            plan_context=plan_context_provider() if plan_context_provider else None,
            soft_target_length=True,
        )
        if observe:
            observe("rewrite", modification)
    elif action == "polish":
        modification = execute_prose_polisher(
            polisher_provider,
            profile=profile,
            checklist=checklist_json,
            current_render=original,
            semantic_consensus={},
            quality_findings={},
            model_id=model_id,
            api_key=api_key,
            auto_edit_review=review.report,
            prompt_version=AUTO_EDIT_POLISHER_PROMPT_VERSION,
            soft_target_length=True,
        )
        if observe:
            observe("polish", modification)
    else:
        return _fallback(
            original,
            checklist_json,
            profile,
            review,
            None,
            None,
            "prose_auto_edit_review_action_invalid",
        )

    if modification.status != "completed" or modification.render is None:
        if modification.error_code and any(
            token in modification.error_code
            for token in ("binding", "fingerprint", "hash_mismatch")
        ):
            raise CompilerContractError(modification.error_code)
        return _fallback(
            original,
            checklist_json,
            profile,
            review,
            modification,
            None,
            modification.error_code,
        )

    selection = execute_auto_edit_decision(
        decision_provider,
        stage="selection",
        previous_edit_issues=previous_edit_issues,
        checklist=checklist_json,
        profile=profile,
        original_render=original,
        modified_render=modification.render,
        model_id=model_id,
        api_key=api_key,
        protocol_version=protocol_version,
    )
    if observe:
        observe("auto_edit_selection", selection)
    if selection.status != "completed" or selection.report is None:
        return _fallback(
            original,
            checklist_json,
            profile,
            review,
            modification,
            selection,
            selection.error_code,
        )

    mapping = selection.report["candidate_mapping"]
    selected_key = (
        "candidate_a" if selection.report["action"] == "select_candidate_a" else "candidate_b"
    )
    selected_kind = mapping[selected_key]
    selected = original if selected_kind == "original" else modification.render
    reason = "auto_edit_original" if selected_kind == "original" else "auto_edit_modified"
    accepted = _finalize(selected, original, checklist_json, profile, reason, selection.report)
    issues = tuple(selection.report.get("unresolved_issues") or ())
    return AutoEditExecution(
        "completed_with_issues" if issues else "completed",
        accepted,
        review,
        modification,
        selection,
        issues,
    )


def execute_auto_edit_decision(
    provider: AutoEditDecisionProvider,
    *,
    stage: Literal["review", "selection"],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    original_render: dict[str, Any],
    modified_render: dict[str, Any] | None,
    model_id: str,
    api_key: str,
    previous_edit_issues: list[str] | None = None,
    protocol_version: str = AUTO_EDIT_PROTOCOL,
) -> AutoEditDecisionExecution:
    request, mapping = _decision_request(
        stage=stage,
        checklist=checklist,
        profile=profile,
        original_render=original_render,
        modified_render=modified_render,
        model_id=model_id,
        api_key=api_key,
        previous_edit_issues=previous_edit_issues,
        protocol_version=protocol_version,
    )
    for attempt in range(2):
        try:
            call = provider.auto_edit_decide(request)
        except ProseRewriterInfrastructureError as error:
            # Never retry a timeout with an unknown remote outcome.
            if attempt or "Timeout" in str(error):
                return AutoEditDecisionExecution("inconclusive", None, None, str(error))
            continue
        if (
            call.request_fingerprint != request.request_fingerprint
            or call.prompt_hash != request.prompt_hash
            or call.input_hash != request.input_hash
            or call.component_input_hash != request.component_input_hash
            or call.model_id != request.model_id
            or call.prompt_version != request.prompt_version
            or canonical_json_sha256(call.request_payload) != request.input_hash
        ):
            raise CompilerContractError("prose_auto_edit_binding_invalid")
        validation_errors: list[dict[str, Any]] = []
        try:
            candidate = ProseRevisionDecisionCandidate.model_validate(call.candidate).model_dump(
                mode="json", exclude_none=True
            )
            _validate_candidate(candidate, stage, checklist)
            break
        except (ValidationError, ValueError, TypeError) as error:
            validation_errors = _candidate_validation_errors(
                call.candidate, stage, checklist, error
            )
            if attempt:
                return AutoEditDecisionExecution(
                    "protocol_failed", None, call, "prose_auto_edit_decision_invalid"
                )
            record = getattr(provider, "record_protocol_failure", None)
            if record:
                record_details: dict[str, Any] = {"code": "prose_auto_edit_decision_invalid"}
                if protocol_version == AUTO_EDIT_PROTOCOL:
                    record_details["validation_errors"] = validation_errors
                record(call.request_fingerprint, record_details)
            repair = (
                {
                    "attempt": 1,
                    "failed_response": call.candidate,
                    "instruction": "严格按本阶段 Schema 和全部 check_id 返回，不能增加字段。",
                }
                if protocol_version == AUTO_EDIT_PROTOCOL_V1
                else {
                    "attempt": 1,
                    "failed_response": call.candidate,
                    "validation_errors": validation_errors,
                    "expected_check_ids": [item["check_id"] for item in checklist["checks"]],
                    "instruction": (
                        "只修复 validation_errors：unresolved_issues 必须是数组；findings 必须"
                        "逐项且仅覆盖 expected_check_ids，每个恰好一次。严格按本阶段 Schema 返回。"
                    ),
                }
            )
            payload = {
                **request.input_payload,
                "protocol_repair": repair,
            }
            digest = canonical_json_sha256(payload)
            request = replace(
                request,
                input_payload=payload,
                input_hash=digest,
                request_fingerprint=canonical_json_sha256(
                    {"source": request.request_fingerprint, "repair": digest}
                ),
            )
    report = {
        "schema_id": "compiler.prose-revision-decision.v1",
        "scene_id": original_render["scene_id"],
        "render_hash": canonical_json_sha256(original_render),
        "input_hash": request.input_hash,
        "repair_budget_exhausted": stage == "selection",
        **candidate,
    }
    if mapping is not None:
        report["candidate_mapping"] = mapping
    return AutoEditDecisionExecution("completed", report, call)


def _decision_request(
    *,
    stage: Literal["review", "selection"],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    original_render: dict[str, Any],
    modified_render: dict[str, Any] | None,
    model_id: str,
    api_key: str,
    previous_edit_issues: list[str] | None = None,
    protocol_version: str = AUTO_EDIT_PROTOCOL,
) -> tuple[ProseRewriterRequest, dict[str, str] | None]:
    if protocol_version not in {AUTO_EDIT_PROTOCOL_V1, AUTO_EDIT_PROTOCOL}:
        raise ValueError("prose_auto_edit_protocol_unsupported")
    prompt = load_prompt("prose_auto_edit_judge", AUTO_EDIT_JUDGE_PROMPT_VERSION)
    mapping: dict[str, str] | None = None
    candidates: dict[str, Any] | None = None
    if stage == "selection":
        if modified_render is None:
            raise ValueError("prose_auto_edit_modified_render_required")
        seed = canonical_json_sha256(
            {
                "checklist": canonical_json_sha256(checklist),
                "original": canonical_json_sha256(original_render),
                "modified": canonical_json_sha256(modified_render),
            }
        )
        original_first = int(seed[0], 16) % 2 == 0
        mapping = {
            "candidate_a": "original" if original_first else "modified",
            "candidate_b": "modified" if original_first else "original",
        }
        candidates = {
            key: {
                "blocks": [
                    {"text": block["text"]}
                    for block in (original_render if value == "original" else modified_render)[
                        "blocks"
                    ]
                ]
            }
            for key, value in mapping.items()
        }
    response_schema = ProseRevisionDecisionCandidate.model_json_schema()
    action_schema = response_schema["properties"]["action"]
    action_schema.clear()
    action_schema["enum"] = (
        ["retain", "full_rewrite", "polish"]
        if stage == "review"
        else ["select_candidate_a", "select_candidate_b"]
    )
    response_schema["required"] = list(
        dict.fromkeys([*response_schema["required"], "decision_stage", "unresolved_issues"])
    )
    if protocol_version == AUTO_EDIT_PROTOCOL:
        expected_check_ids = [item["check_id"] for item in checklist["checks"]]
        response_schema["properties"]["decision_stage"] = {
            "type": "string",
            "enum": [stage],
        }
        unresolved_schema = response_schema["properties"]["unresolved_issues"]["anyOf"][0]
        response_schema["properties"]["unresolved_issues"] = unresolved_schema
        findings_schema = response_schema["properties"]["findings"]
        findings_schema["minItems"] = len(expected_check_ids)
        findings_schema["maxItems"] = len(expected_check_ids)
        response_schema["$defs"]["ProseRevisionFinding"]["properties"]["check_id"] = {
            "type": "string",
            "enum": expected_check_ids,
        }
    payload = {
        "server_bindings": {
            "protocol": protocol_version,
            "stage": stage,
            "scene_id": original_render["scene_id"],
            "checklist_hash": canonical_json_sha256(checklist),
            "original_render_hash": canonical_json_sha256(original_render),
            "modified_render_hash": (
                canonical_json_sha256(modified_render) if modified_render is not None else None
            ),
        },
        "untrusted_data": {
            "profile": profile,
            "checklist": checklist,
            "previous_edit_issues": previous_edit_issues or [],
            **(
                {"current_render": original_render}
                if stage == "review"
                else {"candidates": candidates}
            ),
        },
        "auto_edit_stage": stage,
        "output_schema_id": "compiler.prose-revision-decision.v1",
        "response_schema": response_schema,
    }
    input_hash = canonical_json_sha256(payload)
    component_input_hash = canonical_json_sha256(payload["server_bindings"])
    fingerprint = canonical_json_sha256(
        {
            "protocol": protocol_version,
            "stage": stage,
            "model": model_id,
            "prompt": prompt.system_prompt_sha256,
            "input": input_hash,
            "max_output_tokens": PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
        }
    )
    return (
        ProseRewriterRequest(
            model_id=model_id,
            api_key=api_key,
            system_prompt=prompt.system_prompt,
            prompt_version=prompt.version,
            prompt_hash=prompt.system_prompt_sha256,
            input_payload=payload,
            input_hash=input_hash,
            component_input_hash=component_input_hash,
            request_fingerprint=fingerprint,
            rewrite_round=1 if stage == "review" else 2,
            remaining_scene_call_budget=8 if stage == "review" else 2,
            max_output_tokens=PROSE_COUNCIL_MAX_OUTPUT_TOKENS,
        ),
        mapping,
    )


def _validate_candidate(
    candidate: dict[str, Any], stage: Literal["review", "selection"], checklist: dict[str, Any]
) -> None:
    if candidate.get("decision_stage") != stage:
        raise ValueError("stage")
    if "unresolved_issues" not in candidate:
        raise ValueError("unresolved_issues")
    expected = [item["check_id"] for item in checklist["checks"]]
    actual = [item["check_id"] for item in candidate["findings"]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("findings")
    action = candidate["action"]
    if stage == "review":
        if action not in {"retain", "local_revision", "full_rewrite", "polish"}:
            raise ValueError("action")
        if action != "retain" and not candidate["revision_plan"].strip():
            raise ValueError("plan")
    else:
        if action not in {"select_candidate_a", "select_candidate_b"}:
            raise ValueError("action")


def _candidate_validation_errors(
    raw: Any,
    stage: Literal["review", "selection"],
    checklist: dict[str, Any],
    error: Exception,
) -> list[dict[str, Any]]:
    """Return bounded, deterministic feedback for the single paid protocol repair."""

    if not isinstance(raw, dict):
        return [{"code": "response_must_be_object"}]
    issues: list[dict[str, Any]] = []
    if raw.get("decision_stage") != stage:
        issues.append(
            {
                "code": "decision_stage_mismatch",
                "expected": stage,
                "actual": raw.get("decision_stage"),
            }
        )
    if not isinstance(raw.get("unresolved_issues"), list):
        issues.append(
            {
                "code": "unresolved_issues_must_be_array",
                "actual_type": type(raw.get("unresolved_issues")).__name__,
            }
        )

    expected = [item["check_id"] for item in checklist["checks"]]
    findings = raw.get("findings")
    if not isinstance(findings, list):
        issues.append({"code": "findings_must_be_array"})
    else:
        actual: list[str] = []
        malformed: list[int] = []
        for index, item in enumerate(findings):
            check_id = item.get("check_id") if isinstance(item, dict) else None
            if not isinstance(check_id, str):
                malformed.append(index)
            else:
                actual.append(check_id)
        if malformed:
            issues.append(
                {
                    "code": "finding_check_id_must_be_string",
                    "indexes": malformed,
                }
            )
        unexpected = sorted({check_id for check_id in actual if check_id not in set(expected)})
        missing = [check_id for check_id in expected if check_id not in actual]
        duplicates = sorted({str(check_id) for check_id in actual if actual.count(check_id) > 1})
        if unexpected:
            issues.append({"code": "unexpected_check_ids", "check_ids": unexpected})
        if missing:
            issues.append({"code": "missing_check_ids", "check_ids": missing})
        if duplicates:
            issues.append({"code": "duplicate_check_ids", "check_ids": duplicates})
        if len(findings) != len(expected):
            issues.append(
                {
                    "code": "findings_count_mismatch",
                    "expected": len(expected),
                    "actual": len(findings),
                }
            )

    allowed_actions = (
        {"retain", "full_rewrite", "polish"}
        if stage == "review"
        else {"select_candidate_a", "select_candidate_b"}
    )
    if raw.get("action") not in allowed_actions:
        issues.append(
            {
                "code": "action_invalid_for_stage",
                "allowed": sorted(allowed_actions),
                "actual": raw.get("action"),
            }
        )
    if (
        stage == "review"
        and raw.get("action") != "retain"
        and not str(raw.get("revision_plan") or "").strip()
    ):
        issues.append({"code": "revision_plan_required"})

    if isinstance(error, ValidationError):
        for item in error.errors(include_url=False, include_input=False)[:8]:
            issues.append(
                {
                    "code": "schema_validation_failed",
                    "field": ".".join(str(part) for part in item["loc"]),
                    "error_type": item["type"],
                }
            )
    return issues or [{"code": "candidate_validation_failed", "detail": str(error)[:120]}]


def _finalize(
    selected: dict[str, Any],
    original: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    reason: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    return finalize_scene_render(
        selected,
        original_render=original,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_json_sha256(report),
        selection_reason=reason,
        enforce_target_length=False,
    ).model_dump(mode="json")


def _fallback(
    original: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    review: AutoEditDecisionExecution,
    modification: ProseRewriterExecution | ProsePolisherExecution | None,
    selection: AutoEditDecisionExecution | None,
    error_code: str | None,
) -> AutoEditExecution:
    binding = {
        "review": review.error_code or (review.report and canonical_json_sha256(review.report)),
        "modification": modification.error_code if modification else None,
        "selection": selection.error_code if selection else None,
        "fallback": "writer",
    }
    accepted = finalize_scene_render(
        original,
        original_render=original,
        checklist=checklist,
        profile=profile,
        component_input_hash=canonical_json_sha256(binding),
        selection_reason="auto_edit_unreviewed",
        enforce_target_length=False,
    ).model_dump(mode="json")
    return AutoEditExecution(
        "review_incomplete",
        accepted,
        review,
        modification,
        selection,
        (),
        error_code,
    )


__all__ = [
    "AUTO_EDIT_JUDGE_PROMPT_VERSION",
    "AUTO_EDIT_POLISHER_PROMPT_VERSION",
    "AUTO_EDIT_PROTOCOL",
    "AUTO_EDIT_PROTOCOL_V1",
    "AUTO_EDIT_REWRITER_PROMPT_VERSION",
    "AutoEditDecisionExecution",
    "AutoEditExecution",
    "auto_edit_protocol_for_runtime",
    "execute_auto_edit",
    "execute_auto_edit_decision",
]
