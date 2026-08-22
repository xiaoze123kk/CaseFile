"""Result-level Eval contracts, Grader, and calibration for CaseFile chat.

Terminology follows the Agent evaluation handbook: a `ChatOutcomeTask` is one
Task (frozen input + success criteria + Reference Solution), each execution is
a Trial, the persisted candidate plus Draft state is the Outcome, and every
dimension is scored by deterministic Grader assertions.

This module intentionally contains no I/O: it builds the 34-task T1 Suite,
grades one candidate at a time, and exposes a calibration runner that proves
the Grader accepts every Reference Solution and catches every mutation sample.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, cast

from casefile.agent_runtime.chat_intent import route_allows_suggestions
from casefile.agent_runtime.chat_safe_patches import server_gate_audit_suggestions
from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V4_VERSION,
    check_patch_proposal,
    find_casefile_object,
    simulate_patch_delta,
)
from casefile.agent_runtime.models import (
    AuditFindingKind,
    AuditFindingSeverity,
    CaseFileChatAuditFindingCandidate,
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatSuggestionCandidate,
    CaseFileChatSuggestionCandidateV2,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.application.v1_editing import editable_fields_by_collection
from casefile.worker.executors.chat import resolve_chat_route

REFERENCE_PRECISION_TARGET = 1.0
REFERENCE_RECALL_TARGET = 1.0
SUGGESTION_LEGALITY_TARGET = 1.0
GOLDEN_MARKER_TARGET = 0.0

_FREE_TEXT: dict[str, Any] = {"entrypoint": "free_text", "preset_id": None}
_INSPECT_PRESET: dict[str, Any] = {"entrypoint": "preset", "preset_id": "inspect"}
_AUDIT_PRESET: dict[str, Any] = {"entrypoint": "preset", "preset_id": "audit"}

_OUTCOME_CASEFILE: dict[str, Any] = {
    "entities": [
        {
            "id": "ent_lucy",
            "name": "Lucy",
            "description": "负责追查午夜重启原因的研究员。",
            "aliases": ["露西"],
            "entity_type": "person",
        }
    ],
    "events": [
        {
            "id": "evt_restart",
            "title": "午夜重启",
            "description": "服务器在 23:59 自动重启。",
            "time": {"start": "23:59", "end": "00:05"},
        }
    ],
    "claims": [
        {
            "id": "clm_restart",
            "title": "欠压保护触发了回航",
            "statement": "欠压保护触发了回航。",
            "support_refs": ["info_restart_log"],
        }
    ],
    "hypotheses": [
        {"id": "hyp_first", "title": "大副修改了航行记录"},
        {"id": "hyp_second", "title": "传感器误报了欠压信号"},
    ],
    "information_units": [
        {
            "id": "info_restart_log",
            "title": "重启日志",
            "content": "23:59 系统重启，欠压信号在重启前 1 秒出现。",
        }
    ],
    "locations": [{"id": "loc_server_room", "name": "服务器机房"}],
    "relationships": [
        {
            "id": "rel_lucy_restart",
            "title": "负责调查",
            "from_ref": "ent_lucy",
            "to_ref": "evt_restart",
        }
    ],
    "resolution_specs": [
        {
            "id": "res_restart",
            "title": "午夜重启",
            "question_type": "what_happened",
            "accepted_answers": [],
            "conclusion": {"review_status": "proposed", "text": "欠压保护触发。"},
        }
    ],
}

_OUTCOME_VALIDATION_ISSUES: tuple[dict[str, Any], ...] = (
    {
        "issue_id": "validator:issue-1",
        "title": "事件时间倒置",
        "message": "events/0 的结束时间早于开始时间。",
    },
)


_COMMON_META = {
    "tags": [],
    "source_refs": [],
    "confidence": 1.0,
    "confirmation_status": "user_confirmed",
    "created_by": {"actor_type": "user", "actor_id": "user_local_owner"},
    "updated_at": "2042-06-01T12:00:00Z",
    "revision": 1,
}


def _planted_entity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "aliases": [],
        "traits": [],
        "goals": [],
        "secrets": [],
        "capabilities": [],
        "knowledge_states": [],
        **_COMMON_META,
        **item,
    }


def _planted_event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "truth_status": "canon_true",
        "time": {
            "kind": "range",
            "start": "2042-06-01T20:00",
            "end": "2042-06-01T20:03",
            "precision": "minute",
        },
        "participant_refs": [],
        "location_ref": None,
        "cause_refs": [],
        "effect_refs": [],
        "observed_by_refs": [],
        **_COMMON_META,
        **item,
    }


def _planted_claim(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_type": "causal",
        "support_refs": [],
        "refute_refs": [],
        "dependency_claim_refs": [],
        "status": "supported",
        "materiality": "critical",
        **_COMMON_META,
        **item,
    }


def _planted_casefile(
    casefile_id: str,
    title: str,
    *,
    entities: tuple[dict[str, Any], ...] = (),
    events: tuple[dict[str, Any], ...] = (),
    claims: tuple[dict[str, Any], ...] = (),
    information_units: tuple[dict[str, Any], ...] = (),
    resolution_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "casefile_id": casefile_id,
        "title": title,
        "status": "draft",
        "version": {
            "version_id": f"draft_{casefile_id}_1",
            "version_no": 1,
            "parent_version_id": None,
        },
        "brief_ref": {"brief_id": f"brief_{casefile_id}", "version": 1},
        "resolution_specs": list(resolution_specs),
        "entities": [_planted_entity(item) for item in entities],
        "relationships": [],
        "locations": [],
        "events": [_planted_event(item) for item in events],
        "information_units": list(information_units),
        "claims": [_planted_claim(item) for item in claims],
        "hypotheses": [],
        "reasoning_paths": [],
        "constraints": [],
        "structure_locks": [],
        "content_notices": [],
        "extensions": {"casefile.fixture": {"purpose": "logic_audit_eval"}},
    }


_EDITING_CASEFILE = _planted_casefile(
    "case_chat_editing",
    "午夜重启（编辑金例）",
    entities=(
        {
            "id": "ent_lucy",
            "entity_type": "person",
            "name": "Lucy",
            "description": "负责追查午夜重启原因的研究员。",
            "aliases": ["露西"],
        },
    ),
    events=(
        {
            "id": "evt_restart",
            "title": "午夜重启",
            "description": "服务器在 23:59 自动重启。",
        },
    ),
)

_EDIT_DESCRIPTION_CASEFILE = deepcopy(_EDITING_CASEFILE)
_EDIT_DESCRIPTION_CASEFILE["entities"][0]["description"] = (
    "Lucy 是唯一能够揭开午夜重启真相的天才研究员。"
)

_MULTI_OBJECT_CASEFILE = deepcopy(_EDITING_CASEFILE)
_MULTI_OBJECT_CASEFILE["events"][0]["title"] = "服务器午夜重启"

_HISTORY_CONFLICT_CASEFILE = deepcopy(_EDITING_CASEFILE)
_HISTORY_CONFLICT_CASEFILE["entities"][0]["description"] = (
    "负责系统异常调查的研究员。"
)

_DUPLICATE_LABEL_CASEFILE = deepcopy(_EDITING_CASEFILE)
_DUPLICATE_LABEL_CASEFILE["resolution_specs"] = [
    {
        "id": "res_restart",
        "title": "午夜重启",
        "question_type": "fact_reconstruction",
        "reasoning_question": "午夜重启的根因是什么？",
        "conclusion_mode": "unique",
        "required_slots": [],
        "accepted_answers": [],
        "required_claim_refs": [],
        **_COMMON_META,
    }
]


_AUDIT_FRACTURED_CASEFILE = _planted_casefile(
    "case_audit_fractured_alliance",
    "破裂的同盟（审计金例）",
    entities=(
        {
            "id": "ent_leader",
            "entity_type": "person",
            "name": "联盟首领",
            "description": "同盟仍然稳固，双方互信没有变化。",
        },
        {
            "id": "ent_defector",
            "entity_type": "person",
            "name": "叛逃者",
            "description": "已经脱离同盟并带走核心情报。",
        },
    ),
    claims=(
        {
            "id": "claim_alliance_broken",
            "title": "同盟已经破裂",
            "statement": "同盟已经在叛逃事件后破裂。",
            "claim_type": "relationship",
        },
    ),
    events=(
        {
            "id": "evt_defection",
            "title": "叛逃事件",
            "participant_refs": [{"object_type": "entity", "object_id": "ent_defector"}],
        },
    ),
)

_AUDIT_RESTART_LOOP_CASEFILE = _planted_casefile(
    "case_audit_restart_loop",
    "第七次重启（审计金例）",
    entities=(
        {
            "id": "ent_researcher",
            "entity_type": "person",
            "name": "林研究员",
            "description": "认为第七次重启是由人工误操作导致的。",
        },
        {
            "id": "ent_backup_system",
            "entity_type": "system",
            "name": "备用控制系统",
            "description": "依据安全规则在失联与过热持续三分钟后强制重启主系统。",
        },
    ),
    events=(
        {
            "id": "evt_restart_seven",
            "title": "系统第七次重启",
            "participant_refs": [
                {"object_type": "entity", "object_id": "ent_backup_system"}
            ],
        },
    ),
    claims=(
        {
            "id": "claim_backup_trigger",
            "title": "备用系统自动触发重启",
            "statement": "第七次重启由备用系统依据安全规则自动触发。",
        },
    ),
)

_AUDIT_VANISHING_ROUTE_CASEFILE = _planted_casefile(
    "case_audit_vanishing_route",
    "消失的航线（审计金例）",
    entities=(
        {
            "id": "ent_captain",
            "entity_type": "person",
            "name": "船长",
            "description": "负责选择北侧灯塔航线撤离。",
        },
    ),
    events=(
        {
            "id": "evt_departure",
            "title": "南侧航线撤离",
            "description": "船队按北侧灯塔航线离开，在风暴前抵达安全港。",
        },
    ),
    resolution_specs=(
        {
            "id": "res_best_route",
            "title": "最优撤离路线",
            "question_type": "path_discovery",
            "reasoning_question": "哪条路线能在风暴前抵达安全港？",
            "conclusion_mode": "optimal",
            "required_slots": [],
            "accepted_answers": ["北侧灯塔航线"],
            "required_claim_refs": [],
            "tags": [],
            "source_refs": [],
            "confidence": 1.0,
            "confirmation_status": "user_confirmed",
            "created_by": {"actor_type": "user", "actor_id": "user_local_owner"},
            "updated_at": "2042-06-01T12:00:00Z",
            "revision": 1,
        },
    ),
)

_AUDIT_CLEAN_CASEFILE = _planted_casefile(
    "case_audit_clean",
    "干净卷宗（审计金例）",
    entities=(
        {
            "id": "ent_lucy",
            "entity_type": "person",
            "name": "Lucy",
            "description": "负责追查午夜重启原因的研究员。",
        },
    ),
    claims=(
        {
            "id": "claim_restart",
            "title": "欠压保护触发回航",
            "statement": "欠压保护在日志记录后触发了回航。",
        },
    ),
)


@dataclass(frozen=True, slots=True)
class ExpectedSuggestion:
    """One required patch target with an optional exact JSON value."""

    object_id: str
    path: str
    value_json: str | None = None


@dataclass(frozen=True, slots=True)
class ChatOutcomeExpectations:
    """Deterministic success criteria for one Task."""

    expected_object_ids: tuple[str, ...] = ()
    expected_event_ids: tuple[str, ...] = ()
    expected_validation_issue_ids: tuple[str, ...] = ()
    forbidden_object_ids: tuple[str, ...] = ()
    forbidden_event_ids: tuple[str, ...] = ()
    forbidden_validation_issue_ids: tuple[str, ...] = ()
    required_suggestions: tuple[ExpectedSuggestion, ...] = ()
    forbidden_suggestion_paths: tuple[tuple[str, str], ...] = ()
    suggestion_count_range: tuple[int, int] | None = None
    audit_finding_count_range: tuple[int, int] | None = None
    required_audit_finding_kinds: tuple[str, ...] = ()
    forbidden_audit_finding_kinds: tuple[str, ...] = ()
    required_audit_evidence_object_ids: tuple[str, ...] = ()
    required_audit_evidence_event_ids: tuple[str, ...] = ()
    required_audit_evidence_validation_issue_ids: tuple[str, ...] = ()
    audit_findings_must_be_legal: bool = True
    simulate_suggestions: bool = False
    expected_answer_markers: tuple[str, ...] = ()
    expected_primary_intent: str | None = None
    requires_suggestion: bool = False
    references_must_exist: bool = True
    suggestions_must_be_legal: bool = True
    no_unnecessary_suggestions: bool = True
    answer_must_not_be_blank: bool = True

@dataclass(frozen=True, slots=True)
class ChatOutcomeTask:
    """One Eval Task with a frozen input and a Reference Solution."""

    task_id: str
    message: str
    hint: dict[str, Any]
    expectations: ChatOutcomeExpectations
    reference_candidate: CaseFileChatCandidate | CaseFileChatCandidateV2
    tier: Literal["T1", "T2"] = "T1"
    kind: Literal["golden", "boundary", "adversarial", "feedback"] = "golden"
    focus: dict[str, Any] | None = None
    history: tuple[dict[str, str], ...] = ()
    casefile: dict[str, Any] | None = None
    validation_issues: tuple[dict[str, Any], ...] | None = None
    dangerous_pair: tuple[str, str] | None = None
    capability: str | None = None

    @property
    def frozen_casefile(self) -> dict[str, Any]:
        return _OUTCOME_CASEFILE if self.casefile is None else self.casefile

    @property
    def frozen_validation_issues(self) -> tuple[dict[str, Any], ...]:
        if self.validation_issues is None:
            return _OUTCOME_VALIDATION_ISSUES
        return self.validation_issues


@dataclass(frozen=True, slots=True)
class ChatOutcomeThresholds:
    """Capability thresholds applied to one Trial."""

    reference_precision: float = REFERENCE_PRECISION_TARGET
    reference_recall: float = REFERENCE_RECALL_TARGET
    suggestion_legality: float = SUGGESTION_LEGALITY_TARGET


@dataclass(frozen=True, slots=True)
class ChatOutcomeTrialVerdict:
    """Grader output for one Trial, with safety and capability gates."""

    task_id: str
    trial_no: int = 1
    reference_precision: float = 1.0
    reference_recall: float = 1.0
    reference_valid_count: int = 0
    reference_total_count: int = 0
    expected_reference_hits: int = 0
    expected_reference_total: int = 0
    forbidden_reference_count: int = 0
    duplicate_reference_count: int = 0
    suggestion_legality: float = 1.0
    suggestion_valid_count: int = 0
    suggestion_total_count: int = 0
    forbidden_suggestion_count: int = 0
    missing_required_suggestion_count: int = 0
    suggestion_value_mismatch_count: int = 0
    unexpected_suggestion_count: int = 0
    unnecessary_suggestions: bool = False
    missing_edit_suggestion: bool = False
    audit_finding_count: int = 0
    audit_finding_evidence_precision: float = 1.0
    audit_finding_evidence_valid_count: int = 0
    audit_finding_evidence_total_count: int = 0
    required_audit_finding_hits: int = 0
    required_audit_finding_total: int = 0
    forbidden_audit_finding_kind_count: int = 0
    audit_finding_evidence_dangling_count: int = 0
    audit_finding_ref_unknown_count: int = 0
    simulate_legality: float = 1.0
    simulate_valid_count: int = 0
    simulate_total_count: int = 0
    simulate_introduces_new_issues_count: int = 0
    blank_answer: bool = False
    marker_hit_rate: float = 1.0
    draft_unchanged: bool = True
    actual_intent: str = "unresolved"
    route_source: str = "unresolved"
    allow_suggestions: bool = True
    failures: tuple[str, ...] = ()
    safety_passed: bool = True
    capability_passed: bool = True
    passed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trial_no": self.trial_no,
            "reference_precision": self.reference_precision,
            "reference_recall": self.reference_recall,
            "reference_valid_count": self.reference_valid_count,
            "reference_total_count": self.reference_total_count,
            "expected_reference_hits": self.expected_reference_hits,
            "expected_reference_total": self.expected_reference_total,
            "forbidden_reference_count": self.forbidden_reference_count,
            "duplicate_reference_count": self.duplicate_reference_count,
            "suggestion_legality": self.suggestion_legality,
            "suggestion_valid_count": self.suggestion_valid_count,
            "suggestion_total_count": self.suggestion_total_count,
            "forbidden_suggestion_count": self.forbidden_suggestion_count,
            "missing_required_suggestion_count": self.missing_required_suggestion_count,
            "suggestion_value_mismatch_count": self.suggestion_value_mismatch_count,
            "unexpected_suggestion_count": self.unexpected_suggestion_count,
            "unnecessary_suggestions": self.unnecessary_suggestions,
            "missing_edit_suggestion": self.missing_edit_suggestion,
            "audit_finding_count": self.audit_finding_count,
            "audit_finding_evidence_precision": self.audit_finding_evidence_precision,
            "audit_finding_evidence_valid_count": self.audit_finding_evidence_valid_count,
            "audit_finding_evidence_total_count": self.audit_finding_evidence_total_count,
            "required_audit_finding_hits": self.required_audit_finding_hits,
            "required_audit_finding_total": self.required_audit_finding_total,
            "forbidden_audit_finding_kind_count": self.forbidden_audit_finding_kind_count,
            "audit_finding_evidence_dangling_count": self.audit_finding_evidence_dangling_count,
            "audit_finding_ref_unknown_count": self.audit_finding_ref_unknown_count,
            "simulate_legality": self.simulate_legality,
            "simulate_valid_count": self.simulate_valid_count,
            "simulate_total_count": self.simulate_total_count,
            "simulate_introduces_new_issues_count": self.simulate_introduces_new_issues_count,
            "blank_answer": self.blank_answer,
            "marker_hit_rate": self.marker_hit_rate,
            "draft_unchanged": self.draft_unchanged,
            "actual_intent": self.actual_intent,
            "route_source": self.route_source,
            "allow_suggestions": self.allow_suggestions,
            "failures": list(self.failures),
            "safety_passed": self.safety_passed,
            "capability_passed": self.capability_passed,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class ChatOutcomeMutation:
    """A Grader negative control: one deliberately broken candidate."""

    mutation_id: str
    task_id: str
    mutated_candidate: CaseFileChatCandidate
    expected_failure: str


@dataclass(frozen=True, slots=True)
class ChatOutcomeCalibrationReport:
    """M0 output: the Grader passes every Reference and catches every mutation."""

    reference_verdicts: tuple[ChatOutcomeTrialVerdict, ...]
    mutation_verdicts: tuple[ChatOutcomeTrialVerdict, ...]
    mutation_misses: tuple[str, ...]
    reference_failures: tuple[str, ...]
    contract_failures: tuple[str, ...]
    status: Literal["passed", "failed"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reference_count": len(self.reference_verdicts),
            "reference_failures": list(self.reference_failures),
            "contract_failures": list(self.contract_failures),
            "reference_rows": [row.as_dict() for row in self.reference_verdicts],
            "mutation_count": len(self.mutation_verdicts),
            "mutation_misses": list(self.mutation_misses),
            "mutation_rows": [row.as_dict() for row in self.mutation_verdicts],
        }


def _suggestion(
    object_id: str,
    path: str,
    value: Any,
    reason: str,
) -> CaseFileChatSuggestionCandidate:
    return CaseFileChatSuggestionCandidate(
        object_id=object_id,
        path=path,
        value_json=json.dumps(value, ensure_ascii=False),
        reason=reason,
    )


_UNCONSTRAINED = object()


def _expected_suggestion(
    object_id: str,
    path: str,
    value: Any = _UNCONSTRAINED,
) -> ExpectedSuggestion:
    return ExpectedSuggestion(
        object_id=object_id,
        path=path,
        value_json=(
            None if value is _UNCONSTRAINED else json.dumps(value, ensure_ascii=False)
        ),
    )


def _candidate(
    answer: str,
    *,
    object_ids: tuple[str, ...] = (),
    event_ids: tuple[str, ...] = (),
    validation_issue_ids: tuple[str, ...] = (),
    suggestions: tuple[CaseFileChatSuggestionCandidate, ...] = (),
    suggested_view: str | None = None,
) -> CaseFileChatCandidate:
    return CaseFileChatCandidate(
        answer=answer,
        referenced_object_ids=list(object_ids),
        referenced_event_ids=list(event_ids),
        referenced_validation_issue_ids=list(validation_issue_ids),
        suggested_view=suggested_view,
        suggestions=list(suggestions),
    )


def _finding(
    finding_id: str,
    kind: AuditFindingKind,
    *,
    severity: AuditFindingSeverity = "S2",
    title: str = "",
    statement: str = "",
    needs_manual_review: bool = False,
    object_ids: tuple[str, ...] = (),
    event_ids: tuple[str, ...] = (),
    validation_issue_ids: tuple[str, ...] = (),
) -> CaseFileChatAuditFindingCandidate:
    return CaseFileChatAuditFindingCandidate(
        finding_id=finding_id,
        kind=kind,
        severity=severity,
        title=title or f"{finding_id} 逻辑漏洞",
        statement=statement or "基准审计发现。",
        needs_manual_review=needs_manual_review,
        evidence_object_ids=list(object_ids),
        evidence_event_ids=list(event_ids),
        evidence_validation_issue_ids=list(validation_issue_ids),
    )


def _audit_suggestion(
    object_id: str,
    path: str,
    value: object,
    reason: str,
    *,
    finding_ref: str,
) -> CaseFileChatSuggestionCandidateV2:
    return CaseFileChatSuggestionCandidateV2(
        object_id=object_id,
        path=path,
        value_json=json.dumps(value, ensure_ascii=False),
        reason=reason,
        finding_ref=finding_ref,
    )


def _audit_candidate(
    answer: str,
    *,
    object_ids: tuple[str, ...] = (),
    event_ids: tuple[str, ...] = (),
    validation_issue_ids: tuple[str, ...] = (),
    findings: tuple[CaseFileChatAuditFindingCandidate, ...] = (),
    suggestions: tuple[CaseFileChatSuggestionCandidateV2, ...] = (),
) -> CaseFileChatCandidateV2:
    return CaseFileChatCandidateV2(
        answer=answer,
        referenced_object_ids=list(object_ids),
        referenced_event_ids=list(event_ids),
        referenced_validation_issue_ids=list(validation_issue_ids),
        suggested_view=None,
        suggestions=list(suggestions),
        audit_findings=list(findings),
    )


def _focus(
    *,
    object_ids: tuple[str, ...] = (),
    event_ids: tuple[str, ...] = (),
    validation_issue_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "object_ids": list(object_ids),
        "event_ids": list(event_ids),
        "validation_issue_ids": list(validation_issue_ids),
    }


def _casefile_object_ids(casefile: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for collection, items in casefile.items():
        if collection == "events" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.add(item["id"])
    return ids


def _casefile_event_ids(casefile: dict[str, Any]) -> set[str]:
    events = casefile.get("events")
    if not isinstance(events, list):
        return set()
    return {
        item["id"] for item in events if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _validation_issue_ids(issues: tuple[dict[str, Any], ...]) -> set[str]:
    return {
        issue["issue_id"]
        for issue in issues
        if isinstance(issue, dict) and isinstance(issue.get("issue_id"), str)
    }


def _collection_for_object(casefile: dict[str, Any], object_id: str) -> str | None:
    matches = [
        collection
        for collection, items in casefile.items()
        if isinstance(items, list)
        and any(isinstance(item, dict) and item.get("id") == object_id for item in items)
    ]
    return matches[0] if len(matches) == 1 else None


def _top_level_field(path: str) -> str | None:
    if not path.startswith("/"):
        return None
    segments = path.split("/")[1:]
    if not segments or any(not segment for segment in segments):
        return None
    top = segments[0]
    for segment in segments:
        if "~" in segment:
            if any(marker in segment for marker in ("~0", "~1")):
                continue
            return None
    return top.replace("~1", "/").replace("~0", "~")


def _value_json_valid(value_json: str) -> bool:
    try:
        json.loads(value_json)
    except (TypeError, ValueError):
        return False
    return True


_MISSING = object()


def _pointer_value(document: Any, path: str) -> Any:
    if not path.startswith("/"):
        return _MISSING
    current = document
    for raw_segment in path.split("/")[1:]:
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def _is_raw_json_text(answer: str) -> bool:
    stripped = answer.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except (TypeError, ValueError):
        return False
    return True


def _request_for_task(
    task: ChatOutcomeTask,
    *,
    task_run_id: int = 1,
    model_id: str = "fake-baseline",
    prompt_version: str = "casefile-chat-v12",
) -> CaseFileChatRequest:
    focus = (
        dict(task.focus)
        if task.focus is not None
        else {"object_ids": [], "event_ids": [], "validation_issue_ids": []}
    )
    issues = task.frozen_validation_issues
    return CaseFileChatRequest(
        task_run_id=task_run_id,
        prompt_version=prompt_version,
        toolset_version=CHAT_TOOLSET_V4_VERSION,
        casefile=task.frozen_casefile,
        history=task.history,
        message=task.message,
        editable_fields_by_collection=editable_fields_by_collection(),
        input_hash="0" * 64,
        model_id=model_id,
        api_key=None,
        max_turns=6,
        emit=lambda _event_type, _stage, _payload: None,
        validation={"issues": [dict(issue) for issue in issues]},
        validation_issues=issues,
        focus=focus,
        routing_hint=task.hint,
    )


def resolve_task_route(task: ChatOutcomeTask) -> CaseFileChatRequest:
    """Run the exact Worker routing cascade with FakeProvider, no network."""
    return resolve_chat_route(
        _request_for_task(task),
        provider=FakeProvider(),
    )


def grade_chat_outcome(
    task: ChatOutcomeTask,
    candidate: CaseFileChatCandidate | CaseFileChatCandidateV2,
    *,
    allow_suggestions: bool,
    trial_no: int = 1,
    thresholds: ChatOutcomeThresholds | None = None,
    actual_intent: str = "unresolved",
    route_source: str = "unresolved",
    draft_unchanged: bool = True,
) -> ChatOutcomeTrialVerdict:
    """Score one Trial against the Task's frozen success criteria."""

    thresholds = thresholds or ChatOutcomeThresholds()
    expectations = task.expectations
    casefile = task.frozen_casefile
    object_ids = _casefile_object_ids(casefile)
    event_ids = _casefile_event_ids(casefile)
    issue_ids = _validation_issue_ids(task.frozen_validation_issues)

    raw_objects = list(candidate.referenced_object_ids)
    raw_events = list(candidate.referenced_event_ids)
    raw_issues = list(candidate.referenced_validation_issue_ids)
    duplicate_reference_count = (
        len(raw_objects)
        - len(set(raw_objects))
        + len(raw_events)
        - len(set(raw_events))
        + len(raw_issues)
        - len(set(raw_issues))
    )

    valid_objects = [value for value in raw_objects if value in object_ids]
    valid_events = [value for value in raw_events if value in event_ids]
    valid_issues = [value for value in raw_issues if value in issue_ids]
    valid_reference_count = len(valid_objects) + len(valid_events) + len(valid_issues)
    total_reference_count = len(raw_objects) + len(raw_events) + len(raw_issues)
    reference_precision = (
        valid_reference_count / total_reference_count if total_reference_count else 1.0
    )

    expected_object_hits = sum(
        1 for value in expectations.expected_object_ids if value in raw_objects
    )
    expected_event_hits = sum(1 for value in expectations.expected_event_ids if value in raw_events)
    expected_issue_hits = sum(
        1 for value in expectations.expected_validation_issue_ids if value in raw_issues
    )
    expected_reference_hits = expected_object_hits + expected_event_hits + expected_issue_hits
    expected_reference_total = (
        len(expectations.expected_object_ids)
        + len(expectations.expected_event_ids)
        + len(expectations.expected_validation_issue_ids)
    )
    reference_recall = (
        expected_reference_hits / expected_reference_total if expected_reference_total else 1.0
    )

    forbidden_reference_count = (
        sum(1 for value in raw_objects if value in expectations.forbidden_object_ids)
        + sum(1 for value in raw_events if value in expectations.forbidden_event_ids)
        + sum(1 for value in raw_issues if value in expectations.forbidden_validation_issue_ids)
    )

    editable_fields = editable_fields_by_collection()
    suggestion_scores: list[int] = []
    seen_paths: set[tuple[str, str]] = set()
    forbidden_suggestion_count = 0
    for suggestion in candidate.suggestions:
        top = _top_level_field(suggestion.path)
        collection = _collection_for_object(casefile, suggestion.object_id)
        if top is None or collection is None or top not in editable_fields.get(collection, ()):
            score = 0
        elif not _value_json_valid(suggestion.value_json):
            score = 0
        elif (
            (found := find_casefile_object(casefile, suggestion.object_id)) is not None
            and _pointer_value(found[1], suggestion.path) == json.loads(suggestion.value_json)
        ):
            score = 0
        elif not suggestion.reason.strip():
            score = 0
        elif (suggestion.object_id, top) in seen_paths:
            score = 0
        else:
            score = 1
        if top is not None:
            seen_paths.add((suggestion.object_id, top))
            if (suggestion.object_id, top) in expectations.forbidden_suggestion_paths:
                forbidden_suggestion_count += 1
        suggestion_scores.append(score)
    suggestion_legality = (
        sum(suggestion_scores) / len(suggestion_scores) if suggestion_scores else 1.0
    )

    audit_findings = tuple(getattr(candidate, "audit_findings", ()) or ())
    finding_ids = {finding.finding_id for finding in audit_findings}
    finding_evidence_valid = 0
    finding_evidence_total = 0
    finding_evidence_dangling = 0
    for finding in audit_findings:
        for evidence_id in finding.evidence_object_ids:
            finding_evidence_total += 1
            if evidence_id in object_ids:
                finding_evidence_valid += 1
            else:
                finding_evidence_dangling += 1
        for evidence_id in finding.evidence_event_ids:
            finding_evidence_total += 1
            if evidence_id in event_ids:
                finding_evidence_valid += 1
            else:
                finding_evidence_dangling += 1
        for evidence_id in finding.evidence_validation_issue_ids:
            finding_evidence_total += 1
            if evidence_id in issue_ids:
                finding_evidence_valid += 1
            else:
                finding_evidence_dangling += 1
    finding_evidence_precision = (
        finding_evidence_valid / finding_evidence_total
        if finding_evidence_total
        else 1.0
    )
    required_audit_finding_hits = sum(
        1 for kind in expectations.required_audit_finding_kinds
        if any(finding.kind == kind for finding in audit_findings)
    )
    required_audit_finding_total = len(expectations.required_audit_finding_kinds)
    forbidden_audit_finding_kind_count = sum(
        1 for finding in audit_findings
        if finding.kind in expectations.forbidden_audit_finding_kinds
    )
    expected_audit_evidence_hits = (
        sum(
            1
            for evidence_id in expectations.required_audit_evidence_object_ids
            if any(evidence_id in finding.evidence_object_ids for finding in audit_findings)
        )
        + sum(
            1
            for evidence_id in expectations.required_audit_evidence_event_ids
            if any(evidence_id in finding.evidence_event_ids for finding in audit_findings)
        )
        + sum(
            1
            for evidence_id in expectations.required_audit_evidence_validation_issue_ids
            if any(
                evidence_id in finding.evidence_validation_issue_ids
                for finding in audit_findings
            )
        )
    )
    expected_audit_evidence_total = (
        len(expectations.required_audit_evidence_object_ids)
        + len(expectations.required_audit_evidence_event_ids)
        + len(expectations.required_audit_evidence_validation_issue_ids)
    )
    audit_finding_ref_unknown = sum(
        1
        for suggestion in candidate.suggestions
        if getattr(suggestion, "finding_ref", None) not in (None, *finding_ids)
    )
    audit_finding_ref_unknown_count = audit_finding_ref_unknown

    simulate_scores: list[int] = []
    simulate_introduces_count = 0
    if expectations.simulate_suggestions:
        for suggestion in candidate.suggestions:
            try:
                value_json = suggestion.value_json
                preview = simulate_patch_delta(
                    task.frozen_casefile,
                    task.frozen_validation_issues,
                    suggestion.object_id,
                    suggestion.path,
                    value_json,
                )
            except Exception:
                preview = {"valid": False, "advice": "simulate_error"}
            if preview.get("valid") is True and preview.get("advice") in {
                "safe_to_propose",
                "fixes_n_issues",
            }:
                simulate_scores.append(1)
            else:
                simulate_scores.append(0)
                if preview.get("advice") == "introduces_new_issues":
                    simulate_introduces_count += 1
    simulate_legality = (
        sum(simulate_scores) / len(simulate_scores) if simulate_scores else 1.0
    )

    missing_required_suggestion_count = 0
    suggestion_value_mismatch_count = 0
    for expected in expectations.required_suggestions:
        matching = []
        for raw_suggestion, score in zip(
            candidate.suggestions, suggestion_scores, strict=True
        ):
            suggestion = cast(
                CaseFileChatSuggestionCandidate | CaseFileChatSuggestionCandidateV2,
                raw_suggestion,
            )
            if (
                score == 1
                and suggestion.object_id == expected.object_id
                and suggestion.path == expected.path
            ):
                matching.append(suggestion)
        if not matching:
            missing_required_suggestion_count += 1
            continue
        if expected.value_json is not None:
            expected_value = json.loads(expected.value_json)
            if not any(
                json.loads(suggestion.value_json) == expected_value
                for suggestion in matching
            ):
                suggestion_value_mismatch_count += 1
    required_target_set = {
        (expected.object_id, expected.path)
        for expected in expectations.required_suggestions
    }
    unexpected_suggestion_count = (
        sum(
            (suggestion.object_id, suggestion.path) not in required_target_set
            for suggestion in candidate.suggestions
        )
        if required_target_set
        else 0
    )

    unnecessary_suggestions = not allow_suggestions and len(candidate.suggestions) > 0
    missing_edit_suggestion = (
        expectations.requires_suggestion
        and expectations.expected_primary_intent == "edit_request"
        and allow_suggestions
        and sum(suggestion_scores) == 0
    )
    finding_count_mismatch = False
    if expectations.audit_finding_count_range is not None:
        low, high = expectations.audit_finding_count_range
        finding_count_mismatch = not (low <= len(audit_findings) <= high)
    suggestion_count_mismatch = False
    if expectations.suggestion_count_range is not None:
        low, high = expectations.suggestion_count_range
        suggestion_count_mismatch = not (low <= len(candidate.suggestions) <= high)
    missing_required_audit_finding_count = max(
        0, required_audit_finding_total - required_audit_finding_hits
    )
    missing_required_audit_evidence_count = max(
        0, expected_audit_evidence_total - expected_audit_evidence_hits
    )

    answer = candidate.answer
    blank_answer = not answer.strip() or _is_raw_json_text(answer)
    marker_hit_rate = (
        sum(1 for marker in expectations.expected_answer_markers if marker in answer)
        / len(expectations.expected_answer_markers)
        if expectations.expected_answer_markers
        else 1.0
    )

    failures: list[str] = []
    safety_failed = False
    if expectations.references_must_exist and forbidden_reference_count:
        safety_failed = True
        failures.append("forbidden_reference")
    if duplicate_reference_count:
        safety_failed = True
        failures.append("duplicate_reference")
    if forbidden_suggestion_count:
        safety_failed = True
        failures.append("forbidden_suggestion_path")
    if expectations.no_unnecessary_suggestions and unnecessary_suggestions:
        safety_failed = True
        failures.append("unnecessary_suggestion")
    if expectations.answer_must_not_be_blank and blank_answer:
        safety_failed = True
        failures.append("blank_answer")
    if expectations.audit_findings_must_be_legal and finding_evidence_dangling:
        safety_failed = True
        failures.append("audit_finding_evidence")
    if audit_finding_ref_unknown_count:
        safety_failed = True
        failures.append("audit_finding_ref_unknown")
    if forbidden_audit_finding_kind_count:
        safety_failed = True
        failures.append("forbidden_audit_finding_kind")
    if simulate_introduces_count:
        safety_failed = True
        failures.append("simulate_introduces_new_issues")
    if not draft_unchanged:
        safety_failed = True
        failures.append("draft_changed_without_apply")

    capability_failed = False
    if reference_precision < thresholds.reference_precision:
        capability_failed = True
        failures.append("reference_precision")
    if reference_recall < thresholds.reference_recall:
        capability_failed = True
        failures.append("reference_recall")
    if expectations.suggestions_must_be_legal and (
        suggestion_legality < thresholds.suggestion_legality
    ):
        capability_failed = True
        failures.append("suggestion_legality")
    if missing_required_suggestion_count:
        capability_failed = True
        failures.append("missing_required_suggestion")
    if suggestion_value_mismatch_count:
        capability_failed = True
        failures.append("suggestion_value_mismatch")
    if unexpected_suggestion_count:
        capability_failed = True
        failures.append("unexpected_suggestion")
    if missing_edit_suggestion:
        capability_failed = True
        failures.append("missing_edit_suggestion")
    if suggestion_count_mismatch:
        capability_failed = True
        failures.append("suggestion_count_range")
    if finding_count_mismatch:
        capability_failed = True
        failures.append("audit_finding_count_range")
    if missing_required_audit_finding_count:
        capability_failed = True
        failures.append("missing_required_audit_finding_kind")
    if missing_required_audit_evidence_count:
        capability_failed = True
        failures.append("missing_required_audit_finding_evidence")
    if expectations.simulate_suggestions and (
        simulate_legality < thresholds.suggestion_legality
    ):
        capability_failed = True
        failures.append("simulate_legality")

    return ChatOutcomeTrialVerdict(
        task_id=task.task_id,
        trial_no=trial_no,
        reference_precision=round(reference_precision, 6),
        reference_recall=round(reference_recall, 6),
        reference_valid_count=valid_reference_count,
        reference_total_count=total_reference_count,
        expected_reference_hits=expected_reference_hits,
        expected_reference_total=expected_reference_total,
        forbidden_reference_count=forbidden_reference_count,
        duplicate_reference_count=duplicate_reference_count,
        suggestion_legality=round(suggestion_legality, 6),
        suggestion_valid_count=sum(suggestion_scores),
        suggestion_total_count=len(suggestion_scores),
        forbidden_suggestion_count=forbidden_suggestion_count,
        missing_required_suggestion_count=missing_required_suggestion_count,
        suggestion_value_mismatch_count=suggestion_value_mismatch_count,
        unexpected_suggestion_count=unexpected_suggestion_count,
        unnecessary_suggestions=unnecessary_suggestions,
        missing_edit_suggestion=missing_edit_suggestion,
        audit_finding_count=len(audit_findings),
        audit_finding_evidence_precision=round(finding_evidence_precision, 6),
        audit_finding_evidence_valid_count=finding_evidence_valid,
        audit_finding_evidence_total_count=finding_evidence_total,
        required_audit_finding_hits=required_audit_finding_hits,
        required_audit_finding_total=required_audit_finding_total,
        forbidden_audit_finding_kind_count=forbidden_audit_finding_kind_count,
        audit_finding_evidence_dangling_count=finding_evidence_dangling,
        audit_finding_ref_unknown_count=audit_finding_ref_unknown_count,
        simulate_legality=round(simulate_legality, 6),
        simulate_valid_count=sum(simulate_scores),
        simulate_total_count=len(simulate_scores),
        simulate_introduces_new_issues_count=simulate_introduces_count,
        blank_answer=blank_answer,
        marker_hit_rate=round(marker_hit_rate, 6),
        draft_unchanged=draft_unchanged,
        actual_intent=actual_intent,
        route_source=route_source,
        allow_suggestions=allow_suggestions,
        failures=tuple(failures),
        safety_passed=not safety_failed,
        capability_passed=not capability_failed,
        passed=not safety_failed and not capability_failed,
    )


def grade_reference_solution(task: ChatOutcomeTask) -> ChatOutcomeTrialVerdict:
    """Resolve the Task route and grade its Reference Solution."""
    request = resolve_task_route(task)
    route = request.route
    if route is None:
        allow_suggestions = True
        actual_intent = "unresolved"
        route_source = "unresolved"
    else:
        allow_suggestions = route_allows_suggestions(route)
        understanding = request.task_understanding
        actual_intent = understanding.primary_intent if understanding is not None else "unresolved"
        route_source = route.route_source
    return grade_chat_outcome(
        task,
        task.reference_candidate,
        allow_suggestions=allow_suggestions,
        actual_intent=actual_intent,
        route_source=route_source,
    )


def validate_task_contract(task: ChatOutcomeTask) -> tuple[str, ...]:
    """Validate that a frozen Task and its Reference are executable and honest."""

    failures: list[str] = []
    object_ids = _casefile_object_ids(task.frozen_casefile)
    event_ids = _casefile_event_ids(task.frozen_casefile)
    issue_ids = _validation_issue_ids(task.frozen_validation_issues)
    for slot, expected_ids, available_ids in (
        ("object", task.expectations.expected_object_ids, object_ids),
        ("event", task.expectations.expected_event_ids, event_ids),
        ("validation_issue", task.expectations.expected_validation_issue_ids, issue_ids),
    ):
        for expected_id in expected_ids:
            if expected_id not in available_ids:
                failures.append(f"{task.task_id}:{slot}_id_missing:{expected_id}")

    if task.task_id.startswith("golden-") and not task.capability:
        failures.append(f"{task.task_id}:golden_capability_missing")

    required = task.expectations.required_suggestions
    required_targets = [(item.object_id, item.path) for item in required]
    if len(required_targets) != len(set(required_targets)):
        failures.append(f"{task.task_id}:required_suggestion_duplicate")
    reference_targets = [
        (suggestion.object_id, suggestion.path)
        for suggestion in task.reference_candidate.suggestions
    ]
    if len(reference_targets) != len(set(reference_targets)):
        failures.append(f"{task.task_id}:reference_suggestion_duplicate")
    if set(reference_targets) != set(required_targets):
        failures.append(f"{task.task_id}:reference_target_mismatch")

    request = _request_for_task(task)
    expected_by_target = {
        (expected.object_id, expected.path): expected for expected in required
    }
    for expected in required:
        if _top_level_field(expected.path) is None:
            failures.append(
                f"{task.task_id}:required_suggestion_path_invalid:"
                f"{expected.object_id}:{expected.path}"
            )
        if expected.value_json is not None and not _value_json_valid(expected.value_json):
            failures.append(
                f"{task.task_id}:required_suggestion_value_invalid:"
                f"{expected.object_id}:{expected.path}"
            )
    for suggestion in task.reference_candidate.suggestions:
        target = (suggestion.object_id, suggestion.path)
        check = check_patch_proposal(
            request,
            suggestion.object_id,
            suggestion.path,
            suggestion.value_json,
            require_path_exists=True,
        )
        if check.reason_code is not None:
            failures.append(
                f"{task.task_id}:reference_patch_invalid:{suggestion.object_id}:"
                f"{suggestion.path}:{check.reason_code}"
            )
            continue
        current = _pointer_value(check.item, suggestion.path)
        proposed = json.loads(suggestion.value_json)
        if current == proposed:
            failures.append(
                f"{task.task_id}:reference_patch_noop:"
                f"{suggestion.object_id}:{suggestion.path}"
            )
        matched_expected = expected_by_target.get(target)
        if matched_expected is not None and matched_expected.value_json is not None:
            if proposed != json.loads(matched_expected.value_json):
                failures.append(
                    f"{task.task_id}:reference_value_mismatch:"
                    f"{suggestion.object_id}:{suggestion.path}"
                )
        simulation = simulate_patch_delta(
            task.frozen_casefile,
            task.frozen_validation_issues,
            suggestion.object_id,
            suggestion.path,
            suggestion.value_json,
        )
        if simulation.get("valid") is not True or simulation.get("advice") not in {
            "safe_to_propose",
            "fixes_n_issues",
        }:
            failures.append(
                f"{task.task_id}:reference_simulation_failed:"
                f"{suggestion.object_id}:{suggestion.path}:"
                f"{simulation.get('reason_code') or simulation.get('advice')}"
            )

    if isinstance(task.reference_candidate, CaseFileChatCandidateV2) and reference_targets:
        gate = server_gate_audit_suggestions(
            request,
            [suggestion.model_dump() for suggestion in task.reference_candidate.suggestions],
        )
        if gate.failures or gate.discards or len(gate.registry.candidates) != len(
            reference_targets
        ):
            failures.append(f"{task.task_id}:reference_audit_server_gate_failed")
    return tuple(failures)


def _large_casefile() -> dict[str, Any]:
    entities = [
        {
            "id": f"ent_{index:02d}",
            "name": f"对象{index}",
            "description": "用于大卷宗检索基准的对象。",
        }
        for index in range(30)
    ]
    events = [
        {
            "id": f"evt_{index:02d}",
            "title": f"事件{index}",
            "description": "用于大卷宗检索基准的事件。",
        }
        for index in range(20)
    ]
    entities[1].update(
        name="欠压保护控制器",
        description="负责监测欠压保护状态。",
    )
    events[1].update(
        title="欠压保护触发",
        description="欠压保护触发后系统执行回航。",
    )
    return {"entities": entities, "events": events}


def _edit_lucy_description(
    reason: str = "原文语气过于戏剧化。",
    *,
    value: str = "负责追查午夜重启原因的研究员。",
) -> CaseFileChatCandidate:
    return _candidate(
        "已生成可审阅的修改建议，不会直接改动工作稿。",
        object_ids=("ent_lucy",),
        suggestions=(
            _suggestion(
                "ent_lucy",
                "/description",
                value,
                reason,
            ),
        ),
    )


def build_outcome_tasks() -> tuple[ChatOutcomeTask, ...]:
    """Return the frozen T1 fixtures from their dedicated assembly module."""

    from casefile.benchmark.chat_outcome_fixtures import (
        build_outcome_tasks as build_fixtures,
    )

    return build_fixtures()


def build_outcome_tasks_from_audit_feedback(
    fixtures: Iterable[dict[str, Any]],
) -> tuple[ChatOutcomeTask, ...]:
    """Convert exported audit feedback fixtures into a replayable outcome suite.

    Human decisions become success criteria:

    * ``applied``  — the accepted finding, evidence slots, and patch must be
      reproduced;
    * ``rejected`` / ``undone`` — the frozen input becomes a zero-finding,
      zero-suggestion gate (the human already refused the patch).

    Malformed library entries raise ``ValueError`` instead of being skipped so
    a corrupt fixture pack is loud during eval curation.
    """

    tasks: list[ChatOutcomeTask] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("audit feedback fixture must be an object")
        fixture_id = str(fixture.get("fixture_id") or "audit-feedback")
        decision = fixture.get("decision")
        if decision not in {"applied", "rejected", "undone"}:
            raise ValueError(f"{fixture_id}: unsupported decision {decision!r}")
        casefile = fixture.get("casefile")
        if not isinstance(casefile, dict):
            raise ValueError(f"{fixture_id}: casefile must be an object")
        message = fixture.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"{fixture_id}: message must be a non-blank string")
        hint = fixture.get("hint")
        if not isinstance(hint, dict) or not hint.get("entrypoint"):
            hint = dict(_FREE_TEXT)
        focus = fixture.get("focus")
        if not isinstance(focus, dict):
            focus = _focus()
        focus_values = {
            "object_ids": focus.get("object_ids") or [],
            "event_ids": focus.get("event_ids") or [],
            "validation_issue_ids": focus.get("validation_issue_ids") or [],
        }
        validation_issues = tuple(
            issue
            for issue in fixture.get("validation_issues") or ()
            if isinstance(issue, dict)
        )
        findings = tuple(
            CaseFileChatAuditFindingCandidate.model_validate(finding)
            for finding in fixture.get("audit_findings") or ()
            if isinstance(finding, dict)
        )
        operations = [
            operation
            for operation in fixture.get("patch_operations") or ()
            if isinstance(operation, dict)
            and isinstance(operation.get("target_object_id"), str)
            and isinstance(operation.get("field_path"), str)
        ]
        decision_label = {"applied": "已采纳", "rejected": "已拒绝", "undone": "已撤销"}[decision]
        task_id = f"audit-feedback-{decision}-{fixture_id}"
        referenced_object_ids = list(fixture.get("referenced_object_ids") or [])
        referenced_event_ids = list(fixture.get("referenced_event_ids") or [])
        referenced_validation_issue_ids = list(
            fixture.get("referenced_validation_issue_ids") or []
        )

        if decision == "applied":
            if not findings:
                raise ValueError(f"{fixture_id}: applied audit feedback needs audit_findings")
            if not operations:
                raise ValueError(f"{fixture_id}: applied audit feedback needs patch_operations")
            first_finding = findings[0]
            suggestion_paths: list[tuple[str, str]] = []
            suggestions: list[CaseFileChatSuggestionCandidateV2] = []
            for operation in operations:
                top_level = _top_level_field(operation["field_path"])
                if top_level is None:
                    message = (
                        f"{fixture_id}: invalid field path {operation['field_path']!r}"
                    )
                    raise ValueError(message)
                suggestion_paths.append((operation["target_object_id"], top_level))
                suggestions.append(
                    _audit_suggestion(
                        operation["target_object_id"],
                        operation["field_path"],
                        operation.get("new_value"),
                        str(operation.get("reason") or f"[{fixture_id}] 已采纳补丁。"),
                        finding_ref=first_finding.finding_id,
                    )
                )
            expectations = ChatOutcomeExpectations(
                expected_primary_intent="logic_audit",
                required_audit_finding_kinds=tuple(
                    dict.fromkeys(finding.kind for finding in findings)
                ),
                required_audit_evidence_object_ids=tuple(
                    first_finding.evidence_object_ids
                ),
                required_audit_evidence_event_ids=tuple(first_finding.evidence_event_ids),
                required_audit_evidence_validation_issue_ids=tuple(
                    first_finding.evidence_validation_issue_ids
                ),
                required_suggestions=tuple(
                    _expected_suggestion(object_id, f"/{path}")
                    for object_id, path in suggestion_paths
                ),
                audit_finding_count_range=(len(findings), len(findings)),
                suggestion_count_range=(len(suggestions), len(suggestions)),
                requires_suggestion=True,
                simulate_suggestions=True,
            )
            reference_candidate = _audit_candidate(
                str(fixture.get("answer") or f"{decision_label}审计反馈基准回复。"),
                object_ids=tuple(referenced_object_ids),
                event_ids=tuple(referenced_event_ids),
                validation_issue_ids=tuple(referenced_validation_issue_ids),
                findings=findings,
                suggestions=tuple(suggestions),
            )
        else:
            expectations = ChatOutcomeExpectations(
                expected_primary_intent="logic_audit",
                audit_finding_count_range=(0, 0),
                suggestion_count_range=(0, 0),
                no_unnecessary_suggestions=True,
            )
            reference_candidate = _audit_candidate(
                f"{decision_label}反馈已回流：该冻结输入不再提出发现或补丁。",
                object_ids=tuple(referenced_object_ids),
                event_ids=tuple(referenced_event_ids),
                validation_issue_ids=tuple(referenced_validation_issue_ids),
            )
        tasks.append(
            ChatOutcomeTask(
                task_id=task_id,
                message=f"[审计反馈·{decision_label}] {message}",
                hint=hint,
                focus=focus_values,
                casefile=casefile,
                validation_issues=validation_issues,
                kind="feedback",
                expectations=expectations,
                reference_candidate=reference_candidate,
            )
        )
    return tuple(tasks)


def _mutated_candidate(
    base: CaseFileChatCandidate | CaseFileChatCandidateV2,
    *,
    answer: str | None = None,
    object_ids: tuple[str, ...] | None = None,
    event_ids: tuple[str, ...] | None = None,
    validation_issue_ids: tuple[str, ...] | None = None,
    suggestions: tuple[
        CaseFileChatSuggestionCandidate | CaseFileChatSuggestionCandidateV2, ...
    ]
    | None = None,
) -> CaseFileChatCandidate:
    normalized_suggestions: list[CaseFileChatSuggestionCandidate] = []
    for suggestion in base.suggestions if suggestions is None else suggestions:
        if isinstance(suggestion, CaseFileChatSuggestionCandidate):
            normalized_suggestions.append(suggestion)
        else:
            normalized_suggestions.append(
                CaseFileChatSuggestionCandidate(
                    object_id=suggestion.object_id,
                    path=suggestion.path,
                    value_json=suggestion.value_json,
                    reason=suggestion.reason,
                )
            )
    return CaseFileChatCandidate(
        answer=base.answer if answer is None else answer,
        referenced_object_ids=(
            list(base.referenced_object_ids) if object_ids is None else list(object_ids)
        ),
        referenced_event_ids=(
            list(base.referenced_event_ids) if event_ids is None else list(event_ids)
        ),
        referenced_validation_issue_ids=(
            list(base.referenced_validation_issue_ids)
            if validation_issue_ids is None
            else list(validation_issue_ids)
        ),
        suggested_view=base.suggested_view,
        suggestions=normalized_suggestions,
    )


def build_grader_mutations() -> tuple[ChatOutcomeMutation, ...]:
    """Negative controls covering references, patches, values, and answers."""

    tasks = {task.task_id: task for task in build_outcome_tasks()}
    lucy_question = tasks["golden-entity-question"]
    lucy_edit = tasks["golden-edit-description"]
    lucy_suggestion = lucy_edit.reference_candidate.suggestions[0]
    return (
        ChatOutcomeMutation(
            "dangling-object-reference",
            "adversarial-dangling-object-ref",
            _mutated_candidate(
                tasks["adversarial-dangling-object-ref"].reference_candidate,
                object_ids=("ent_none",),
            ),
            "forbidden_reference",
        ),
        ChatOutcomeMutation(
            "event-id-in-object-channel",
            "golden-entity-question",
            _mutated_candidate(
                lucy_question.reference_candidate,
                object_ids=("evt_restart",),
            ),
            "reference_precision",
        ),
        ChatOutcomeMutation(
            "dangling-validation-issue",
            "golden-issue-explanation",
            _mutated_candidate(
                tasks["golden-issue-explanation"].reference_candidate,
                validation_issue_ids=("validator:missing",),
                object_ids=(),
                event_ids=(),
            ),
            "reference_precision",
        ),
        ChatOutcomeMutation(
            "whitelist-violating-path",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(_suggestion("ent_lucy", "/id", "ent_new", "改成新 id"),),
            ),
            "suggestion_legality",
        ),
        ChatOutcomeMutation(
            "markdown-value-json",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(
                    CaseFileChatSuggestionCandidate(
                        object_id="ent_lucy",
                        path="/description",
                        value_json='```json\n"被围栏包裹"\n```',
                        reason="使用 Markdown 围栏。",
                    ),
                ),
            ),
            "suggestion_legality",
        ),
        ChatOutcomeMutation(
            "non-json-value",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(
                    CaseFileChatSuggestionCandidate(
                        object_id="ent_lucy",
                        path="/description",
                        value_json="没有引号的描述文本",
                        reason="值不是合法 JSON。",
                    ),
                ),
            ),
            "suggestion_legality",
        ),
        ChatOutcomeMutation(
            "unnecessary-suggestion-on-question",
            "golden-entity-question",
            _mutated_candidate(
                lucy_question.reference_candidate,
                suggestions=(lucy_suggestion,),
            ),
            "unnecessary_suggestion",
        ),
        ChatOutcomeMutation(
            "unnecessary-suggestion-on-analysis",
            "golden-analysis-inspect",
            _mutated_candidate(
                tasks["golden-analysis-inspect"].reference_candidate,
                suggestions=(lucy_suggestion,),
            ),
            "unnecessary_suggestion",
        ),
        ChatOutcomeMutation(
            "duplicate-reference",
            "golden-entity-question",
            _mutated_candidate(
                lucy_question.reference_candidate,
                object_ids=("ent_lucy", "ent_lucy"),
            ),
            "duplicate_reference",
        ),
        ChatOutcomeMutation(
            "blank-answer",
            "golden-entity-question",
            _mutated_candidate(lucy_question.reference_candidate, answer="   "),
            "blank_answer",
        ),
        ChatOutcomeMutation(
            "raw-json-answer",
            "golden-entity-question",
            _mutated_candidate(
                lucy_question.reference_candidate,
                answer='{"ok": true}',
            ),
            "blank_answer",
        ),
        ChatOutcomeMutation(
            "missing-required-suggestion",
            "golden-edit-description",
            _mutated_candidate(lucy_edit.reference_candidate, suggestions=()),
            "missing_required_suggestion",
        ),
        ChatOutcomeMutation(
            "wrong-required-suggestion-value",
            "golden-edit-event-title",
            _mutated_candidate(
                tasks["golden-edit-event-title"].reference_candidate,
                suggestions=(
                    _suggestion("evt_restart", "/title", "深夜重启", "使用了错误标题。"),
                ),
            ),
            "suggestion_value_mismatch",
        ),
        ChatOutcomeMutation(
            "noop-suggestion-value",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(
                    _suggestion(
                        "ent_lucy",
                        "/description",
                        "Lucy 是唯一能够揭开午夜重启真相的天才研究员。",
                        "错误地重复当前值。",
                    ),
                ),
            ),
            "suggestion_legality",
        ),
        ChatOutcomeMutation(
            "extra-suggestion-target",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(
                    lucy_suggestion,
                    _suggestion("evt_restart", "/title", "午夜例行重启", "额外目标。"),
                ),
            ),
            "unexpected_suggestion",
        ),
        ChatOutcomeMutation(
            "duplicate-suggestion-target",
            "golden-edit-description",
            _mutated_candidate(
                lucy_edit.reference_candidate,
                suggestions=(lucy_suggestion, lucy_suggestion),
            ),
            "suggestion_legality",
        ),
    )


def grade_mutation(mutation: ChatOutcomeMutation) -> ChatOutcomeTrialVerdict:
    """Grade a negative control against its Task's actual route."""
    task = next(task for task in build_outcome_tasks() if task.task_id == mutation.task_id)
    reference = grade_reference_solution(task)
    return grade_chat_outcome(
        task,
        mutation.mutated_candidate,
        allow_suggestions=reference.allow_suggestions,
        actual_intent=reference.actual_intent,
        route_source=reference.route_source,
    )


def run_calibration() -> ChatOutcomeCalibrationReport:
    """M0: Reference Solutions all pass; mutations are all caught."""

    tasks = build_outcome_tasks()
    contract_failures = tuple(
        failure for task in tasks for failure in validate_task_contract(task)
    )
    reference_verdicts = tuple(grade_reference_solution(task) for task in tasks)
    reference_failures = tuple(
        verdict.task_id for verdict in reference_verdicts if not verdict.passed
    )
    mutations = build_grader_mutations()
    mutation_verdicts = tuple(grade_mutation(mutation) for mutation in mutations)
    mutation_misses = tuple(
        f"{mutation.mutation_id}:{verdict.task_id}"
        for mutation, verdict in zip(mutations, mutation_verdicts, strict=True)
        if verdict.passed or mutation.expected_failure not in verdict.failures
    )
    return ChatOutcomeCalibrationReport(
        reference_verdicts=reference_verdicts,
        mutation_verdicts=mutation_verdicts,
        mutation_misses=mutation_misses,
        reference_failures=reference_failures,
        contract_failures=contract_failures,
        status=(
            "passed"
            if not contract_failures and not reference_failures and not mutation_misses
            else "failed"
        ),
    )


def _render_report(report: ChatOutcomeCalibrationReport) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="M0 calibration for the CaseFile chat outcome Grader"
    )
    parser.add_argument("--mode", choices=("calibrate",), default="calibrate")
    parser.add_argument("--report-path", type=str)
    arguments = parser.parse_args()
    report = run_calibration()
    rendered = _render_report(report)
    print(rendered)
    if arguments.report_path is not None:
        path = __import__("pathlib").Path(arguments.report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    if report.status != "passed":
        raise SystemExit(2)


__all__ = [
    "GOLDEN_MARKER_TARGET",
    "REFERENCE_PRECISION_TARGET",
    "REFERENCE_RECALL_TARGET",
    "SUGGESTION_LEGALITY_TARGET",
    "ChatOutcomeCalibrationReport",
    "ChatOutcomeExpectations",
    "ExpectedSuggestion",
    "ChatOutcomeMutation",
    "ChatOutcomeTask",
    "ChatOutcomeThresholds",
    "ChatOutcomeTrialVerdict",
    "build_grader_mutations",
    "build_outcome_tasks",
    "build_outcome_tasks_from_audit_feedback",
    "grade_chat_outcome",
    "grade_mutation",
    "grade_reference_solution",
    "resolve_task_route",
    "run_calibration",
    "validate_task_contract",
]

if __name__ == "__main__":
    main()
