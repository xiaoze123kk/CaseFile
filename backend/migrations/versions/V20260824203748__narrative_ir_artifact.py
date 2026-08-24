"""narrative_ir_artifact

Revision ID: 20260824203748
Revises: 20260824175326
Create Date: 2026-08-24 20:37:49.256371
"""

from collections.abc import Sequence

from alembic import op

revision: str = '20260824203748'
down_revision: str | None = '20260824175326'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_compile_artifacts_artifact_kind_n4_1"),
        "compile_artifacts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_compile_artifacts_artifact_key_n4_1"),
        "compile_artifacts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_compile_artifacts_schema_id_n4_1"),
        "compile_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        "(artifact_kind = 'input_manifest' AND "
        "artifact_key = 'compiler.input_manifest' AND "
        "schema_id = 'compiler.input-manifest.v1') OR "
        "(artifact_kind = 'narrative_ir' AND "
        "artifact_key = 'compiler.narrative_ir' AND "
        "schema_id = 'compiler.narrative-ir.v1')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_artifact_kind_n4_1"),
        "compile_artifacts",
        "artifact_kind = 'input_manifest'",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_artifact_key_n4_1"),
        "compile_artifacts",
        "artifact_key = 'compiler.input_manifest'",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_schema_id_n4_1"),
        "compile_artifacts",
        "schema_id = 'compiler.input-manifest.v1'",
    )
