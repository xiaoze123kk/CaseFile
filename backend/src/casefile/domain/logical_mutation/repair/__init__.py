"""Public contracts for M3.3 bounded closure repair."""

from casefile.domain.logical_mutation.repair.assessment import assess_closure_repair
from casefile.domain.logical_mutation.repair.context import (
    REPAIR_CONTEXT_V1,
    RepairContextError,
    build_closure_repair_context,
)
from casefile.domain.logical_mutation.repair.models import (
    ClosureObligation,
    ClosureRepairAssessment,
    ClosureRepairContextV1,
    ProtectedRepairPath,
    RepairAutomation,
    RepairContextObject,
    RepairObjectPaths,
    RepairPolicy,
    RepairScopeV1,
    ScopedRepairObligation,
)
from casefile.domain.logical_mutation.repair.policy import (
    REPAIR_POLICY_V1,
    SUPPORTED_REPAIR_POLICY_VERSIONS,
    repair_policies,
    repair_policy,
    validate_repair_policy_version,
)
from casefile.domain.logical_mutation.repair.scope import (
    REPAIR_SCOPE_V1,
    RepairScopeError,
    build_repair_scope,
)

__all__ = [
    "REPAIR_POLICY_V1",
    "REPAIR_CONTEXT_V1",
    "REPAIR_SCOPE_V1",
    "SUPPORTED_REPAIR_POLICY_VERSIONS",
    "ClosureObligation",
    "ClosureRepairAssessment",
    "ClosureRepairContextV1",
    "ProtectedRepairPath",
    "RepairAutomation",
    "RepairContextError",
    "RepairContextObject",
    "RepairObjectPaths",
    "RepairPolicy",
    "RepairScopeError",
    "RepairScopeV1",
    "ScopedRepairObligation",
    "assess_closure_repair",
    "build_closure_repair_context",
    "build_repair_scope",
    "repair_policies",
    "repair_policy",
    "validate_repair_policy_version",
]
