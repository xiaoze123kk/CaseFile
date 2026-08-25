"""Pure Narrative Compiler foundation contracts and semantic gates."""

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    build_artifact_ref,
    canonical_json_sha256,
    validate_compile_input_manifest,
    validate_diagnostic,
    validate_source_ref,
)
from casefile.domain.narrative_compiler.narrative_ir import (
    NARRATIVE_IR_PROJECTION_VERSION,
    NARRATIVE_IR_SCHEMA_ID,
    REFERENCE_FIELD_SPECS,
    narrative_ir_component_fingerprint,
    project_narrative_ir,
    project_narrative_ir_json,
    validate_narrative_ir,
)
from casefile.domain.narrative_compiler.novel_plan import (
    NOVEL_PLAN_CANDIDATE_SCHEMA_ID,
    NOVEL_PLAN_SCHEMA_ID,
    STORY_PLANNER_COMPONENT_VERSION,
    canonicalize_novel_plan,
    story_planner_component_fingerprint,
    validate_novel_plan_candidate,
)
from casefile.domain.narrative_compiler.planner_input import (
    PLANNER_INPUT_SCHEMA_ID,
    build_planner_input_bundle,
    planner_input_fingerprint,
)
from casefile.domain.narrative_compiler.source_refs import (
    build_source_ref,
    resolve_source_fragment,
    validate_source_ref_against_value,
)

__all__ = [
    "CompilerContractError",
    "NOVEL_PLAN_CANDIDATE_SCHEMA_ID",
    "NOVEL_PLAN_SCHEMA_ID",
    "PLANNER_INPUT_SCHEMA_ID",
    "STORY_PLANNER_COMPONENT_VERSION",
    "build_planner_input_bundle",
    "canonicalize_novel_plan",
    "build_artifact_ref",
    "canonical_json_sha256",
    "build_source_ref",
    "NARRATIVE_IR_PROJECTION_VERSION",
    "NARRATIVE_IR_SCHEMA_ID",
    "narrative_ir_component_fingerprint",
    "planner_input_fingerprint",
    "project_narrative_ir",
    "project_narrative_ir_json",
    "REFERENCE_FIELD_SPECS",
    "resolve_source_fragment",
    "validate_compile_input_manifest",
    "validate_diagnostic",
    "validate_source_ref",
    "validate_source_ref_against_value",
    "validate_narrative_ir",
    "story_planner_component_fingerprint",
    "validate_novel_plan_candidate",
]
