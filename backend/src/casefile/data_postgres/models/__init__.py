"""Import all persistence models so Alembic sees the complete metadata."""

from casefile.data_postgres.models.agent_execution import AgentModelCall, AgentStepRun
from casefile.data_postgres.models.brief_intake import (
    BriefIntake,
    BriefIntakeCandidate,
    BriefIntakeQuestion,
)
from casefile.data_postgres.models.casefile import (
    CaseFile,
    CaseFileContractRef,
    CaseFileObject,
    CaseFileRef,
    Draft,
    DraftOperation,
)
from casefile.data_postgres.models.collaboration import (
    AgentMessage,
    AgentPatchOperation,
    AgentPatchSet,
    AgentThread,
)
from casefile.data_postgres.models.content import (
    Claim,
    Entity,
    Event,
    EvidenceItem,
    InformationUnit,
    KnowledgeState,
    KnowledgeStateEntry,
    Location,
    NarrativePhase,
    Person,
    Relationship,
    Testimony,
)
from casefile.data_postgres.models.context_states import AgentThreadContextState
from casefile.data_postgres.models.exposure import (
    ExposurePlan,
    ExposurePlanEntry,
    ExposurePlanEntryRef,
    ExposurePlanRevision,
)
from casefile.data_postgres.models.idea import IdeaCandidate
from casefile.data_postgres.models.identity import Project, User, UserProviderSetting
from casefile.data_postgres.models.reasoning import (
    CaseFileConstraint,
    Hypothesis,
    ReasoningEdge,
    ReasoningNode,
    ReasoningPath,
    ResolutionSlot,
    ResolutionSpec,
    StructureLock,
)
from casefile.data_postgres.models.reverse_parse import ImportedDocument, ParseItem
from casefile.data_postgres.models.verification import (
    VerificationFinding,
    VerificationFindingPatchOperation,
    VerificationFindingRef,
    VerificationFindingReview,
    VerificationRun,
)
from casefile.data_postgres.models.versioning import AuditEvent, CanonVersion, DraftSnapshot
from casefile.data_postgres.models.workflow import (
    Brief,
    BriefVersion,
    SourceRecord,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)

__all__ = [
    "AgentModelCall",
    "AgentStepRun",
    "AgentMessage",
    "AgentPatchOperation",
    "AgentPatchSet",
    "AgentThread",
    "AgentThreadContextState",
    "AuditEvent",
    "Brief",
    "BriefIntake",
    "BriefIntakeCandidate",
    "BriefIntakeQuestion",
    "BriefVersion",
    "CanonVersion",
    "CaseFile",
    "CaseFileContractRef",
    "CaseFileObject",
    "CaseFileRef",
    "CaseFileConstraint",
    "Claim",
    "Draft",
    "DraftOperation",
    "DraftSnapshot",
    "Entity",
    "Event",
    "EvidenceItem",
    "ExposurePlan",
    "ExposurePlanEntry",
    "ExposurePlanEntryRef",
    "ExposurePlanRevision",
    "Hypothesis",
    "IdeaCandidate",
    "ImportedDocument",
    "InformationUnit",
    "KnowledgeState",
    "KnowledgeStateEntry",
    "Location",
    "NarrativePhase",
    "ParseItem",
    "Person",
    "Project",
    "Relationship",
    "ReasoningEdge",
    "ReasoningNode",
    "ReasoningPath",
    "ResolutionSlot",
    "ResolutionSpec",
    "SourceRecord",
    "StructureLock",
    "Testimony",
    "TaskAttempt",
    "TaskEvent",
    "TaskRun",
    "User",
    "UserProviderSetting",
    "VerificationFinding",
    "VerificationFindingPatchOperation",
    "VerificationFindingRef",
    "VerificationFindingReview",
    "VerificationRun",
]
