"""Strict HTTP request models for the first typed write slice."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.application.commands import EntityWrite, EventWrite, ProjectCreate


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreateRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)

    def command(self) -> ProjectCreate:
        return ProjectCreate(self.title, self.description, self.profile)


class ProjectUpdateRequest(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    profile: dict[str, Any] | None = None


class PersonInput(StrictRequest):
    role: str | None = Field(default=None, max_length=120)
    background: str | None = None


class LocationInput(StrictRequest):
    geo: dict[str, Any] = Field(default_factory=dict)
    movement_rules: dict[str, Any] = Field(default_factory=dict)


class EntityWriteRequest(StrictRequest):
    entity_kind: Literal["person", "location"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    traits: list[Any] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    person: PersonInput | None = None
    location: LocationInput | None = None

    @model_validator(mode="after")
    def subtype_matches(self) -> Self:
        if self.entity_kind == "person":
            if self.person is None or self.location is not None:
                raise ValueError("person entities require only the person extension")
        elif self.location is None or self.person is not None:
            raise ValueError("location entities require only the location extension")
        return self

    def command(self) -> EntityWrite:
        return EntityWrite(
            entity_kind=self.entity_kind,
            name=self.name,
            description=self.description,
            traits=self.traits,
            attributes=self.attributes,
            confidence=self.confidence,
            role=None if self.person is None else self.person.role,
            background=None if self.person is None else self.person.background,
            geo={} if self.location is None else self.location.geo,
            movement_rules={} if self.location is None else self.location.movement_rules,
        )


class EventWriteRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    start_time: dict[str, Any] | None = None
    end_time: dict[str, Any] | None = None
    narrative_order: int = Field(ge=1)
    narrative_phase_object_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,127}$")
    location_object_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,127}$")
    visibility: Literal["public", "restricted", "hidden"] = "restricted"
    truth_status: Literal["true", "false", "uncertain", "disputed"] = "uncertain"
    confidence: float | None = Field(default=None, ge=0, le=1)

    def command(self) -> EventWrite:
        return EventWrite(
            title=self.title,
            summary=self.summary,
            start_time=self.start_time,
            end_time=self.end_time,
            narrative_order=self.narrative_order,
            narrative_phase_object_id=self.narrative_phase_object_id,
            location_object_id=self.location_object_id,
            visibility=self.visibility,
            truth_status=self.truth_status,
            confidence=self.confidence,
        )


class ReferenceReplaceRequest(StrictRequest):
    object_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_object_ids(self) -> Self:
        import re

        pattern = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
        if any(pattern.fullmatch(object_id) is None for object_id in self.object_ids):
            raise ValueError("every object_id must use lower snake_case")
        return self


class ProviderSettingRequest(StrictRequest):
    provider: Literal["openai", "deepseek"] = "openai"
    api_key: str = Field(min_length=8, max_length=512)
    model_id: str = Field(default="gpt-5.6-sol", min_length=1, max_length=160)
    model_is_custom: bool = False

    @model_validator(mode="after")
    def provider_default_model(self) -> Self:
        if (
            self.provider == "deepseek"
            and self.model_id == "gpt-5.6-sol"
            and not self.model_is_custom
        ):
            self.model_id = "deepseek-v4-flash"
        return self


class BriefUpdateRequest(StrictRequest):
    expected_revision: int = Field(ge=1)
    content: dict[str, Any]


class BriefConfirmRequest(StrictRequest):
    expected_revision: int = Field(ge=1)


class SourceRecordCreateRequest(StrictRequest):
    source_kind: Literal["human_original", "human_revision"]
    content_text: str = Field(min_length=1)
    parent_source_record_id: int | None = Field(default=None, ge=1)


class BriefPolishTaskRequest(StrictRequest):
    source_record_id: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"


class BriefAnchorExtractTaskRequest(StrictRequest):
    expected_brief_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"


class GenerateTaskRequest(StrictRequest):
    brief_version_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"


class ObjectPatchRequest(StrictRequest):
    expected_revision: int = Field(ge=1)
    changes: dict[str, Any] = Field(min_length=1)
