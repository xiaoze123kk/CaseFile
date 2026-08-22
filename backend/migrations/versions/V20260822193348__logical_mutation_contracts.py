"""logical_mutation_contracts

Revision ID: 20260822193348
Revises: 20260820151005
Create Date: 2026-08-22 19:33:49.369971
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822193348"
down_revision: str | None = "20260820151005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_patch_sets",
        sa.Column(
            "closure_policy_version",
            sa.String(length=64),
            server_default=sa.text("'logical-mutation-v1'"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_patch_sets",
        sa.Column(
            "mutation_mode",
            sa.String(length=16),
            server_default=sa.text("'normal'"),
            nullable=False,
        ),
    )
    op.add_column("agent_patch_sets", sa.Column("baseline_hash", sa.String(64)))
    op.add_column("agent_patch_sets", sa.Column("candidate_hash", sa.String(64)))
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_mutation_mode_allowed"),
        "agent_patch_sets",
        "mutation_mode IN ('normal', 'restructure')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_closure_policy_version_not_blank"),
        "agent_patch_sets",
        "length(btrim(closure_policy_version)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_baseline_hash_format"),
        "agent_patch_sets",
        "baseline_hash IS NULL OR baseline_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_candidate_hash_format"),
        "agent_patch_sets",
        "candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "agent_patch_operations",
        sa.Column("target_object_key", sa.String(128), nullable=True),
    )
    op.add_column(
        "agent_patch_operations",
        sa.Column("target_collection", sa.String(40), nullable=True),
    )
    op.execute(
        """
        UPDATE agent_patch_operations AS operation
        SET target_object_key = registry.object_id,
            target_collection = CASE registry.object_type
                WHEN 'resolution_spec' THEN 'resolution_specs'
                WHEN 'entity' THEN 'entities'
                WHEN 'relationship' THEN 'relationships'
                WHEN 'location' THEN 'locations'
                WHEN 'event' THEN 'events'
                WHEN 'information_unit' THEN 'information_units'
                WHEN 'claim' THEN 'claims'
                WHEN 'hypothesis' THEN 'hypotheses'
                WHEN 'reasoning_path' THEN 'reasoning_paths'
                WHEN 'constraint' THEN 'constraints'
                WHEN 'structure_lock' THEN 'structure_locks'
            END
        FROM casefile_objects AS registry
        WHERE registry.id = operation.target_object_id
        """
    )
    op.alter_column("agent_patch_operations", "target_object_key", nullable=False)
    op.alter_column("agent_patch_operations", "target_collection", nullable=False)
    op.alter_column("agent_patch_operations", "target_object_id", nullable=True)
    op.drop_constraint(
        op.f("ck_agent_patch_operations_operation_type_allowed"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_field_path_json_pointer"),
        "agent_patch_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_operation_type_allowed"),
        "agent_patch_operations",
        "operation_type IN ('add', 'remove', 'replace', "
        "'create_object', 'update_field', 'delete_object')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_field_path_shape"),
        "agent_patch_operations",
        "(operation_type IN ('create_object', 'delete_object') AND field_path = '') OR "
        "(operation_type NOT IN ('create_object', 'delete_object') AND field_path ~ '^/')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_target_object_shape"),
        "agent_patch_operations",
        "(operation_type = 'create_object' AND target_object_id IS NULL) OR "
        "(operation_type <> 'create_object' AND target_object_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_target_object_key_not_blank"),
        "agent_patch_operations",
        "length(btrim(target_object_key)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_target_collection_allowed"),
        "agent_patch_operations",
        "target_collection IN ('resolution_specs', 'entities', 'relationships', "
        "'locations', 'events', 'information_units', 'claims', 'hypotheses', "
        "'reasoning_paths', 'constraints', 'structure_locks')",
    )

    op.drop_constraint(op.f("ck_draft_operations_type_allowed"), "draft_operations", type_="check")
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN ('add', 'remove', 'replace', 'agent_generate_from_brief', "
        "'agent_adopt_brief_candidate', 'agent_patch_apply', 'agent_patch_undo', "
        "'logical_mutation_apply', 'logical_mutation_undo', "
        "'logical_mutation_redo', 'logical_mutation_normalize')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM agent_patch_operations
                WHERE operation_type IN ('create_object', 'delete_object')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while object-level logical mutations exist';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "UPDATE agent_patch_operations SET operation_type = 'replace' "
        "WHERE operation_type = 'update_field'"
    )
    op.drop_constraint(op.f("ck_draft_operations_type_allowed"), "draft_operations", type_="check")
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN ('add', 'remove', 'replace', 'agent_generate_from_brief', "
        "'agent_adopt_brief_candidate', 'agent_patch_apply', 'agent_patch_undo')",
    )
    for name in (
        "target_collection_allowed",
        "target_object_key_not_blank",
        "target_object_shape",
        "field_path_shape",
        "operation_type_allowed",
    ):
        op.drop_constraint(
            op.f(f"ck_agent_patch_operations_{name}"),
            "agent_patch_operations",
            type_="check",
        )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_operation_type_allowed"),
        "agent_patch_operations",
        "operation_type IN ('add', 'remove', 'replace')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_field_path_json_pointer"),
        "agent_patch_operations",
        "field_path ~ '^/'",
    )
    op.alter_column("agent_patch_operations", "target_object_id", nullable=False)
    op.drop_column("agent_patch_operations", "target_collection")
    op.drop_column("agent_patch_operations", "target_object_key")
    for name in (
        "candidate_hash_format",
        "baseline_hash_format",
        "closure_policy_version_not_blank",
        "mutation_mode_allowed",
    ):
        op.drop_constraint(
            op.f(f"ck_agent_patch_sets_{name}"),
            "agent_patch_sets",
            type_="check",
        )
    op.drop_column("agent_patch_sets", "candidate_hash")
    op.drop_column("agent_patch_sets", "baseline_hash")
    op.drop_column("agent_patch_sets", "mutation_mode")
    op.drop_column("agent_patch_sets", "closure_policy_version")
