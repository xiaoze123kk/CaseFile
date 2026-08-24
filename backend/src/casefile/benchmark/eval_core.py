"""Small, benchmark-local contracts for task/trial/outcome evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

EvalSuiteKind = Literal["regression", "capability"]
GraderSeverity = Literal["hard", "soft"]


@dataclass(frozen=True, slots=True)
class EvalTask:
    task_id: str
    policy_key: tuple[str, str]
    automation: Literal["agent", "manual", "ineligible"]
    input: Mapping[str, Any]
    oracle: Mapping[str, Any]
    reference_path: str
    tags: tuple[str, ...]
    difficulty: str = "policy"
    topology: str = "policy_probe"
    staged: bool = False


@dataclass(frozen=True, slots=True)
class EvalSuite:
    suite_id: str
    suite_kind: EvalSuiteKind
    schema_version: str
    tasks: tuple[EvalTask, ...]
    fingerprint: str
    suite_role: str = "capability_dev_v1"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Transcript:
    input_summary: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...] = ()
    rounds: tuple[Mapping[str, Any], ...] = ()
    exception: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_summary": dict(self.input_summary),
            "events": [dict(item) for item in self.events],
            "rounds": [dict(item) for item in self.rounds],
            "exception": None if self.exception is None else dict(self.exception),
        }


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    reason_code: str
    provider_invoked: bool
    proof_complete: bool
    patchset_eligible: bool
    round_count: int
    companion_operation_count: int
    changed_object_count: int
    final_rule_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "provider_invoked": self.provider_invoked,
            "proof_complete": self.proof_complete,
            "patchset_eligible": self.patchset_eligible,
            "round_count": self.round_count,
            "companion_operation_count": self.companion_operation_count,
            "changed_object_count": self.changed_object_count,
            "final_rule_codes": list(self.final_rule_codes),
        }


@dataclass(frozen=True, slots=True)
class GraderResult:
    grader_id: str
    severity: GraderSeverity
    passed: bool
    score: float
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grader_id": self.grader_id,
            "severity": self.severity,
            "passed": self.passed,
            "score": self.score,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class TrialRecord:
    trial_id: str
    task_id: str
    trial_index: int
    outcome: Outcome
    transcript: Transcript
    graders: tuple[GraderResult, ...]
    usage: Mapping[str, int]
    latency_ms: float
    infrastructure_failure: Mapping[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.infrastructure_failure is None and all(
            item.passed for item in self.graders if item.severity == "hard"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "trial_index": self.trial_index,
            "passed": self.passed,
            "outcome": self.outcome.as_dict(),
            "transcript": self.transcript.as_dict(),
            "graders": [item.as_dict() for item in self.graders],
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
            "infrastructure_failure": (
                None if self.infrastructure_failure is None else dict(self.infrastructure_failure)
            ),
        }


@dataclass(frozen=True, slots=True)
class SuiteReport:
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


__all__ = [
    "EvalSuite",
    "EvalTask",
    "GraderResult",
    "Outcome",
    "SuiteReport",
    "Transcript",
    "TrialRecord",
]
