"""version_audit_triggers

Revision ID: 20260726131019
Revises: 20260726131017
Create Date: 2026-07-26 13:10:19.907985
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726131019"
down_revision: str | None = "20260726131017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPDATED_AT_TABLES = (
    "users",
    "projects",
    "casefiles",
    "drafts",
    "casefile_objects",
    "narrative_phases",
    "entities",
    "people",
    "locations",
    "events",
    "information_units",
    "evidence_items",
    "testimonies",
    "claims",
    "hypotheses",
    "reasoning_paths",
    "reasoning_nodes",
    "reasoning_edges",
    "resolution_specs",
    "resolution_slots",
    "casefile_constraints",
    "knowledge_states",
    "knowledge_state_entries",
)

CORE_CONTENT_TABLES = (
    "narrative_phases",
    "entities",
    "events",
    "information_units",
    "claims",
    "hypotheses",
    "reasoning_paths",
    "resolution_specs",
    "casefile_constraints",
    "knowledge_states",
)


def upgrade() -> None:
    op.create_table(
        "draft_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("snapshot_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "snapshot_revision >= 1", name=op.f("ck_draft_snapshots_revision_positive")
        ),
        sa.CheckConstraint(
            "length(btrim(schema_version)) > 0",
            name=op.f("ck_draft_snapshots_schema_version_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot_jsonb) = 'object'",
            name=op.f("ck_draft_snapshots_content_is_object"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_draft_snapshots_content_hash_format")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_draft_snapshots_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_draft_snapshots_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft_snapshots")),
        sa.UniqueConstraint(
            "draft_id", "snapshot_revision", name="uq_draft_snapshots_draft_id_snapshot_revision"
        ),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "id", name="uq_draft_snapshots_project_id_casefile_id_id"
        ),
    )
    op.create_index(
        "ix_draft_snapshots_casefile_id_created_at",
        "draft_snapshots",
        ["casefile_id", "created_at"],
    )

    op.create_table(
        "canon_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_canon_version_id", sa.BigInteger(), nullable=True),
        sa.Column("source_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("content_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_canon_versions_version_positive")),
        sa.CheckConstraint(
            "length(btrim(schema_version)) > 0",
            name=op.f("ck_canon_versions_schema_version_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content_jsonb) = 'object'",
            name=op.f("ck_canon_versions_content_is_object"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_canon_versions_content_hash_format")
        ),
        sa.CheckConstraint(
            "(version_no = 1 AND parent_canon_version_id IS NULL) OR "
            "(version_no > 1 AND parent_canon_version_id IS NOT NULL)",
            name=op.f("ck_canon_versions_parent_fields_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["users.id"],
            name=op.f("fk_canon_versions_confirmed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_canon_versions_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "parent_canon_version_id"],
            ["canon_versions.project_id", "canon_versions.casefile_id", "canon_versions.id"],
            name="fk_canon_versions_project_casefile_parent_canon_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "source_snapshot_id"],
            ["draft_snapshots.project_id", "draft_snapshots.casefile_id", "draft_snapshots.id"],
            name="fk_canon_versions_project_casefile_source_snapshot_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canon_versions")),
        sa.UniqueConstraint(
            "casefile_id", "version_no", name="uq_canon_versions_casefile_id_version_no"
        ),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "id", name="uq_canon_versions_project_id_casefile_id_id"
        ),
        sa.UniqueConstraint("source_snapshot_id", name="uq_canon_versions_source_snapshot_id"),
    )
    op.create_index(
        "ix_canon_versions_casefile_id_created_at", "canon_versions", ["casefile_id", "created_at"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_ref", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "details_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL) OR "
            "(actor_kind IN ('agent', 'system', 'import') AND actor_user_id IS NULL "
            "AND actor_ref IS NOT NULL AND length(btrim(actor_ref)) > 0)",
            name=op.f("ck_audit_events_actor_shape"),
        ),
        sa.CheckConstraint(
            "action ~ '^[a-z][a-z0-9_.]*$'", name=op.f("ck_audit_events_action_format")
        ),
        sa.CheckConstraint(
            "target_type ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_audit_events_target_type_format")
        ),
        sa.CheckConstraint("target_id >= 1", name=op.f("ck_audit_events_target_id_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(details_jsonb) = 'object'", name=op.f("ck_audit_events_details_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_audit_events_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_audit_events_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_project_id_occurred_at", "audit_events", ["project_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_events_casefile_id_occurred_at", "audit_events", ["casefile_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_events_trace_id",
        "audit_events",
        ["trace_id"],
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )

    op.create_foreign_key(
        "fk_casefiles_project_casefile_current_canon_canon_versions",
        "casefiles",
        "canon_versions",
        ["project_id", "id", "current_canon_version_id"],
        ["project_id", "casefile_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_drafts_project_casefile_base_canon_canon_versions",
        "drafts",
        "canon_versions",
        ["project_id", "casefile_id", "base_canon_version_id"],
        ["project_id", "casefile_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_events_narrative_phase",
        "events",
        "narrative_phases",
        ["project_id", "casefile_id", "draft_id", "narrative_phase_id"],
        ["project_id", "casefile_id", "draft_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_information_units_visible_phase",
        "information_units",
        "narrative_phases",
        ["project_id", "casefile_id", "draft_id", "visible_from_phase_id"],
        ["project_id", "casefile_id", "draft_id", "id"],
        ondelete="RESTRICT",
    )

    _create_integrity_functions()
    _create_integrity_triggers()


def downgrade() -> None:
    _drop_integrity_triggers()
    _drop_integrity_functions()
    op.drop_constraint(
        "fk_information_units_visible_phase", "information_units", type_="foreignkey"
    )
    op.drop_constraint("fk_events_narrative_phase", "events", type_="foreignkey")
    op.drop_constraint(
        "fk_drafts_project_casefile_base_canon_canon_versions", "drafts", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_casefiles_project_casefile_current_canon_canon_versions",
        "casefiles",
        type_="foreignkey",
    )
    op.drop_index("ix_audit_events_trace_id", table_name="audit_events")
    op.drop_index("ix_audit_events_casefile_id_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_project_id_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_canon_versions_casefile_id_created_at", table_name="canon_versions")
    op.drop_table("canon_versions")
    op.drop_index("ix_draft_snapshots_casefile_id_created_at", table_name="draft_snapshots")
    op.drop_table("draft_snapshots")


def _create_integrity_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION casefile_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_prevent_owner_transfer()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id THEN
                RAISE EXCEPTION 'project owner_user_id is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_prevent_object_identity_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(NEW.project_id, NEW.casefile_id, NEW.draft_id, NEW.object_id, NEW.object_type)
               IS DISTINCT FROM
               ROW(
                   OLD.project_id, OLD.casefile_id, OLD.draft_id,
                   OLD.object_id, OLD.object_type
               ) THEN
                RAISE EXCEPTION 'casefile object identity, lineage, and type are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_prevent_discriminator_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'entities' AND NEW.entity_kind IS DISTINCT FROM OLD.entity_kind THEN
                RAISE EXCEPTION 'entity_kind is immutable';
            ELSIF TG_TABLE_NAME = 'information_units'
                  AND NEW.information_kind IS DISTINCT FROM OLD.information_kind THEN
                RAISE EXCEPTION 'information_kind is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_content_object_type()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_type text;
            actual_type text;
        BEGIN
            expected_type := CASE TG_TABLE_NAME
                WHEN 'narrative_phases' THEN 'narrative_phase'
                WHEN 'entities' THEN 'entity'
                WHEN 'events' THEN 'event'
                WHEN 'information_units' THEN 'information_unit'
                WHEN 'claims' THEN 'claim'
                WHEN 'hypotheses' THEN 'hypothesis'
                WHEN 'reasoning_paths' THEN 'reasoning_path'
                WHEN 'resolution_specs' THEN 'resolution_spec'
                WHEN 'casefile_constraints' THEN 'constraint'
                WHEN 'knowledge_states' THEN 'knowledge_state'
                ELSE NULL
            END;

            SELECT object_type
              INTO actual_type
              FROM casefile_objects
             WHERE id = NEW.object_registry_id
               AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id
               AND draft_id = NEW.draft_id;

            IF actual_type IS NULL OR actual_type <> expected_type THEN
                RAISE EXCEPTION '% requires registered object_type %, got %',
                    TG_TABLE_NAME, expected_type, COALESCE(actual_type, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_entity_extension()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_kind text;
            actual_kind text;
        BEGIN
            expected_kind := CASE TG_TABLE_NAME
                WHEN 'people' THEN 'person'
                WHEN 'locations' THEN 'location'
                ELSE NULL
            END;
            SELECT entity_kind
              INTO actual_kind
              FROM entities
             WHERE id = NEW.entity_id
               AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id
               AND draft_id = NEW.draft_id;
            IF actual_kind IS NULL OR actual_kind <> expected_kind THEN
                RAISE EXCEPTION '% requires entity_kind %, got %',
                    TG_TABLE_NAME, expected_kind, COALESCE(actual_kind, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_information_extension()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_kind text;
            actual_kind text;
        BEGIN
            expected_kind := CASE TG_TABLE_NAME
                WHEN 'evidence_items' THEN 'evidence'
                WHEN 'testimonies' THEN 'testimony'
                ELSE NULL
            END;
            SELECT information_kind
              INTO actual_kind
              FROM information_units
             WHERE id = NEW.information_unit_id
               AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id
               AND draft_id = NEW.draft_id;
            IF actual_kind IS NULL OR actual_kind <> expected_kind THEN
                RAISE EXCEPTION '% requires information_kind %, got %',
                    TG_TABLE_NAME, expected_kind, COALESCE(actual_kind, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_known_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            source_type text;
            target_type text;
            source_entity_kind text;
            target_entity_kind text;
        BEGIN
            SELECT object_type INTO source_type
              FROM casefile_objects WHERE id = NEW.from_object_id;
            SELECT object_type INTO target_type
              FROM casefile_objects WHERE id = NEW.to_object_id;

            IF NEW.ref_kind = 'event_actor' THEN
                IF source_type <> 'event' OR target_type <> 'entity' THEN
                    RAISE EXCEPTION 'event_actor requires event -> entity';
                END IF;
            ELSIF NEW.ref_kind = 'location_adjacent_to' THEN
                SELECT entity_kind INTO source_entity_kind
                  FROM entities WHERE object_registry_id = NEW.from_object_id;
                SELECT entity_kind INTO target_entity_kind
                  FROM entities WHERE object_registry_id = NEW.to_object_id;
                IF source_entity_kind IS DISTINCT FROM 'location'
                   OR target_entity_kind IS DISTINCT FROM 'location'
                   OR NEW.from_object_id = NEW.to_object_id THEN
                    RAISE EXCEPTION 'location_adjacent_to requires two distinct location entities';
                END IF;
            ELSIF NEW.ref_kind IN ('supports', 'refutes') THEN
                IF source_type <> 'information_unit' OR target_type <> 'claim' THEN
                    RAISE EXCEPTION '% requires information_unit -> claim', NEW.ref_kind;
                END IF;
            ELSIF NEW.ref_kind = 'hypothesis_claim' THEN
                IF source_type <> 'hypothesis' OR target_type <> 'claim' THEN
                    RAISE EXCEPTION 'hypothesis_claim requires hypothesis -> claim';
                END IF;
            ELSIF NEW.ref_kind = 'hypothesis_required_information' THEN
                IF source_type <> 'hypothesis' OR target_type <> 'information_unit' THEN
                    RAISE EXCEPTION
                        'hypothesis_required_information requires hypothesis -> information_unit';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_apply_draft_operation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_revision integer;
            current_status text;
            expected_sequence bigint;
            owner_id bigint;
        BEGIN
            SELECT d.revision, d.status, p.owner_user_id
              INTO current_revision, current_status, owner_id
              FROM drafts AS d
              JOIN projects AS p ON p.id = d.project_id
             WHERE d.id = NEW.draft_id
               AND d.project_id = NEW.project_id
               AND d.casefile_id = NEW.casefile_id
             FOR UPDATE OF d;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'draft lineage does not exist';
            END IF;
            IF current_status <> 'active' THEN
                RAISE EXCEPTION 'locked Draft cannot accept operations';
            END IF;
            IF NEW.base_revision <> current_revision THEN
                RAISE EXCEPTION 'stale Draft revision: expected %, got %',
                    current_revision, NEW.base_revision;
            END IF;
            SELECT COALESCE(MAX(sequence_no), 0) + 1
              INTO expected_sequence
              FROM draft_operations
             WHERE draft_id = NEW.draft_id;
            IF NEW.sequence_no <> expected_sequence THEN
                RAISE EXCEPTION 'non-contiguous operation sequence: expected %, got %',
                    expected_sequence, NEW.sequence_no;
            END IF;
            IF NEW.actor_kind = 'user' AND NEW.actor_user_id <> owner_id THEN
                RAISE EXCEPTION 'operation user must own the Project';
            END IF;
            UPDATE drafts
               SET revision = NEW.result_revision
             WHERE id = NEW.draft_id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            draft_revision integer;
            draft_schema text;
            casefile_schema text;
            owner_id bigint;
        BEGIN
            SELECT d.revision, d.schema_version, cf.schema_version, p.owner_user_id
              INTO draft_revision, draft_schema, casefile_schema, owner_id
              FROM drafts AS d
              JOIN casefiles AS cf
                ON cf.id = d.casefile_id AND cf.project_id = d.project_id
              JOIN projects AS p ON p.id = d.project_id
             WHERE d.id = NEW.draft_id
               AND d.project_id = NEW.project_id
               AND d.casefile_id = NEW.casefile_id
             FOR UPDATE OF d, cf;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'snapshot Draft lineage does not exist';
            END IF;
            IF NEW.snapshot_revision <> draft_revision THEN
                RAISE EXCEPTION 'snapshot revision must equal current Draft revision';
            END IF;
            IF NEW.schema_version <> draft_schema OR NEW.schema_version <> casefile_schema THEN
                RAISE EXCEPTION 'CaseFile, Draft, and Snapshot schema versions must match';
            END IF;
            IF NEW.created_by_user_id <> owner_id THEN
                RAISE EXCEPTION 'snapshot creator must own the Project';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_canon()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            draft_row_id bigint;
            draft_revision integer;
            draft_schema text;
            casefile_schema text;
            current_canon_id bigint;
            current_version integer;
            owner_id bigint;
            snapshot_draft_id bigint;
            snapshot_revision_value integer;
            snapshot_schema text;
            snapshot_content jsonb;
            snapshot_hash text;
        BEGIN
            SELECT d.id, d.revision, d.schema_version, cf.schema_version,
                   cf.current_canon_version_id, p.owner_user_id
              INTO draft_row_id, draft_revision, draft_schema, casefile_schema,
                   current_canon_id, owner_id
              FROM casefiles AS cf
              JOIN projects AS p ON p.id = cf.project_id
              JOIN drafts AS d
                ON d.project_id = cf.project_id AND d.casefile_id = cf.id
             WHERE cf.id = NEW.casefile_id
               AND cf.project_id = NEW.project_id
             FOR UPDATE OF cf, d;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Canon CaseFile/Draft lineage does not exist';
            END IF;

            SELECT ds.draft_id, ds.snapshot_revision, ds.schema_version,
                   ds.snapshot_jsonb, ds.content_hash
              INTO snapshot_draft_id, snapshot_revision_value, snapshot_schema,
                   snapshot_content, snapshot_hash
              FROM draft_snapshots AS ds
             WHERE ds.id = NEW.source_snapshot_id
               AND ds.project_id = NEW.project_id
               AND ds.casefile_id = NEW.casefile_id;
            IF NOT FOUND OR snapshot_draft_id <> draft_row_id THEN
                RAISE EXCEPTION 'Canon source Snapshot does not belong to current Draft';
            END IF;
            IF NEW.confirmed_by_user_id <> owner_id THEN
                RAISE EXCEPTION 'Canon confirmer must own the Project';
            END IF;
            IF snapshot_revision_value <> draft_revision THEN
                RAISE EXCEPTION 'Canon source Snapshot must match current Draft revision';
            END IF;
            IF NEW.schema_version <> casefile_schema
               OR NEW.schema_version <> draft_schema
               OR NEW.schema_version <> snapshot_schema THEN
                RAISE EXCEPTION 'CaseFile, Draft, Snapshot, and Canon schema versions must match';
            END IF;
            IF NEW.content_jsonb IS DISTINCT FROM snapshot_content
               OR NEW.content_hash IS DISTINCT FROM snapshot_hash THEN
                RAISE EXCEPTION 'Canon content and hash must equal the source Snapshot';
            END IF;

            IF current_canon_id IS NULL THEN
                IF NEW.version_no <> 1 OR NEW.parent_canon_version_id IS NOT NULL THEN
                    RAISE EXCEPTION 'first Canon must be version 1 without a parent';
                END IF;
            ELSE
                SELECT version_no INTO current_version
                  FROM canon_versions WHERE id = current_canon_id;
                IF NEW.version_no <> current_version + 1
                   OR NEW.parent_canon_version_id <> current_canon_id THEN
                    RAISE EXCEPTION 'Canon version and parent must continue current Canon';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_complete_canon()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE casefiles
               SET current_canon_version_id = NEW.id,
                   status = 'canon'
             WHERE id = NEW.casefile_id AND project_id = NEW.project_id;
            UPDATE drafts
               SET base_canon_version_id = NEW.id
             WHERE casefile_id = NEW.casefile_id AND project_id = NEW.project_id;
            INSERT INTO audit_events (
                project_id,
                casefile_id,
                actor_kind,
                actor_user_id,
                action,
                target_type,
                target_id,
                details_jsonb
            ) VALUES (
                NEW.project_id,
                NEW.casefile_id,
                'user',
                NEW.confirmed_by_user_id,
                'canon.created',
                'canon_version',
                NEW.id,
                jsonb_build_object(
                    'version_no', NEW.version_no,
                    'source_snapshot_id', NEW.source_snapshot_id,
                    'schema_version', NEW.schema_version,
                    'content_hash', NEW.content_hash
                )
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_reject_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is forbidden', TG_TABLE_NAME, TG_OP;
        END;
        $$
        """
    )


def _create_integrity_triggers() -> None:
    for table_name in UPDATED_AT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_projects_owner_immutable
        BEFORE UPDATE OF owner_user_id ON projects
        FOR EACH ROW EXECUTE FUNCTION casefile_prevent_owner_transfer()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_casefile_objects_identity_immutable
        BEFORE UPDATE OF project_id, casefile_id, draft_id, object_id, object_type
        ON casefile_objects
        FOR EACH ROW EXECUTE FUNCTION casefile_prevent_object_identity_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_entities_kind_immutable
        BEFORE UPDATE OF entity_kind ON entities
        FOR EACH ROW EXECUTE FUNCTION casefile_prevent_discriminator_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_information_units_kind_immutable
        BEFORE UPDATE OF information_kind ON information_units
        FOR EACH ROW EXECUTE FUNCTION casefile_prevent_discriminator_change()
        """
    )

    for table_name in CORE_CONTENT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_registered_type
            BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, object_registry_id
            ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_validate_content_object_type()
            """
        )

    for table_name in ("people", "locations"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_entity_kind
            BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, entity_id
            ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_validate_entity_extension()
            """
        )
    for table_name in ("evidence_items", "testimonies"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_information_kind
            BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, information_unit_id
            ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_validate_information_extension()
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_casefile_refs_known_type
        BEFORE INSERT OR UPDATE OF from_object_id, to_object_id, ref_kind
        ON casefile_refs
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_known_reference()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_draft_operations_apply
        BEFORE INSERT ON draft_operations
        FOR EACH ROW EXECUTE FUNCTION casefile_apply_draft_operation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_draft_snapshots_validate
        BEFORE INSERT ON draft_snapshots
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_snapshot()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_canon_versions_validate
        BEFORE INSERT ON canon_versions
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_canon()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_canon_versions_complete
        AFTER INSERT ON canon_versions
        FOR EACH ROW EXECUTE FUNCTION casefile_complete_canon()
        """
    )
    for table_name in (
        "draft_operations",
        "draft_snapshots",
        "canon_versions",
        "audit_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_reject_history_mutation()
            """
        )


def _drop_integrity_triggers() -> None:
    for table_name in (
        "draft_operations",
        "draft_snapshots",
        "canon_versions",
        "audit_events",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP TRIGGER trg_canon_versions_complete ON canon_versions")
    op.execute("DROP TRIGGER trg_canon_versions_validate ON canon_versions")
    op.execute("DROP TRIGGER trg_draft_snapshots_validate ON draft_snapshots")
    op.execute("DROP TRIGGER trg_draft_operations_apply ON draft_operations")
    op.execute("DROP TRIGGER trg_casefile_refs_known_type ON casefile_refs")
    for table_name in ("evidence_items", "testimonies"):
        op.execute(f"DROP TRIGGER trg_{table_name}_information_kind ON {table_name}")
    for table_name in ("people", "locations"):
        op.execute(f"DROP TRIGGER trg_{table_name}_entity_kind ON {table_name}")
    for table_name in CORE_CONTENT_TABLES:
        op.execute(f"DROP TRIGGER trg_{table_name}_registered_type ON {table_name}")
    op.execute("DROP TRIGGER trg_information_units_kind_immutable ON information_units")
    op.execute("DROP TRIGGER trg_entities_kind_immutable ON entities")
    op.execute("DROP TRIGGER trg_casefile_objects_identity_immutable ON casefile_objects")
    op.execute("DROP TRIGGER trg_projects_owner_immutable ON projects")
    for table_name in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER trg_{table_name}_updated_at ON {table_name}")


def _drop_integrity_functions() -> None:
    for function_name in (
        "casefile_reject_history_mutation",
        "casefile_complete_canon",
        "casefile_validate_canon",
        "casefile_validate_snapshot",
        "casefile_apply_draft_operation",
        "casefile_validate_known_reference",
        "casefile_validate_information_extension",
        "casefile_validate_entity_extension",
        "casefile_validate_content_object_type",
        "casefile_prevent_discriminator_change",
        "casefile_prevent_object_identity_change",
        "casefile_prevent_owner_transfer",
        "casefile_set_updated_at",
    ):
        op.execute(f"DROP FUNCTION {function_name}()")
