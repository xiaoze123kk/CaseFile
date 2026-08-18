"""M3-C1: casefile-chat-output-v2 contract and server-side finding validation."""

from __future__ import annotations

import pytest
from casefile.agent_runtime.chat_audit_validation import (
    audit_findings_suppressed_for,
    normalize_audit_findings,
    route_primary_intent,
)
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatSuggestionCandidateV2,
)
from casefile.agent_runtime.prompt_package import OUTPUT_SCHEMAS
from pydantic import ValidationError


def make_finding(**overrides: object) -> dict:
    finding = {
        "finding_id": "F1",
        "kind": "contradiction",
        "severity": "S2",
        "title": "描述冲突",
        "statement": "对象 A 与对象 B 的描述互相矛盾。",
        "needs_manual_review": False,
        "evidence_object_ids": ["ent_lucy", "ent_researcher"],
        "evidence_event_ids": ["evt_dinner"],
        "evidence_validation_issue_ids": ["iss_1"],
    }
    finding.update(overrides)
    return finding


def test_v2_candidate_carries_audit_findings_and_binds_suggestions() -> None:
    candidate = CaseFileChatCandidateV2.model_validate(
        {
            "answer": "发现一处矛盾，已给出补丁提案。",
            "referenced_object_ids": ["ent_lucy", "ent_researcher"],
            "referenced_event_ids": ["evt_dinner"],
            "referenced_validation_issue_ids": ["iss_1"],
            "suggestions": [
                {
                    "object_id": "ent_lucy",
                    "path": "/description",
                    "value_json": '"修正后的描述"',
                    "reason": "[漏洞#F1] 描述冲突修复",
                    "finding_ref": "F1",
                }
            ],
            "audit_findings": [make_finding()],
        }
    )

    assert candidate.audit_findings[0].finding_id == "F1"
    assert candidate.audit_findings[0].kind == "contradiction"
    assert candidate.audit_findings[0].severity == "S2"
    assert candidate.suggestions[0].finding_ref == "F1"


def test_v1_candidate_stays_frozen_without_audit_findings() -> None:
    candidate = CaseFileChatCandidate.model_validate(
        {"answer": "普通问答", "suggestions": []}
    )
    assert not hasattr(candidate, "audit_findings")
    assert "casefile-chat-output-v1" in OUTPUT_SCHEMAS
    assert OUTPUT_SCHEMAS["casefile-chat-output-v1"] is CaseFileChatCandidate
    assert OUTPUT_SCHEMAS["casefile-chat-output-v2"] is CaseFileChatCandidateV2


def test_v2_suggestion_rejects_unknown_finding_ref() -> None:
    with pytest.raises(ValidationError):
        CaseFileChatSuggestionCandidateV2.model_validate(
            {
                "object_id": "ent_lucy",
                "path": "/description",
                "value_json": '"x"',
                "reason": "无来源修复",
                "finding_ref": "missing-ref",
            }
        )


def test_route_intent_and_findings_suppression() -> None:
    assert route_primary_intent(None) is None
    assert audit_findings_suppressed_for(None) is False
    assert (
        audit_findings_suppressed_for(
            {"execution_profile": {"primary_intent": "question"}}
        )
        is True
    )
    assert (
        audit_findings_suppressed_for(
            {"execution_profile": {"primary_intent": "logic_audit"}}
        )
        is False
    )


def test_normalize_audit_findings_dedupes_evidence_and_binds_suggestions() -> None:
    normalized, missing_o, missing_e, missing_i = normalize_audit_findings(
        [make_finding(evidence_object_ids=["ent_lucy", "ent_lucy", "ent_extra"])],
        frozen_object_ids={"ent_lucy", "ent_researcher"},
        frozen_event_ids={"evt_dinner"},
        known_issue_ids={"iss_1"},
        suggestion_finding_refs=["F1"],
    )

    assert normalized[0]["evidence_object_ids"] == ["ent_lucy", "ent_extra"]
    assert missing_o == ["ent_extra"]
    assert missing_e == []
    assert missing_i == []


def test_normalize_audit_findings_rejects_structural_violations() -> None:
    shared = {
        "frozen_object_ids": {"ent_lucy"},
        "frozen_event_ids": {"evt_1"},
        "known_issue_ids": {"iss_1"},
        "suggestion_finding_refs": [],
    }
    with pytest.raises(ValueError, match="audit_finding_id_duplicate"):
        normalize_audit_findings([make_finding(), make_finding()], **shared)
    with pytest.raises(ValueError, match="audit_finding_kind_invalid"):
        normalize_audit_findings([make_finding(kind="style")], **shared)
    with pytest.raises(ValueError, match="audit_finding_severity_invalid"):
        normalize_audit_findings([make_finding(severity="S9")], **shared)
    with pytest.raises(ValueError, match="audit_finding_missing:title"):
        normalize_audit_findings([make_finding(title="")], **shared)


def test_normalize_audit_findings_rejects_unbound_or_manual_refs() -> None:
    with pytest.raises(ValueError, match="audit_finding_ref_unknown"):
        normalize_audit_findings(
            [make_finding()],
            frozen_object_ids={"ent_lucy", "ent_researcher"},
            frozen_event_ids={"evt_dinner"},
            known_issue_ids={"iss_1"},
            suggestion_finding_refs=["F9"],
        )
    with pytest.raises(ValueError, match="audit_finding_ref_manual_review"):
        normalize_audit_findings(
            [make_finding(needs_manual_review=True)],
            frozen_object_ids={"ent_lucy", "ent_researcher"},
            frozen_event_ids={"evt_dinner"},
            known_issue_ids={"iss_1"},
            suggestion_finding_refs=["F1"],
        )


def test_normalize_audit_findings_collects_all_evidence_violations() -> None:
    _, missing_o, missing_e, missing_i = normalize_audit_findings(
        [
            make_finding(
                evidence_event_ids=["evt_ghost"],
                evidence_validation_issue_ids=["iss_ghost"],
            )
        ],
        frozen_object_ids={"ent_lucy", "ent_researcher"},
        frozen_event_ids={"evt_dinner"},
        known_issue_ids={"iss_1"},
        suggestion_finding_refs=[],
    )
    assert missing_o == []
    assert missing_e == ["evt_ghost"]
    assert missing_i == ["iss_ghost"]
