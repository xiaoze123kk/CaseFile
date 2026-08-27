"""scene_plan_v2_artifact

Revision ID: 20260826175944
Revises: 20260826161422
Create Date: 2026-08-26 17:59:45.648078
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826175944"
down_revision: str | None = "20260826161422"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        "(artifact_kind = 'input_manifest' AND artifact_key = 'compiler.input_manifest' "
        "AND schema_id = 'compiler.input-manifest.v1') OR "
        "(artifact_kind = 'narrative_ir' AND artifact_key = 'compiler.narrative_ir' "
        "AND schema_id = 'compiler.narrative-ir.v1') OR "
        "(artifact_kind = 'novel_plan' AND artifact_key = 'compiler.novel_plan' "
        "AND schema_id = 'compiler.novel-plan.v1') OR "
        "(artifact_kind = 'scene_plan' AND artifact_key = 'compiler.scene_plan' "
        "AND schema_id IN ('compiler.scene-plan.v1', 'compiler.scene-plan.v2'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"),
        "compile_artifacts",
        "(artifact_kind = 'input_manifest' AND artifact_key = 'compiler.input_manifest' "
        "AND schema_id = 'compiler.input-manifest.v1') OR "
        "(artifact_kind = 'narrative_ir' AND artifact_key = 'compiler.narrative_ir' "
        "AND schema_id = 'compiler.narrative-ir.v1') OR "
        "(artifact_kind = 'novel_plan' AND artifact_key = 'compiler.novel_plan' "
        "AND schema_id = 'compiler.novel-plan.v1') OR "
        "(artifact_kind = 'scene_plan' AND artifact_key = 'compiler.scene_plan' "
        "AND schema_id = 'compiler.scene-plan.v1')",
    )
