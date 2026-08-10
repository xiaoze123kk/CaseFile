"""Allow many editable Drafts with one server-selected current Draft.

Revision ID: 20260809224245
Revises: 20260808154126
Create Date: 2026-08-09 22:42:47.062571
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809224245"
down_revision: str | None = "20260808154126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("title", sa.String(length=200), nullable=True))
    op.add_column(
        "drafts",
        sa.Column(
            "document_status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE drafts AS draft
        SET title = casefile.title,
            document_status = CASE
                WHEN casefile.status = 'archived' THEN 'archived'
                ELSE 'draft'
            END
        FROM casefiles AS casefile
        WHERE casefile.id = draft.casefile_id
          AND casefile.project_id = draft.project_id
        """
    )
    op.alter_column("drafts", "title", nullable=False)
    op.create_check_constraint(
        op.f("ck_drafts_title_not_blank"),
        "drafts",
        "length(btrim(title)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_drafts_document_status_allowed"),
        "drafts",
        "document_status IN ('draft', 'canon', 'archived')",
    )
    op.create_index(
        "ix_drafts_casefile_id_updated_at",
        "drafts",
        ["casefile_id", "updated_at"],
    )

    op.add_column(
        "casefiles",
        sa.Column("current_draft_id", sa.BigInteger(), nullable=True),
    )
    op.execute(
        """
        UPDATE casefiles AS casefile
        SET current_draft_id = draft.id
        FROM drafts AS draft
        WHERE draft.project_id = casefile.project_id
          AND draft.casefile_id = casefile.id
        """
    )
    op.alter_column("casefiles", "current_draft_id", nullable=False)
    op.create_foreign_key(
        "fk_casefiles_project_casefile_current_draft_drafts",
        "casefiles",
        "drafts",
        ["project_id", "id", "current_draft_id"],
        ["project_id", "casefile_id", "id"],
        ondelete="RESTRICT",
        use_alter=True,
        deferrable=True,
        initially="DEFERRED",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_canon()
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
                ON d.project_id = cf.project_id
               AND d.casefile_id = cf.id
               AND d.id = cf.current_draft_id
             WHERE cf.id = NEW.casefile_id
               AND cf.project_id = NEW.project_id
             FOR UPDATE OF cf, d;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Canon CaseFile/current Draft lineage does not exist';
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
        CREATE OR REPLACE FUNCTION casefile_complete_canon()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE casefiles
               SET current_canon_version_id = NEW.id,
                   status = 'canon'
             WHERE id = NEW.casefile_id AND project_id = NEW.project_id;
            UPDATE drafts
               SET base_canon_version_id = NEW.id,
                   document_status = 'canon'
             WHERE id = (
                 SELECT current_draft_id
                   FROM casefiles
                  WHERE id = NEW.casefile_id AND project_id = NEW.project_id
             )
               AND casefile_id = NEW.casefile_id
               AND project_id = NEW.project_id;
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

    op.drop_constraint("uq_drafts_project_id_casefile_id", "drafts", type_="unique")
    op.drop_constraint(
        "uq_casefile_objects_casefile_id_object_id",
        "casefile_objects",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_casefile_objects_draft_id_object_id",
        "casefile_objects",
        ["draft_id", "object_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM drafts
                 GROUP BY project_id, casefile_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while a CaseFile contains multiple Drafts';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_canon()
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
        CREATE OR REPLACE FUNCTION casefile_complete_canon()
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

    op.drop_constraint(
        "uq_casefile_objects_draft_id_object_id",
        "casefile_objects",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_casefile_objects_casefile_id_object_id",
        "casefile_objects",
        ["casefile_id", "object_id"],
    )
    op.create_unique_constraint(
        "uq_drafts_project_id_casefile_id",
        "drafts",
        ["project_id", "casefile_id"],
    )

    op.drop_constraint(
        "fk_casefiles_project_casefile_current_draft_drafts",
        "casefiles",
        type_="foreignkey",
    )
    op.drop_column("casefiles", "current_draft_id")

    op.drop_index("ix_drafts_casefile_id_updated_at", table_name="drafts")
    op.drop_constraint(op.f("ck_drafts_document_status_allowed"), "drafts", type_="check")
    op.drop_constraint(op.f("ck_drafts_title_not_blank"), "drafts", type_="check")
    op.drop_column("drafts", "document_status")
    op.drop_column("drafts", "title")
