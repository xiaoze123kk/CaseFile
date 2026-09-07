"""prose_continuity_and_attempt_artifacts

Revision ID: 20260907210634
Revises: 20260904220930
Create Date: 2026-09-07 21:06:35.074753
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260907210634"
down_revision: str | None = "20260904220930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_IDENTITY = (
    "COALESCE(((artifact_kind = 'input_manifest' AND artifact_key = 'compiler.input_manife"
    "st' AND schema_id = 'compiler.input-manifest.v1') OR (artifact_kind = 'narrative_ir' "
    "AND artifact_key = 'compiler.narrative_ir' AND schema_id = 'compiler.narrative-ir.v1'"
    ") OR (artifact_kind = 'novel_plan' AND artifact_key = 'compiler.novel_plan' AND schem"
    "a_id = 'compiler.novel-plan.v1') OR (artifact_kind = 'scene_plan' AND artifact_key = "
    "'compiler.scene_plan' AND schema_id IN ('compiler.scene-plan.v1', 'compiler.scene-pla"
    "n.v2')) OR (content_jsonb->>'schema_id' = schema_id AND ((artifact_kind = 'scene_cont"
    "ext' AND schema_id = 'compiler.prose-judge-checklist.v1' AND artifact_key = 'compiler"
    ".scene_context.' || (content_jsonb->>'scene_id')) OR (artifact_kind = 'scene_render' "
    "AND schema_id = 'compiler.scene-render.v1' AND content_jsonb->>'stage' IN ('writer','"
    "rewrite_1','rewrite_2','polished','accepted') AND artifact_key = 'compiler.scene_rend"
    "er.' || (content_jsonb->>'scene_id') || '.' || (content_jsonb->>'stage')) OR (artifac"
    "t_kind = 'validation_report' AND schema_id = 'compiler.prose-judge-report.v1' AND con"
    "tent_jsonb->>'role' IN ('fidelity','adversarial','coherence','arbiter') AND artifact_"
    "key IN ('compiler.validation_report.' || (content_jsonb->>'scene_id') || '.semantic_0"
    ".' || (content_jsonb->>'role'),'compiler.validation_report.' || (content_jsonb->>'sce"
    "ne_id') || '.semantic_1.' || (content_jsonb->>'role'),'compiler.validation_report.' |"
    "| (content_jsonb->>'scene_id') || '.semantic_2.' || (content_jsonb->>'role'),'compile"
    "r.validation_report.' || (content_jsonb->>'scene_id') || '.preservation.' || (content"
    "_jsonb->>'role'))) OR (artifact_kind = 'validation_report' AND schema_id = 'compiler."
    "prose-consensus-report.v1' AND artifact_key IN ('compiler.validation_report.' || (con"
    "tent_jsonb->>'scene_id') || '.semantic_' || (content_jsonb->>'round') || '.consensus'"
    ", 'compiler.validation_report.' || (content_jsonb->>'scene_id') || '.preservation.con"
    "sensus')) OR (artifact_kind = 'validation_report' AND schema_id = 'compiler.prose-qua"
    "lity-report.v1' AND ((content_jsonb->>'report_kind' = 'findings' AND artifact_key = '"
    "compiler.validation_report.' || (content_jsonb->>'scene_id') || '.quality.findings') "
    "OR (content_jsonb->>'report_kind' = 'pairwise' AND ((content_jsonb->'position_mapping"
    "'->>'a' = 'original' AND artifact_key = 'compiler.validation_report.' || (content_jso"
    "nb->>'scene_id') || '.quality.pairwise.original_first') OR (content_jsonb->'position_"
    "mapping'->>'a' = 'polished' AND artifact_key = 'compiler.validation_report.' || (cont"
    "ent_jsonb->>'scene_id') || '.quality.pairwise.polished_first'))))) OR (artifact_kind "
    "= 'novel_candidate' AND artifact_key = 'compiler.novel_candidate' AND schema_id = 'co"
    "mpiler.novel-candidate.v1') OR (artifact_kind = 'compile_manifest' AND artifact_key ="
    " 'compiler.compile_manifest' AND schema_id = 'compiler.compile-manifest.v1')))), fals"
    "e)"
)
NEW_IDENTITY = (
    "(COALESCE(((artifact_kind = 'input_manifest' AND artifact_key = 'compiler.input_manif"
    "est' AND schema_id = 'compiler.input-manifest.v1') OR (artifact_kind = 'narrative_ir'"
    " AND artifact_key = 'compiler.narrative_ir' AND schema_id = 'compiler.narrative-ir.v1"
    "') OR (artifact_kind = 'novel_plan' AND artifact_key = 'compiler.novel_plan' AND sche"
    "ma_id = 'compiler.novel-plan.v1') OR (artifact_kind = 'scene_plan' AND artifact_key ="
    " 'compiler.scene_plan' AND schema_id IN ('compiler.scene-plan.v1', 'compiler.scene-pl"
    "an.v2')) OR (content_jsonb->>'schema_id' = schema_id AND ((artifact_kind = 'scene_con"
    "text' AND schema_id = 'compiler.prose-judge-checklist.v1' AND artifact_key = 'compile"
    "r.scene_context.' || (content_jsonb->>'scene_id')) OR (artifact_kind = 'scene_render'"
    " AND schema_id = 'compiler.scene-render.v1' AND content_jsonb->>'stage' IN ('writer',"
    "'rewrite_1','rewrite_2','polished','accepted') AND artifact_key = 'compiler.scene_ren"
    "der.' || (content_jsonb->>'scene_id') || '.' || (content_jsonb->>'stage')) OR (artifa"
    "ct_kind = 'validation_report' AND schema_id = 'compiler.prose-judge-report.v1' AND co"
    "ntent_jsonb->>'role' IN ('fidelity','adversarial','coherence','arbiter') AND artifact"
    "_key IN ('compiler.validation_report.' || (content_jsonb->>'scene_id') || '.semantic_"
    "0.' || (content_jsonb->>'role'),'compiler.validation_report.' || (content_jsonb->>'sc"
    "ene_id') || '.semantic_1.' || (content_jsonb->>'role'),'compiler.validation_report.' "
    "|| (content_jsonb->>'scene_id') || '.semantic_2.' || (content_jsonb->>'role'),'compil"
    "er.validation_report.' || (content_jsonb->>'scene_id') || '.preservation.' || (conten"
    "t_jsonb->>'role'))) OR (artifact_kind = 'validation_report' AND schema_id = 'compiler"
    ".prose-consensus-report.v1' AND artifact_key IN ('compiler.validation_report.' || (co"
    "ntent_jsonb->>'scene_id') || '.semantic_' || (content_jsonb->>'round') || '.consensus"
    "', 'compiler.validation_report.' || (content_jsonb->>'scene_id') || '.preservation.co"
    "nsensus')) OR (artifact_kind = 'validation_report' AND schema_id = 'compiler.prose-qu"
    "ality-report.v1' AND ((content_jsonb->>'report_kind' = 'findings' AND artifact_key = "
    "'compiler.validation_report.' || (content_jsonb->>'scene_id') || '.quality.findings')"
    " OR (content_jsonb->>'report_kind' = 'pairwise' AND ((content_jsonb->'position_mappin"
    "g'->>'a' = 'original' AND artifact_key = 'compiler.validation_report.' || (content_js"
    "onb->>'scene_id') || '.quality.pairwise.original_first') OR (content_jsonb->'position"
    "_mapping'->>'a' = 'polished' AND artifact_key = 'compiler.validation_report.' || (con"
    "tent_jsonb->>'scene_id') || '.quality.pairwise.polished_first'))))) OR (artifact_kind"
    " = 'novel_candidate' AND artifact_key = 'compiler.novel_candidate' AND schema_id = 'c"
    "ompiler.novel-candidate.v1') OR (artifact_kind = 'compile_manifest' AND artifact_key "
    "= 'compiler.compile_manifest' AND schema_id = 'compiler.compile-manifest.v1')))), fal"
    "se)) OR ((artifact_kind IN ('scene_render','validation_report','compile_manifest')) A"
    "ND artifact_key ~ '\\.attempt_[1-9][0-9]*$' AND COALESCE(((artifact_kind = 'input_mani"
    "fest' AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.inpu"
    "t_manifest' AND schema_id = 'compiler.input-manifest.v1') OR (artifact_kind = 'narrat"
    "ive_ir' AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.na"
    "rrative_ir' AND schema_id = 'compiler.narrative-ir.v1') OR (artifact_kind = 'novel_pl"
    "an' AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.novel_"
    "plan' AND schema_id = 'compiler.novel-plan.v1') OR (artifact_kind = 'scene_plan' AND "
    "regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.scene_plan' AN"
    "D schema_id IN ('compiler.scene-plan.v1', 'compiler.scene-plan.v2')) OR (content_json"
    "b->>'schema_id' = schema_id AND ((artifact_kind = 'scene_context' AND schema_id = 'co"
    "mpiler.prose-judge-checklist.v1' AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-"
    "9]*$', '') = 'compiler.scene_context.' || (content_jsonb->>'scene_id')) OR (artifact_"
    "kind = 'scene_render' AND schema_id = 'compiler.scene-render.v1' AND content_jsonb->>"
    "'stage' IN ('writer','rewrite_1','rewrite_2','polished','accepted') AND regexp_replac"
    "e(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.scene_render.' || (content_"
    "jsonb->>'scene_id') || '.' || (content_jsonb->>'stage')) OR (artifact_kind = 'validat"
    "ion_report' AND schema_id = 'compiler.prose-judge-report.v1' AND content_jsonb->>'rol"
    "e' IN ('fidelity','adversarial','coherence','arbiter') AND regexp_replace(artifact_ke"
    "y, '\\.attempt_[1-9][0-9]*$', '') IN ('compiler.validation_report.' || (content_jsonb-"
    ">>'scene_id') || '.semantic_0.' || (content_jsonb->>'role'),'compiler.validation_repo"
    "rt.' || (content_jsonb->>'scene_id') || '.semantic_1.' || (content_jsonb->>'role'),'c"
    "ompiler.validation_report.' || (content_jsonb->>'scene_id') || '.semantic_2.' || (con"
    "tent_jsonb->>'role'),'compiler.validation_report.' || (content_jsonb->>'scene_id') ||"
    " '.preservation.' || (content_jsonb->>'role'))) OR (artifact_kind = 'validation_repor"
    "t' AND schema_id = 'compiler.prose-consensus-report.v1' AND regexp_replace(artifact_k"
    "ey, '\\.attempt_[1-9][0-9]*$', '') IN ('compiler.validation_report.' || (content_jsonb"
    "->>'scene_id') || '.semantic_' || (content_jsonb->>'round') || '.consensus', 'compile"
    "r.validation_report.' || (content_jsonb->>'scene_id') || '.preservation.consensus')) "
    "OR (artifact_kind = 'validation_report' AND schema_id = 'compiler.prose-quality-repor"
    "t.v1' AND ((content_jsonb->>'report_kind' = 'findings' AND regexp_replace(artifact_ke"
    "y, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.validation_report.' || (content_jsonb->>"
    "'scene_id') || '.quality.findings') OR (content_jsonb->>'report_kind' = 'pairwise' AN"
    "D ((content_jsonb->'position_mapping'->>'a' = 'original' AND regexp_replace(artifact_"
    "key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.validation_report.' || (content_jsonb-"
    ">>'scene_id') || '.quality.pairwise.original_first') OR (content_jsonb->'position_map"
    "ping'->>'a' = 'polished' AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]*$', '"
    "') = 'compiler.validation_report.' || (content_jsonb->>'scene_id') || '.quality.pairw"
    "ise.polished_first'))))) OR (artifact_kind = 'novel_candidate' AND regexp_replace(art"
    "ifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.novel_candidate' AND schema_id ="
    " 'compiler.novel-candidate.v1') OR (artifact_kind = 'compile_manifest' AND regexp_rep"
    "lace(artifact_key, '\\.attempt_[1-9][0-9]*$', '') = 'compiler.compile_manifest' AND sc"
    "hema_id = 'compiler.compile-manifest.v1')))), false)) OR COALESCE((artifact_kind = 'v"
    "alidation_report' AND schema_id = 'compiler.prose-continuity-review.v1' AND content_j"
    "sonb->>'schema_id' = schema_id AND regexp_replace(artifact_key, '\\.attempt_[1-9][0-9]"
    "*$', '') = 'compiler.continuity.' || (content_jsonb->>'scene_id')), false)"
)


def _replace(expression: str) -> None:
    op.drop_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_compile_artifacts_identity_allowed"), "compile_artifacts", expression
    )


def upgrade() -> None:
    _replace(NEW_IDENTITY)


def downgrade() -> None:
    _replace(OLD_IDENTITY)
