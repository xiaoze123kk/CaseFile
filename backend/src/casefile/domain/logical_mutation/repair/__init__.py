"""Public contracts for M3.3 bounded closure repair."""

from casefile.domain.logical_mutation.repair.assessment import assess_closure_repair
from casefile.domain.logical_mutation.repair.models import (
    ClosureObligation,
    ClosureRepairAssessment,
    RepairAutomation,
    RepairPolicy,
)
from casefile.domain.logical_mutation.repair.policy import (
    REPAIR_POLICY_V1,
    SUPPORTED_REPAIR_POLICY_VERSIONS,
    repair_policies,
    repair_policy,
    validate_repair_policy_version,
)

__all__ = [
    "REPAIR_POLICY_V1",
    "SUPPORTED_REPAIR_POLICY_VERSIONS",
    "ClosureObligation",
    "ClosureRepairAssessment",
    "RepairAutomation",
    "RepairPolicy",
    "assess_closure_repair",
    "repair_policies",
    "repair_policy",
    "validate_repair_policy_version",
]
