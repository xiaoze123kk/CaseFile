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
from casefile.domain.narrative_compiler.source_refs import (
    build_source_ref,
    resolve_source_fragment,
    validate_source_ref_against_value,
)

__all__ = [
    "CompilerContractError",
    "build_artifact_ref",
    "canonical_json_sha256",
    "build_source_ref",
    "NARRATIVE_IR_PROJECTION_VERSION",
    "NARRATIVE_IR_SCHEMA_ID",
    "narrative_ir_component_fingerprint",
    "project_narrative_ir",
    "project_narrative_ir_json",
    "REFERENCE_FIELD_SPECS",
    "resolve_source_fragment",
    "validate_compile_input_manifest",
    "validate_diagnostic",
    "validate_source_ref",
    "validate_source_ref_against_value",
    "validate_narrative_ir",
]
