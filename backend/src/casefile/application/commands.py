"""Typed application inputs independent of HTTP request models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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


@dataclass(frozen=True, slots=True)
class EntityWrite:
    entity_kind: Literal["person", "location"]
    name: str
    description: str | None = None
    traits: list[Any] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    role: str | None = None
    background: str | None = None
    geo: dict[str, Any] = field(default_factory=dict)
    movement_rules: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventWrite:
    title: str
    summary: str | None
    start_time: dict[str, Any] | None
    end_time: dict[str, Any] | None
    narrative_order: int
    narrative_phase_object_id: str | None
    location_object_id: str | None
    visibility: Literal["public", "restricted", "hidden"]
    truth_status: Literal["true", "false", "uncertain", "disputed"]
    confidence: float | None = None
