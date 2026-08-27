"""story_planner_runtime

Revision ID: 20260824233834
Revises: 20260824203748
Create Date: 2026-08-24 23:38:35.405009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824233834"
down_revision: str | None = "20260824203748"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"),
        "task_runs",
        "(task_type = 'novel_compile' AND ((provider_setting_id IS NULL AND "
        "provider IS NULL AND model_id IS NULL AND provider_config_version IS NULL) OR "
        "(provider_setting_id IS NOT NULL AND provider IS NOT NULL AND model_id IS NOT NULL "
        "AND provider_config_version IS NOT NULL))) OR (task_type <> 'novel_compile' AND "
        "provider_setting_id IS NOT NULL AND provider IS NOT NULL AND model_id IS NOT NULL "
        "AND provider_config_version IS NOT NULL)",
    )
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
        "AND schema_id = 'compiler.novel-plan.v1')",
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
        "AND schema_id = 'compiler.narrative-ir.v1')",
    )
    op.drop_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"),
        "task_runs",
        "(task_type = 'novel_compile' AND provider_setting_id IS NULL AND provider IS NULL "
        "AND model_id IS NULL AND provider_config_version IS NULL) OR "
        "(task_type <> 'novel_compile' AND provider_setting_id IS NOT NULL "
        "AND provider IS NOT NULL "
        "AND model_id IS NOT NULL AND provider_config_version IS NOT NULL)",
    )
