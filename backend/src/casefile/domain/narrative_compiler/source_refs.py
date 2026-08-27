"""Pure provenance helpers for deterministic Compiler source references."""

from __future__ import annotations

from typing import Any

from casefile_contracts import CompilerSourceRef

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
    validate_source_ref,
)


def build_source_ref(
    object_ref: dict[str, str], field_path: str, fragment: Any
) -> CompilerSourceRef:
    """Build and validate one object-relative, content-bound source reference."""

    source_ref = CompilerSourceRef.model_validate(
        {
            "object_ref": object_ref,
            "field_path": field_path,
            "source_fragment_hash": canonical_json_sha256(fragment),
        }
    )
    return validate_source_ref(source_ref)


def resolve_source_fragment(value: Any, field_path: str) -> Any:
    """Resolve an RFC 6901 pointer without accepting array-index traversal."""

    if field_path == "":
        return value
    segments = field_path[1:].split("/")
    current = value
    for encoded in segments:
        segment = encoded.replace("~1", "/").replace("~0", "~")
        if segment.isdecimal():
            raise CompilerContractError("compiler_source_ref_array_index_forbidden")
        if not isinstance(current, dict) or segment not in current:
            raise CompilerContractError("compiler_source_ref_path_invalid")
        current = current[segment]
    return current


def validate_source_ref_against_value(
    source_ref: CompilerSourceRef, object_value: Any
) -> CompilerSourceRef:
    """Re-resolve and hash a source reference against its authoritative value."""

    validate_source_ref(source_ref)
    fragment = resolve_source_fragment(object_value, source_ref.field_path)
    if canonical_json_sha256(fragment) != source_ref.source_fragment_hash:
        raise CompilerContractError("compiler_source_ref_hash_mismatch")
    return source_ref


__all__ = [
    "build_source_ref",
    "resolve_source_fragment",
    "validate_source_ref_against_value",
]
