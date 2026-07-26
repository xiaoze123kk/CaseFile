"""reasoning_resolution_content

Revision ID: 20260726131017
Revises: 20260726131014
Create Date: 2026-07-26 13:10:17.587040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726131017"
down_revision: str | None = "20260726131014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "narrative_phases",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phase_order", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "release_rule_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            "length(btrim(name)) > 0", name=op.f("ck_narrative_phases_name_not_blank")
        ),
        sa.CheckConstraint(
            "phase_order >= 1", name=op.f("ck_narrative_phases_phase_order_positive")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(release_rule_jsonb) = 'object'",
            name=op.f("ck_narrative_phases_release_rule_is_object"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=op.f("ck_narrative_phases_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_narrative_phases_draft",
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
            name="fk_narrative_phases_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_narrative_phases")),
        sa.UniqueConstraint("object_registry_id", name="uq_narrative_phases_object_registry_id"),
        sa.UniqueConstraint("draft_id", "phase_order", name="uq_narrative_phases_draft_order"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_narrative_phases_lineage_id"
        ),
    )
    op.create_index(
        "ix_narrative_phases_draft_id_status", "narrative_phases", ["draft_id", "status"]
    )

    op.create_table(
        "claims",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'unresolved'"), nullable=False
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
            "length(btrim(statement)) > 0", name=op.f("ck_claims_statement_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('unresolved', 'supported', 'refuted', 'disputed')",
            name=op.f("ck_claims_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_claims_draft",
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
            name="fk_claims_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
        sa.UniqueConstraint("object_registry_id", name="uq_claims_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_claims_lineage_id"
        ),
    )
    op.create_index("ix_claims_draft_id_status", "claims", ["draft_id", "status"])

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "exclusion_rule_jsonb",
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
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_hypotheses_title_not_blank")),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'supported', 'refuted', 'discarded')",
            name=op.f("ck_hypotheses_status_allowed"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 1", name=op.f("ck_hypotheses_score_range")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(exclusion_rule_jsonb) = 'object'",
            name=op.f("ck_hypotheses_exclusion_rule_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_hypotheses_draft",
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
            name="fk_hypotheses_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hypotheses")),
        sa.UniqueConstraint("object_registry_id", name="uq_hypotheses_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_hypotheses_lineage_id"
        ),
    )
    op.create_index("ix_hypotheses_draft_id_status", "hypotheses", ["draft_id", "status"])

    op.create_table(
        "reasoning_paths",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("reasoning_type", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("human_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
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
            "length(btrim(name)) > 0", name=op.f("ck_reasoning_paths_name_not_blank")
        ),
        sa.CheckConstraint(
            "reasoning_type IN ('deductive', 'inductive', 'abductive', 'mixed')",
            name=op.f("ck_reasoning_paths_reasoning_type_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'confirmed', 'rejected')",
            name=op.f("ck_reasoning_paths_status_allowed"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_reasoning_paths_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_reasoning_paths_draft",
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
            name="fk_reasoning_paths_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reasoning_paths")),
        sa.UniqueConstraint("object_registry_id", name="uq_reasoning_paths_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_reasoning_paths_lineage_id"
        ),
    )
    op.create_index("ix_reasoning_paths_draft_id_status", "reasoning_paths", ["draft_id", "status"])

    op.create_table(
        "reasoning_nodes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_path_id", sa.BigInteger(), nullable=False),
        sa.Column("node_key", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_object_id", sa.BigInteger(), nullable=True),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
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
            "node_key ~ '^[a-z][a-z0-9_]{1,127}$'", name=op.f("ck_reasoning_nodes_node_key_format")
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_reasoning_nodes_ordinal_positive")),
        sa.CheckConstraint(
            "node_type ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_reasoning_nodes_node_type_format")
        ),
        sa.CheckConstraint(
            "length(btrim(statement)) > 0", name=op.f("ck_reasoning_nodes_statement_not_blank")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_jsonb) = 'object'",
            name=op.f("ck_reasoning_nodes_attributes_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_reasoning_nodes_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id"],
            [
                "reasoning_paths.project_id",
                "reasoning_paths.casefile_id",
                "reasoning_paths.draft_id",
                "reasoning_paths.id",
            ],
            name="fk_reasoning_nodes_path",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "source_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_reasoning_nodes_source_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reasoning_nodes")),
        sa.UniqueConstraint("reasoning_path_id", "node_key", name="uq_reasoning_nodes_path_key"),
        sa.UniqueConstraint("reasoning_path_id", "ordinal", name="uq_reasoning_nodes_path_ordinal"),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "reasoning_path_id",
            "id",
            name="uq_reasoning_nodes_path_lineage_id",
        ),
    )

    op.create_table(
        "reasoning_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_path_id", sa.BigInteger(), nullable=False),
        sa.Column("from_node_id", sa.BigInteger(), nullable=False),
        sa.Column("to_node_id", sa.BigInteger(), nullable=False),
        sa.Column("argument_kind", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("human_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            "from_node_id <> to_node_id", name=op.f("ck_reasoning_edges_nodes_differ")
        ),
        sa.CheckConstraint(
            "argument_kind ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_reasoning_edges_argument_kind_format"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_reasoning_edges_confidence_range"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes_jsonb) = 'object'",
            name=op.f("ck_reasoning_edges_attributes_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_reasoning_edges_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id"],
            [
                "reasoning_paths.project_id",
                "reasoning_paths.casefile_id",
                "reasoning_paths.draft_id",
                "reasoning_paths.id",
            ],
            name="fk_reasoning_edges_path",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id", "from_node_id"],
            [
                "reasoning_nodes.project_id",
                "reasoning_nodes.casefile_id",
                "reasoning_nodes.draft_id",
                "reasoning_nodes.reasoning_path_id",
                "reasoning_nodes.id",
            ],
            name="fk_reasoning_edges_from_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id", "to_node_id"],
            [
                "reasoning_nodes.project_id",
                "reasoning_nodes.casefile_id",
                "reasoning_nodes.draft_id",
                "reasoning_nodes.reasoning_path_id",
                "reasoning_nodes.id",
            ],
            name="fk_reasoning_edges_to_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reasoning_edges")),
        sa.UniqueConstraint(
            "reasoning_path_id",
            "from_node_id",
            "to_node_id",
            "argument_kind",
            name="uq_reasoning_edges_argument",
        ),
    )
    op.create_index(
        "ix_reasoning_edges_path_from", "reasoning_edges", ["reasoning_path_id", "from_node_id"]
    )
    op.create_index(
        "ix_reasoning_edges_path_to", "reasoning_edges", ["reasoning_path_id", "to_node_id"]
    )

    op.create_table(
        "resolution_specs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("question_type", sa.String(length=40), nullable=False),
        sa.Column("target_question", sa.Text(), nullable=False),
        sa.Column(
            "conclusion_pattern_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            "question_type ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_resolution_specs_question_type_format"),
        ),
        sa.CheckConstraint(
            "length(btrim(target_question)) > 0",
            name=op.f("ck_resolution_specs_target_question_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(conclusion_pattern_jsonb) = 'object'",
            name=op.f("ck_resolution_specs_conclusion_pattern_is_object"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'resolved', 'locked')",
            name=op.f("ck_resolution_specs_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_resolution_specs_draft",
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
            name="fk_resolution_specs_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_specs")),
        sa.UniqueConstraint("object_registry_id", name="uq_resolution_specs_object_registry_id"),
        sa.UniqueConstraint("draft_id", name="uq_resolution_specs_draft_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_resolution_specs_lineage_id"
        ),
    )

    op.create_table(
        "resolution_slots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("resolution_spec_id", sa.BigInteger(), nullable=False),
        sa.Column("slot_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("value_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "slot_key ~ '^[a-z][a-z0-9_]{1,127}$'", name=op.f("ck_resolution_slots_slot_key_format")
        ),
        sa.CheckConstraint(
            "length(btrim(label)) > 0", name=op.f("ck_resolution_slots_label_not_blank")
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_resolution_slots_ordinal_positive")),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_resolution_slots_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "resolution_spec_id"],
            [
                "resolution_specs.project_id",
                "resolution_specs.casefile_id",
                "resolution_specs.draft_id",
                "resolution_specs.id",
            ],
            name="fk_resolution_slots_spec",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_slots")),
        sa.UniqueConstraint("resolution_spec_id", "slot_key", name="uq_resolution_slots_spec_key"),
        sa.UniqueConstraint(
            "resolution_spec_id", "ordinal", name="uq_resolution_slots_spec_ordinal"
        ),
    )

    op.create_table(
        "casefile_constraints",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("target_object_id", sa.BigInteger(), nullable=True),
        sa.Column("constraint_kind", sa.String(length=40), nullable=False),
        sa.Column("constraint_level", sa.String(length=12), nullable=False),
        sa.Column("rule_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column(
            "conflict_status",
            sa.String(length=20),
            server_default=sa.text("'none'"),
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
            "constraint_kind ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_casefile_constraints_constraint_kind_format"),
        ),
        sa.CheckConstraint(
            "constraint_level IN ('hard', 'soft')",
            name=op.f("ck_casefile_constraints_constraint_level_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rule_jsonb) = 'object'",
            name=op.f("ck_casefile_constraints_rule_is_object"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name=op.f("ck_casefile_constraints_status_allowed")
        ),
        sa.CheckConstraint(
            "conflict_status IN ('none', 'potential', 'confirmed', 'resolved')",
            name=op.f("ck_casefile_constraints_conflict_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_constraints_draft",
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
            name="fk_casefile_constraints_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "target_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_constraints_target_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_casefile_constraints")),
        sa.UniqueConstraint(
            "object_registry_id", name="uq_casefile_constraints_object_registry_id"
        ),
    )
    op.create_index(
        "ix_casefile_constraints_draft_id_status", "casefile_constraints", ["draft_id", "status"]
    )

    op.create_table(
        "knowledge_states",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("narrative_phase_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "status IN ('unknown', 'partial', 'known', 'misinformed')",
            name=op.f("ck_knowledge_states_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_knowledge_states_draft",
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
            name="fk_knowledge_states_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entity_id"],
            ["entities.project_id", "entities.casefile_id", "entities.draft_id", "entities.id"],
            name="fk_knowledge_states_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_states")),
        sa.UniqueConstraint("object_registry_id", name="uq_knowledge_states_object_registry_id"),
        sa.UniqueConstraint(
            "draft_id", "entity_id", "narrative_phase_id", name="uq_knowledge_states_entity_phase"
        ),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_knowledge_states_lineage_id"
        ),
    )

    op.create_table(
        "knowledge_state_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_state_id", sa.BigInteger(), nullable=False),
        sa.Column("information_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("cognition_status", sa.String(length=20), nullable=False),
        sa.Column("disclosure_status", sa.String(length=20), nullable=False),
        sa.Column("acquired_from_object_id", sa.BigInteger(), nullable=True),
        sa.Column("certainty", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
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
            "cognition_status IN ('unknown', 'suspected', 'known', 'believed', 'disbelieved')",
            name=op.f("ck_knowledge_state_entries_cognition_status_allowed"),
        ),
        sa.CheckConstraint(
            "disclosure_status IN ('hidden', 'available', 'revealed')",
            name=op.f("ck_knowledge_state_entries_disclosure_status_allowed"),
        ),
        sa.CheckConstraint(
            "certainty IS NULL OR certainty BETWEEN 0 AND 1",
            name=op.f("ck_knowledge_state_entries_certainty_range"),
        ),
        sa.CheckConstraint(
            "ordinal >= 1", name=op.f("ck_knowledge_state_entries_ordinal_positive")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_knowledge_state_entries_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "acquired_from_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_knowledge_state_entries_source_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_state_entries")),
        sa.UniqueConstraint(
            "knowledge_state_id",
            "information_unit_id",
            name="uq_knowledge_entries_state_information",
        ),
        sa.UniqueConstraint(
            "knowledge_state_id", "ordinal", name="uq_knowledge_entries_state_ordinal"
        ),
    )


def downgrade() -> None:
    op.drop_table("knowledge_state_entries")
    op.drop_table("knowledge_states")
    op.drop_index("ix_casefile_constraints_draft_id_status", table_name="casefile_constraints")
    op.drop_table("casefile_constraints")
    op.drop_table("resolution_slots")
    op.drop_table("resolution_specs")
    op.drop_index("ix_reasoning_edges_path_to", table_name="reasoning_edges")
    op.drop_index("ix_reasoning_edges_path_from", table_name="reasoning_edges")
    op.drop_table("reasoning_edges")
    op.drop_table("reasoning_nodes")
    op.drop_index("ix_reasoning_paths_draft_id_status", table_name="reasoning_paths")
    op.drop_table("reasoning_paths")
    op.drop_index("ix_hypotheses_draft_id_status", table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_index("ix_claims_draft_id_status", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_narrative_phases_draft_id_status", table_name="narrative_phases")
    op.drop_table("narrative_phases")
