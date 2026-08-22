"""Stable application import façade for the pure verification domain engine."""

from casefile.domain.verification_engine import (
    BatchSimulation,
    FindingRef,
    FindingSeverity,
    ImpactPlanner,
    ImpactSummary,
    MutationSimulation,
    OperationDelta,
    PatchOperation,
    VerificationEngine,
    VerificationFinding,
    VerificationResult,
)

__all__ = [
    "BatchSimulation",
    "FindingRef",
    "FindingSeverity",
    "ImpactPlanner",
    "ImpactSummary",
    "MutationSimulation",
    "OperationDelta",
    "PatchOperation",
    "VerificationEngine",
    "VerificationFinding",
    "VerificationResult",
]
