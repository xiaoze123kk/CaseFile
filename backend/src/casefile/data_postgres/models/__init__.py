"""Import all persistence models so Alembic sees the complete metadata."""

from casefile.data_postgres.models.casefile import (
    CaseFile,
    CaseFileObject,
    CaseFileRef,
    Draft,
    DraftOperation,
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
    Testimony,
)
from casefile.data_postgres.models.identity import Project, User
from casefile.data_postgres.models.reasoning import (
    CaseFileConstraint,
    Hypothesis,
    ReasoningEdge,
    ReasoningNode,
    ReasoningPath,
    ResolutionSlot,
    ResolutionSpec,
)
from casefile.data_postgres.models.versioning import AuditEvent, CanonVersion, DraftSnapshot

__all__ = [
    "AuditEvent",
    "CanonVersion",
    "CaseFile",
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
    "Hypothesis",
    "InformationUnit",
    "KnowledgeState",
    "KnowledgeStateEntry",
    "Location",
    "NarrativePhase",
    "Person",
    "Project",
    "ReasoningEdge",
    "ReasoningNode",
    "ReasoningPath",
    "ResolutionSlot",
    "ResolutionSpec",
    "Testimony",
    "User",
]
