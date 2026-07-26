"""entity_information_content

Revision ID: 20260726131014
Revises: 20260726131012
Create Date: 2026-07-26 13:10:15.322591
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726131014"
down_revision: str | None = "20260726131012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "traits_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "attributes_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_kind IN ('person', 'location', 'organization', 'object', 'concept', 'other')",
            name=op.f("ck_entities_entity_kind_allowed"),
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_entities_name_not_blank")),
        sa.CheckConstraint(
            "jsonb_typeof(traits_jsonb) = 'array'", name=op.f("ck_entities_traits_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_jsonb) = 'object'",
            name=op.f("ck_entities_attributes_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_entities_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "object_registry_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_entities_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
        sa.UniqueConstraint("object_registry_id", name="uq_entities_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_entities_lineage_id"
        ),
    )
    op.create_index("ix_entities_draft_id_entity_kind", "entities", ["draft_id", "entity_kind"])

    op.create_table(
        "people",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_people_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_people_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
        sa.UniqueConstraint("entity_id", name="uq_people_entity_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_people_lineage_id"
        ),
    )

    op.create_table(
        "locations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "geo_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "movement_rules_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(geo_jsonb) = 'object'", name=op.f("ck_locations_geo_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(movement_rules_jsonb) = 'object'",
            name=op.f("ck_locations_movement_rules_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_locations_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_locations_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("entity_id", name="uq_locations_entity_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_locations_lineage_id"
        ),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("start_time_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("end_time_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("narrative_order", sa.Integer(), nullable=False),
        sa.Column("narrative_phase_id", sa.BigInteger(), nullable=True),
        sa.Column("location_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=20),
            server_default=sa.text("'restricted'"),
            nullable=False,
        ),
        sa.Column(
            "truth_status",
            sa.String(length=20),
            server_default=sa.text("'uncertain'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_events_title_not_blank")),
        sa.CheckConstraint(
            "start_time_jsonb IS NULL OR jsonb_typeof(start_time_jsonb) = 'object'",
            name=op.f("ck_events_start_time_is_object"),
        ),
        sa.CheckConstraint(
            "end_time_jsonb IS NULL OR jsonb_typeof(end_time_jsonb) = 'object'",
            name=op.f("ck_events_end_time_is_object"),
        ),
        sa.CheckConstraint("narrative_order >= 1", name=op.f("ck_events_narrative_order_positive")),
        sa.CheckConstraint(
            "visibility IN ('public', 'restricted', 'hidden')",
            name=op.f("ck_events_visibility_allowed"),
        ),
        sa.CheckConstraint(
            "truth_status IN ('true', 'false', 'uncertain', 'disputed')",
            name=op.f("ck_events_truth_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_events_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "object_registry_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_events_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "location_id"],
            ["locations.project_id", "locations.casefile_id", "locations.draft_id", "locations.id"],
            name="fk_events_location",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("object_registry_id", name="uq_events_object_registry_id"),
        sa.UniqueConstraint("draft_id", "narrative_order", name="uq_events_draft_narrative_order"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_events_lineage_id"
        ),
    )
    op.create_index(
        "ix_events_draft_id_narrative_phase_id", "events", ["draft_id", "narrative_phase_id"]
    )
    op.create_index("ix_events_draft_id_location_id", "events", ["draft_id", "location_id"])

    op.create_table(
        "information_units",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("information_kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("source_credibility", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("visible_from_phase_id", sa.BigInteger(), nullable=True),
        sa.Column("is_misleading", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "information_kind IN ('evidence', 'testimony', 'document', 'observation', "
            "'clue', 'other')",
            name=op.f("ck_information_units_information_kind_allowed"),
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_information_units_title_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(body_text)) > 0", name=op.f("ck_information_units_body_not_blank")
        ),
        sa.CheckConstraint(
            "source_credibility IS NULL OR source_credibility BETWEEN 0 AND 1",
            name=op.f("ck_information_units_source_credibility_range"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=op.f("ck_information_units_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_information_units_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "object_registry_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_information_units_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_information_units")),
        sa.UniqueConstraint("object_registry_id", name="uq_information_units_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_information_units_lineage_id"
        ),
    )
    op.create_index(
        "ix_information_units_draft_id_kind", "information_units", ["draft_id", "information_kind"]
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("information_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=40), nullable=False),
        sa.Column("source_event_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_kind ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_evidence_items_evidence_kind_format"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_evidence_items_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "source_event_id"],
            ["events.project_id", "events.casefile_id", "events.draft_id", "events.id"],
            name="fk_evidence_items_source_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_items")),
        sa.UniqueConstraint("information_unit_id", name="uq_evidence_items_information_unit_id"),
    )

    op.create_table(
        "testimonies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("information_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("speaker_person_id", sa.BigInteger(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("audio_asset_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(quote_text)) > 0", name=op.f("ck_testimonies_quote_not_blank")
        ),
        sa.CheckConstraint(
            "audio_asset_ref IS NULL OR length(btrim(audio_asset_ref)) > 0",
            name=op.f("ck_testimonies_audio_asset_ref_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_testimonies_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "speaker_person_id"],
            ["people.project_id", "people.casefile_id", "people.draft_id", "people.id"],
            name="fk_testimonies_speaker_person",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_testimonies")),
        sa.UniqueConstraint("information_unit_id", name="uq_testimonies_information_unit_id"),
    )


def downgrade() -> None:
    op.drop_table("testimonies")
    op.drop_table("evidence_items")
    op.drop_index("ix_information_units_draft_id_kind", table_name="information_units")
    op.drop_table("information_units")
    op.drop_index("ix_events_draft_id_location_id", table_name="events")
    op.drop_index("ix_events_draft_id_narrative_phase_id", table_name="events")
    op.drop_table("events")
    op.drop_table("locations")
    op.drop_table("people")
    op.drop_index("ix_entities_draft_id_entity_kind", table_name="entities")
    op.drop_table("entities")
