"""Public contracts for M3.3 bounded closure repair."""

from casefile.domain.logical_mutation.repair.assessment import assess_closure_repair
from casefile.domain.logical_mutation.repair.context import (
    REPAIR_CONTEXT_V1,
    REPAIR_CONTEXT_V2,
    RepairContextError,
    build_closure_repair_context,
)
from casefile.domain.logical_mutation.repair.document_diff import (
    RepairDocumentDiffError,
    build_mutation_from_document_diff,
)
from casefile.domain.logical_mutation.repair.engine import (
    MAX_REPAIR_ROUNDS,
    RepairEngineError,
    prove_repair_rebase,
    run_closure_repair,
)
from casefile.domain.logical_mutation.repair.models import (
    ClosureObligation,
    ClosureRepairAssessment,
    ClosureRepairContextV1,
    ClosureRepairContextV2,
    ClosureRepairResult,
    ClosureRepairRound,
    CompanionRepairOperation,
    ProtectedRepairPath,
    RepairAllowedWrite,
    RepairAutomation,
    RepairContextObject,
    RepairObjectPaths,
    RepairPolicy,
    RepairProposal,
    RepairRunStatus,
    RepairScopeV1,
    RepairUpdateOperation,
    ScopedRepairObligation,
)
from casefile.domain.logical_mutation.repair.policy import (
    REPAIR_POLICY_V1,
    SUPPORTED_REPAIR_POLICY_VERSIONS,
    repair_policies,
    repair_policy,
    validate_repair_policy_version,
)
from casefile.domain.logical_mutation.repair.proposal import RepairProposer
from casefile.domain.logical_mutation.repair.scope import (
    REPAIR_SCOPE_V1,
    RepairScopeError,
    build_repair_scope,
)

__all__ = [
    "MAX_REPAIR_ROUNDS",
    "REPAIR_POLICY_V1",
    "REPAIR_CONTEXT_V1",
    "REPAIR_CONTEXT_V2",
    "REPAIR_SCOPE_V1",
    "SUPPORTED_REPAIR_POLICY_VERSIONS",
    "ClosureObligation",
    "ClosureRepairAssessment",
    "ClosureRepairContextV1",
    "ClosureRepairContextV2",
    "ClosureRepairResult",
    "ClosureRepairRound",
    "CompanionRepairOperation",
    "ProtectedRepairPath",
    "RepairAllowedWrite",
    "RepairAutomation",
    "RepairContextError",
    "RepairContextObject",
    "RepairDocumentDiffError",
    "RepairEngineError",
    "RepairObjectPaths",
    "RepairPolicy",
    "RepairProposal",
    "RepairProposer",
    "RepairRunStatus",
    "RepairScopeError",
    "RepairScopeV1",
    "RepairUpdateOperation",
    "ScopedRepairObligation",
    "assess_closure_repair",
    "build_closure_repair_context",
    "build_mutation_from_document_diff",
    "build_repair_scope",
    "prove_repair_rebase",
    "repair_policies",
    "repair_policy",
    "run_closure_repair",
    "validate_repair_policy_version",
]
