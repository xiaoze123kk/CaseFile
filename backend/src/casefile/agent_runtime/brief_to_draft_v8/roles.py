"""Read-only specialist role ports reserved for future manager handoffs."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class SpecialistRoleId(StrEnum):
    EVIDENCE_ANALYST = "evidence_analyst"
    LOGIC_DETECTIVE = "logic_detective"
    FORMAT_COMPILER = "format_compiler"
    QA_VERIFIER = "qa_verifier"
    CASE_DIRECTOR = "case_director"


class SpecialistRolePort(Protocol):
    """A bounded, read-only capability; roles cannot write Draft or Canon."""

    role_id: SpecialistRoleId
    enabled: bool

    def inspect(self, payload: BaseModel) -> BaseModel: ...


FORMAT_COMPILER_ENABLED = False
