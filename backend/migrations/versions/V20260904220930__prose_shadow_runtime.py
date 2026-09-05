"""prose_shadow_runtime

Revision ID: 20260904220930
Revises: 20260903224420
Create Date: 2026-09-04 22:09:31.466010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904220930"
down_revision: str | None = "20260903224420"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_IDENTITY = (
    "(artifact_kind = 'input_manifest' AND artifact_key = "
    "'compiler.input_manifest' AND schema_id = 'compiler.input-manifest.v1') "
    "OR (artifact_kind = 'narrative_ir' AND artifact_key = "
    "'compiler.narrative_ir' AND schema_id = 'compiler.narrative-ir.v1') OR "
    "(artifact_kind = 'novel_plan' AND artifact_key = 'compiler.novel_plan' "
    "AND schema_id = 'compiler.novel-plan.v1') OR (artifact_kind = "
    "'scene_plan' AND artifact_key = 'compiler.scene_plan' AND schema_id IN "
    "('compiler.scene-plan.v1', 'compiler.scene-plan.v2'))"
)
NEW_IDENTITY = (
    "COALESCE(((artifact_kind = 'input_manifest' AND artifact_key = "
    "'compiler.input_manifest' AND schema_id = 'compiler.input-manifest.v1') OR "
    "(artifact_kind = 'narrative_ir' AND artifact_key = 'compiler.narrative_ir' AND"
    " schema_id = 'compiler.narrative-ir.v1') OR (artifact_kind = 'novel_plan' AND "
    "artifact_key = 'compiler.novel_plan' AND schema_id = 'compiler.novel-plan.v1')"
    " OR (artifact_kind = 'scene_plan' AND artifact_key = 'compiler.scene_plan' AND"
    " schema_id IN ('compiler.scene-plan.v1', 'compiler.scene-plan.v2')) OR "
    "(content_jsonb->>'schema_id' = schema_id AND ((artifact_kind = 'scene_context'"
    " AND schema_id = 'compiler.prose-judge-checklist.v1' AND artifact_key = "
    "'compiler.scene_context.' || (content_jsonb->>'scene_id')) OR (artifact_kind ="
    " 'scene_render' AND schema_id = 'compiler.scene-render.v1' AND "
    "content_jsonb->>'stage' IN "
    "('writer','rewrite_1','rewrite_2','polished','accepted') AND artifact_key = "
    "'compiler.scene_render.' || (content_jsonb->>'scene_id') || '.' || "
    "(content_jsonb->>'stage')) OR (artifact_kind = 'validation_report' AND "
    "schema_id = 'compiler.prose-judge-report.v1' AND content_jsonb->>'role' IN "
    "('fidelity','adversarial','coherence','arbiter') AND artifact_key IN "
    "('compiler.validation_report.' || (content_jsonb->>'scene_id') || "
    "'.semantic_0.' || (content_jsonb->>'role'),'compiler.validation_report.' || "
    "(content_jsonb->>'scene_id') || '.semantic_1.' || "
    "(content_jsonb->>'role'),'compiler.validation_report.' || "
    "(content_jsonb->>'scene_id') || '.semantic_2.' || "
    "(content_jsonb->>'role'),'compiler.validation_report.' || "
    "(content_jsonb->>'scene_id') || '.preservation.' || (content_jsonb->>'role')))"
    " OR (artifact_kind = 'validation_report' AND schema_id = 'compiler.prose-"
    "consensus-report.v1' AND artifact_key IN ('compiler.validation_report.' || "
    "(content_jsonb->>'scene_id') || '.semantic_' || (content_jsonb->>'round') || "
    "'.consensus', 'compiler.validation_report.' || (content_jsonb->>'scene_id') ||"
    " '.preservation.consensus')) OR (artifact_kind = 'validation_report' AND "
    "schema_id = 'compiler.prose-quality-report.v1' AND "
    "((content_jsonb->>'report_kind' = 'findings' AND artifact_key = "
    "'compiler.validation_report.' || (content_jsonb->>'scene_id') || "
    "'.quality.findings') OR (content_jsonb->>'report_kind' = 'pairwise' AND "
    "((content_jsonb->'position_mapping'->>'a' = 'original' AND artifact_key = "
    "'compiler.validation_report.' || (content_jsonb->>'scene_id') || "
    "'.quality.pairwise.original_first') OR "
    "(content_jsonb->'position_mapping'->>'a' = 'polished' AND artifact_key = "
    "'compiler.validation_report.' || (content_jsonb->>'scene_id') || "
    "'.quality.pairwise.polished_first'))))) OR (artifact_kind = 'novel_candidate' "
    "AND artifact_key = 'compiler.novel_candidate' AND schema_id = 'compiler.novel-"
    "candidate.v1') OR (artifact_kind = 'compile_manifest' AND artifact_key = "
    "'compiler.compile_manifest' AND schema_id = 'compiler.compile-"
    "manifest.v1')))), false)"
)


def upgrade() -> None:
    op.add_column(
        "compile_runs",
        sa.Column(
            "prose_renderer_shadow", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("agent_model_calls", sa.Column("request_fingerprint", sa.String(64)))
    op.add_column("agent_model_calls", sa.Column("latency_ms", sa.Integer()))
    op.add_column(
        "agent_model_calls",
        sa.Column(
            "response_jsonb", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
        ),
    )
    op.add_column("agent_model_calls", sa.Column("parse_status", sa.String(40)))
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", NEW_IDENTITY
    )


def downgrade() -> None:
    # Existing Shadow artifacts deliberately prevent a lossy downgrade.
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", OLD_IDENTITY
    )
    for name in ("parse_status", "response_jsonb", "latency_ms", "request_fingerprint"):
        op.drop_column("agent_model_calls", name)
    op.drop_column("compile_runs", "prose_renderer_shadow")
