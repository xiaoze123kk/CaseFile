"""M1 DB Canned Outcome harness for the CaseFile chat Agent.

M1 runs each Eval Task through the real production path (send_agent_message →
Worker → complete_chat_task → persisted AgentMessage/PatchSet/Draft) with a
deterministic canned provider, then grades the persisted Outcome rather than
the raw candidate. The provider derives a self-consistent reference outcome
from the frozen request, so this mode proves three invariants:

1. the production completion hook persists exactly what the Grader expects;
2. denied routes suppress suggestions instead of failing the task;
3. no Draft revision/content change happens without an explicit apply.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from casefile.agent_runtime.chat_intent import route_allows_suggestions
from casefile.agent_runtime.models import (
    CaseFileChatAuditFindingCandidate,
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatResult,
    CaseFileChatSuggestionCandidate,
    CaseFileChatSuggestionCandidateV2,
    ToolMetrics,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeExpectations,
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    grade_chat_outcome,
)


def _first_object_id(casefile: dict[str, Any], collection: str) -> str | None:
    items = casefile.get(collection)
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            return str(item["id"])
    return None


def _first_issue_id(validation_issues: tuple[dict[str, Any], ...]) -> str | None:
    for issue in validation_issues:
        if isinstance(issue, dict) and isinstance(issue.get("issue_id"), str):
            return str(issue["issue_id"])
    return None


class CannedChatOutcomeProvider(FakeProvider):
    """Return a reference-quality chat outcome derived from the frozen request."""

    def __init__(self) -> None:
        self.requests: list[CaseFileChatRequest] = []

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        self.requests.append(request)
        entity_id = _first_object_id(request.casefile, "entities")
        event_id = _first_object_id(request.casefile, "events")
        issue_id = _first_issue_id(request.validation_issues)
        suggestions: list[
            CaseFileChatSuggestionCandidate | CaseFileChatSuggestionCandidateV2
        ] = []
        findings: list[CaseFileChatAuditFindingCandidate] = []
        route = request.route
        understanding = request.task_understanding
        intent = understanding.primary_intent if understanding is not None else None
        clean_audit = (
            intent == "logic_audit"
            and ("没有漏洞就不要提出任何修改" in request.message or "干净卷宗" in request.message)
        )
        if (
            route is not None
            and route_allows_suggestions(route)
            and intent in {"edit_request", "logic_audit"}
            and entity_id is not None
            and not clean_audit
        ):
            if intent == "logic_audit":
                findings.append(
                    CaseFileChatAuditFindingCandidate(
                        finding_id="F1",
                        kind="contradiction",
                        severity="S2",
                        title="审计基准发现的描述矛盾",
                        statement="冻结卷宗中的对象描述与证据主张存在矛盾。",
                        needs_manual_review=False,
                        evidence_object_ids=[entity_id],
                        evidence_event_ids=[event_id] if event_id is not None else [],
                        evidence_validation_issue_ids=[issue_id] if issue_id is not None else [],
                    )
                )
                suggestions.append(
                    CaseFileChatSuggestionCandidateV2(
                        object_id=entity_id,
                        path="/description",
                        value_json=json.dumps(
                            "负责追查午夜重启原因的研究员。",
                            ensure_ascii=False,
                        ),
                        reason="M3 审计基准可审阅建议。",
                        finding_ref="F1",
                    )
                )
            else:
                suggestions.append(
                    CaseFileChatSuggestionCandidate(
                        object_id=entity_id,
                        path="/description",
                        value_json=json.dumps(
                            "负责追查午夜重启原因的研究员。",
                            ensure_ascii=False,
                        ),
                        reason="M1 基准可审阅建议。",
                    )
                )
        reference_ids = [entity_id] if entity_id is not None else []
        reference_events = [event_id] if event_id is not None else []
        reference_issues = [issue_id] if issue_id is not None else []
        if intent == "logic_audit":
            candidate: CaseFileChatCandidate | CaseFileChatCandidateV2 = CaseFileChatCandidateV2(
                answer="这是审计基准回复：已按冻结卷宗生成结构化发现与可审阅结果。",
                referenced_object_ids=reference_ids,
                referenced_event_ids=reference_events,
                referenced_validation_issue_ids=reference_issues,
                suggestions=[
                    CaseFileChatSuggestionCandidateV2(
                        object_id=item.object_id,
                        path=item.path,
                        value_json=item.value_json,
                        reason=item.reason,
                        finding_ref=item.finding_ref,
                    )
                    if isinstance(item, CaseFileChatSuggestionCandidateV2)
                    else CaseFileChatSuggestionCandidate(
                        object_id=item.object_id,
                        path=item.path,
                        value_json=item.value_json,
                        reason=item.reason,
                    )
                    for item in suggestions
                ],
                audit_findings=findings,
            )
        else:
            candidate = CaseFileChatCandidate(
                answer="这是 M1 基准回复：已按冻结卷宗生成可审阅的结果。",
                referenced_object_ids=reference_ids,
                referenced_event_ids=reference_events,
                referenced_validation_issue_ids=reference_issues,
                suggestions=[
                    CaseFileChatSuggestionCandidate(
                        object_id=item.object_id,
                        path=item.path,
                        value_json=item.value_json,
                        reason=item.reason,
                    )
                    for item in suggestions
                ],
            )
        usage: dict[str, Any] = {
            "requests": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        tools = ToolMetrics(calls=1, valid_calls=1, successful_calls=1)
        return CaseFileChatResult(candidate=candidate, usage=usage, tools=tools)


def canned_outcome_expectations(
    *,
    casefile: dict[str, Any],
    routing_intent: str | None,
) -> ChatOutcomeExpectations:
    """Expectations for the canned provider, derived from the frozen DB state."""

    entity_id = _first_object_id(casefile, "entities")
    event_id = _first_object_id(casefile, "events")
    required_suggestion_paths: tuple[tuple[str, str], ...] = ()
    if routing_intent in {"edit_request", "logic_audit"} and entity_id is not None:
        required_suggestion_paths = ((entity_id, "description"),)
    return ChatOutcomeExpectations(
        expected_object_ids=(entity_id,) if entity_id is not None else (),
        expected_event_ids=(event_id,) if event_id is not None else (),
        required_suggestion_paths=required_suggestion_paths,
        expected_primary_intent=routing_intent,
        requires_suggestion=bool(required_suggestion_paths),
    )


def persisted_candidate_from_result(
    result_jsonb: dict[str, Any],
    patch_operations: list[dict[str, Any]],
) -> CaseFileChatCandidate | CaseFileChatCandidateV2:
    """Rebuild the persisted candidate from TaskRun result and patch set view."""

    suggestions: list[
        CaseFileChatSuggestionCandidate | CaseFileChatSuggestionCandidateV2
    ] = []
    for operation in patch_operations:
        object_id = operation.get("object_id")
        field_path = operation.get("field_path")
        reason = operation.get("reason")
        if (
            isinstance(object_id, str)
            and isinstance(field_path, str)
            and isinstance(reason, str)
            and reason.strip()
        ):
            finding_ref = operation.get("finding_ref")
            value_json = json.dumps(operation.get("new_value"), ensure_ascii=False)
            if isinstance(finding_ref, str) and finding_ref.strip():
                suggestions.append(
                    CaseFileChatSuggestionCandidateV2(
                        object_id=object_id,
                        path=field_path,
                        value_json=value_json,
                        reason=reason,
                        finding_ref=finding_ref,
                    )
                )
            else:
                suggestions.append(
                    CaseFileChatSuggestionCandidate(
                        object_id=object_id,
                        path=field_path,
                        value_json=value_json,
                        reason=reason,
                    )
                )
    raw_findings = result_jsonb.get("audit_findings") or []
    findings = [
        CaseFileChatAuditFindingCandidate.model_validate(dict(finding))
        for finding in raw_findings
        if isinstance(finding, dict)
    ]
    if findings:
        return CaseFileChatCandidateV2(
            answer=str(result_jsonb.get("answer") or ""),
            referenced_object_ids=list(result_jsonb.get("referenced_object_ids") or []),
            referenced_event_ids=list(result_jsonb.get("referenced_event_ids") or []),
            referenced_validation_issue_ids=list(
                result_jsonb.get("referenced_validation_issue_ids") or []
            ),
            suggested_view=result_jsonb.get("suggested_view"),
            suggestions=[
                item if isinstance(item, CaseFileChatSuggestionCandidateV2)
                else CaseFileChatSuggestionCandidate(
                    object_id=item.object_id,
                    path=item.path,
                    value_json=item.value_json,
                    reason=item.reason,
                )
                for item in suggestions
            ],
            audit_findings=findings,
        )
    return CaseFileChatCandidate(
        answer=str(result_jsonb.get("answer") or ""),
        referenced_object_ids=list(result_jsonb.get("referenced_object_ids") or []),
        referenced_event_ids=list(result_jsonb.get("referenced_event_ids") or []),
        referenced_validation_issue_ids=list(
            result_jsonb.get("referenced_validation_issue_ids") or []
        ),
        suggested_view=result_jsonb.get("suggested_view"),
        suggestions=[
            item if isinstance(item, CaseFileChatSuggestionCandidate)
            else CaseFileChatSuggestionCandidate(
                object_id=item.object_id,
                path=item.path,
                value_json=item.value_json,
                reason=item.reason,
            )
            for item in suggestions
        ],
    )


def grade_persisted_canned_trial(
    task: ChatOutcomeTask,
    *,
    casefile: dict[str, Any],
    validation_issues: tuple[dict[str, Any], ...],
    candidate: CaseFileChatCandidate | CaseFileChatCandidateV2,
    routing: dict[str, Any],
    draft_unchanged: bool,
) -> ChatOutcomeTrialVerdict:
    """Grade one persisted canned Trial against frozen task expectations."""

    routing_intent = routing.get("intent")
    route_source = routing.get("route_source")
    if not isinstance(route_source, str):
        route_source = "unresolved"
    suggestion_policy = routing.get("suggestion_policy")
    if not isinstance(suggestion_policy, str):
        suggestion_policy = "inherit"
    if routing_intent == "logic_audit":
        expectations = task.expectations
    else:
        expectations = canned_outcome_expectations(
            casefile=casefile,
            routing_intent=routing_intent if isinstance(routing_intent, str) else None,
        )
    dynamic_task = replace(
        task,
        casefile=casefile,
        validation_issues=validation_issues,
        focus={"object_ids": [], "event_ids": [], "validation_issue_ids": []},
        expectations=expectations,
    )
    return grade_chat_outcome(
        dynamic_task,
        candidate,
        allow_suggestions=suggestion_policy != "deny",
        actual_intent=routing_intent if isinstance(routing_intent, str) else "unresolved",
        route_source=route_source,
        draft_unchanged=draft_unchanged,
    )


__all__ = [
    "CannedChatOutcomeProvider",
    "canned_outcome_expectations",
    "grade_persisted_canned_trial",
    "persisted_candidate_from_result",
]
