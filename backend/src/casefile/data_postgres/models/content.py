"""Normalized narrative, entity, information, claim, and knowledge-state models."""

from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


def _draft_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id"],
        ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _object_fk(column: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id", column],
        [
            "casefile_objects.project_id",
            "casefile_objects.casefile_id",
            "casefile_objects.draft_id",
            "casefile_objects.id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


class NarrativePhase(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """An ordered phase controlling narrative disclosure."""

    __tablename__ = "narrative_phases"
    __table_args__ = (
        _draft_fk("fk_narrative_phases_draft"),
        _object_fk("object_registry_id", "fk_narrative_phases_object"),
        UniqueConstraint("object_registry_id", name="uq_narrative_phases_object_registry_id"),
        UniqueConstraint("draft_id", "phase_order", name="uq_narrative_phases_draft_order"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_narrative_phases_lineage_id"
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("phase_order >= 1", name="phase_order_positive"),
        CheckConstraint(
            "jsonb_typeof(release_rule_jsonb) = 'object'", name="release_rule_is_object"
        ),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="status_allowed"),
        Index("ix_narrative_phases_draft_id_status", "draft_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    release_rule_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))


class Entity(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A typed real-world or conceptual entity in the current Draft."""

    __tablename__ = "entities"
    __table_args__ = (
        _draft_fk("fk_entities_draft"),
        _object_fk("object_registry_id", "fk_entities_object"),
        UniqueConstraint("object_registry_id", name="uq_entities_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_entities_lineage_id"
        ),
        CheckConstraint(
            "entity_kind IN ('person', 'location', 'organization', 'object', 'concept', 'other')",
            name="entity_kind_allowed",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("jsonb_typeof(traits_jsonb) = 'array'", name="traits_is_array"),
        CheckConstraint("jsonb_typeof(attributes_jsonb) = 'object'", name="attributes_is_object"),
        Index("ix_entities_draft_id_entity_kind", "draft_id", "entity_kind"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    traits_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attributes_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Person(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The one-to-one person extension of an Entity."""

    __tablename__ = "people"
    __table_args__ = (
        _draft_fk("fk_people_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_people_entity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("entity_id", name="uq_people_entity_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_people_lineage_id"
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str | None] = mapped_column(String(120))
    background: Mapped[str | None] = mapped_column(Text)


class Location(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The one-to-one location extension of an Entity."""

    __tablename__ = "locations"
    __table_args__ = (
        _draft_fk("fk_locations_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_locations_entity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("entity_id", name="uq_locations_entity_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_locations_lineage_id"
        ),
        CheckConstraint("jsonb_typeof(geo_jsonb) = 'object'", name="geo_is_object"),
        CheckConstraint(
            "jsonb_typeof(movement_rules_jsonb) = 'object'", name="movement_rules_is_object"
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    geo_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    movement_rules_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Event(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A chronologically and narratively ordered event."""

    __tablename__ = "events"
    __table_args__ = (
        _draft_fk("fk_events_draft"),
        _object_fk("object_registry_id", "fk_events_object"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "narrative_phase_id"],
            [
                "narrative_phases.project_id",
                "narrative_phases.casefile_id",
                "narrative_phases.draft_id",
                "narrative_phases.id",
            ],
            name="fk_events_narrative_phase",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "location_id"],
            ["locations.project_id", "locations.casefile_id", "locations.draft_id", "locations.id"],
            name="fk_events_location",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("object_registry_id", name="uq_events_object_registry_id"),
        UniqueConstraint("draft_id", "narrative_order", name="uq_events_draft_narrative_order"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_events_lineage_id"
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "start_time_jsonb IS NULL OR jsonb_typeof(start_time_jsonb) = 'object'",
            name="start_time_is_object",
        ),
        CheckConstraint(
            "end_time_jsonb IS NULL OR jsonb_typeof(end_time_jsonb) = 'object'",
            name="end_time_is_object",
        ),
        CheckConstraint("narrative_order >= 1", name="narrative_order_positive"),
        CheckConstraint(
            "visibility IN ('public', 'restricted', 'hidden')", name="visibility_allowed"
        ),
        CheckConstraint(
            "truth_status IN ('true', 'false', 'uncertain', 'disputed')",
            name="truth_status_allowed",
        ),
        Index("ix_events_draft_id_narrative_phase_id", "draft_id", "narrative_phase_id"),
        Index("ix_events_draft_id_location_id", "draft_id", "location_id"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    start_time_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    end_time_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    narrative_order: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative_phase_id: Mapped[int | None] = mapped_column(BigInteger)
    location_id: Mapped[int | None] = mapped_column(BigInteger)
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'restricted'")
    )
    truth_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'uncertain'")
    )


class InformationUnit(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A typed unit of evidence, testimony, or other case information."""

    __tablename__ = "information_units"
    __table_args__ = (
        _draft_fk("fk_information_units_draft"),
        _object_fk("object_registry_id", "fk_information_units_object"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "visible_from_phase_id"],
            [
                "narrative_phases.project_id",
                "narrative_phases.casefile_id",
                "narrative_phases.draft_id",
                "narrative_phases.id",
            ],
            name="fk_information_units_visible_phase",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("object_registry_id", name="uq_information_units_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_information_units_lineage_id"
        ),
        CheckConstraint(
            "information_kind IN ('evidence', 'testimony', 'document', 'observation', "
            "'clue', 'other')",
            name="information_kind_allowed",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(body_text)) > 0", name="body_not_blank"),
        CheckConstraint(
            "source_credibility IS NULL OR source_credibility BETWEEN 0 AND 1",
            name="source_credibility_range",
        ),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="status_allowed"),
        Index("ix_information_units_draft_id_kind", "draft_id", "information_kind"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    information_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_credibility: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    visible_from_phase_id: Mapped[int | None] = mapped_column(BigInteger)
    is_misleading: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))


class EvidenceItem(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The one-to-one evidence extension of an Information Unit."""

    __tablename__ = "evidence_items"
    __table_args__ = (
        _draft_fk("fk_evidence_items_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "information_unit_id"],
            [
                "information_units.project_id",
                "information_units.casefile_id",
                "information_units.draft_id",
                "information_units.id",
            ],
            name="fk_evidence_items_information_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "source_event_id"],
            ["events.project_id", "events.casefile_id", "events.draft_id", "events.id"],
            name="fk_evidence_items_source_event",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("information_unit_id", name="uq_evidence_items_information_unit_id"),
        CheckConstraint("evidence_kind ~ '^[a-z][a-z0-9_]*$'", name="evidence_kind_format"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    information_unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_event_id: Mapped[int | None] = mapped_column(BigInteger)


class Testimony(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The one-to-one testimony extension of an Information Unit."""

    __tablename__ = "testimonies"
    __table_args__ = (
        _draft_fk("fk_testimonies_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "information_unit_id"],
            [
                "information_units.project_id",
                "information_units.casefile_id",
                "information_units.draft_id",
                "information_units.id",
            ],
            name="fk_testimonies_information_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "speaker_person_id"],
            ["people.project_id", "people.casefile_id", "people.draft_id", "people.id"],
            name="fk_testimonies_speaker_person",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("information_unit_id", name="uq_testimonies_information_unit_id"),
        CheckConstraint("length(btrim(quote_text)) > 0", name="quote_not_blank"),
        CheckConstraint(
            "audio_asset_ref IS NULL OR length(btrim(audio_asset_ref)) > 0",
            name="audio_asset_ref_not_blank",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    information_unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    speaker_person_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quote_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_asset_ref: Mapped[str | None] = mapped_column(String(512))


class Claim(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A proposition whose support state is evaluated by case information."""

    __tablename__ = "claims"
    __table_args__ = (
        _draft_fk("fk_claims_draft"),
        _object_fk("object_registry_id", "fk_claims_object"),
        UniqueConstraint("object_registry_id", name="uq_claims_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_claims_lineage_id"
        ),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_not_blank"),
        CheckConstraint(
            "status IN ('unresolved', 'supported', 'refuted', 'disputed')",
            name="status_allowed",
        ),
        Index("ix_claims_draft_id_status", "draft_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unresolved'")
    )


class KnowledgeState(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """An Entity's aggregate knowledge state at one narrative phase."""

    __tablename__ = "knowledge_states"
    __table_args__ = (
        _draft_fk("fk_knowledge_states_draft"),
        _object_fk("object_registry_id", "fk_knowledge_states_object"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_knowledge_states_entity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "narrative_phase_id"],
            [
                "narrative_phases.project_id",
                "narrative_phases.casefile_id",
                "narrative_phases.draft_id",
                "narrative_phases.id",
            ],
            name="fk_knowledge_states_phase",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("object_registry_id", name="uq_knowledge_states_object_registry_id"),
        UniqueConstraint(
            "draft_id", "entity_id", "narrative_phase_id", name="uq_knowledge_states_entity_phase"
        ),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_knowledge_states_lineage_id"
        ),
        CheckConstraint(
            "status IN ('unknown', 'partial', 'known', 'misinformed')", name="status_allowed"
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    narrative_phase_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class KnowledgeStateEntry(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One Information Unit's cognitive and disclosure state within a Knowledge State."""

    __tablename__ = "knowledge_state_entries"
    __table_args__ = (
        _draft_fk("fk_knowledge_state_entries_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "knowledge_state_id"],
            [
                "knowledge_states.project_id",
                "knowledge_states.casefile_id",
                "knowledge_states.draft_id",
                "knowledge_states.id",
            ],
            name="fk_knowledge_state_entries_state",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "information_unit_id"],
            [
                "information_units.project_id",
                "information_units.casefile_id",
                "information_units.draft_id",
                "information_units.id",
            ],
            name="fk_knowledge_state_entries_information",
            ondelete="RESTRICT",
        ),
        _object_fk("acquired_from_object_id", "fk_knowledge_state_entries_source_object"),
        UniqueConstraint(
            "knowledge_state_id",
            "information_unit_id",
            name="uq_knowledge_entries_state_information",
        ),
        UniqueConstraint(
            "knowledge_state_id", "ordinal", name="uq_knowledge_entries_state_ordinal"
        ),
        CheckConstraint(
            "cognition_status IN ('unknown', 'suspected', 'known', 'believed', 'disbelieved')",
            name="cognition_status_allowed",
        ),
        CheckConstraint(
            "disclosure_status IN ('hidden', 'available', 'revealed')",
            name="disclosure_status_allowed",
        ),
        CheckConstraint("certainty IS NULL OR certainty BETWEEN 0 AND 1", name="certainty_range"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    knowledge_state_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    information_unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cognition_status: Mapped[str] = mapped_column(String(20), nullable=False)
    disclosure_status: Mapped[str] = mapped_column(String(20), nullable=False)
    acquired_from_object_id: Mapped[int | None] = mapped_column(BigInteger)
    certainty: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
