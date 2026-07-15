"""Import all models so Alembic can discover the complete metadata."""

from casefile.data_postgres.models.casefile import (
    CaseFile,
    CaseFileObject,
    CaseFileRef,
    Draft,
    DraftOperation,
    Project,
)
from casefile.data_postgres.models.identity import Membership, User, Workspace, WorkspaceSetting
from casefile.data_postgres.models.versioning import (
    Approval,
    AuditEvent,
    CanonVersion,
    DraftSnapshot,
)

__all__ = [
    "Approval",
    "AuditEvent",
    "CanonVersion",
    "CaseFile",
    "CaseFileObject",
    "CaseFileRef",
    "Draft",
    "DraftOperation",
    "DraftSnapshot",
    "Membership",
    "Project",
    "User",
    "Workspace",
    "WorkspaceSetting",
]
