"""Typed application inputs independent of HTTP request models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProjectCreate:
    title: str
    description: str | None = None
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectUpdate:
    title: str | None = None
    description: str | None = None
    profile: dict[str, Any] | None = None
