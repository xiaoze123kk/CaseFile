"""Deterministic hashing and semantic validation for N4.0 contracts."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785
from casefile_contracts import (
    ArtifactKind,
    CompileInputManifest,
    CompilerArtifactRef,
    CompilerDiagnostic,
    CompilerSourceRef,
)
from pydantic import ValidationError


class CompilerContractError(ValueError):
    """Stable fail-closed error for Compiler foundation invariants."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def canonical_json_sha256(value: Any) -> str:
    """Return lowercase SHA-256 over RFC 8785 canonical JSON bytes."""

    try:
        canonical = rfc8785.dumps(value)
    except (TypeError, ValueError) as error:
        raise CompilerContractError("compiler_hash_value_invalid") from error
    return hashlib.sha256(canonical).hexdigest()


def validate_source_ref(source_ref: CompilerSourceRef) -> CompilerSourceRef:
    """Reject unstable array-index paths while retaining object-relative pointers."""

    if source_ref.field_path:
        encoded_segments = source_ref.field_path[1:].split("/")
        segments = tuple(
            segment.replace("~1", "/").replace("~0", "~")
            for segment in encoded_segments
        )
        if any(segment.isdecimal() for segment in segments):
            raise CompilerContractError("compiler_source_ref_array_index_forbidden")
    return source_ref


def validate_compile_input_manifest(
    manifest: CompileInputManifest,
) -> CompileInputManifest:
    """Validate cross-binding invariants that JSON Schema codegen cannot express."""

    snapshot = manifest.source_snapshot
    canon = manifest.source_canon
    if manifest.mode.value == "canonical":
        if canon is None:
            raise CompilerContractError("compiler_manifest_canon_required")
        if canon.source_snapshot_id != snapshot.snapshot_id:
            raise CompilerContractError("compiler_manifest_canon_snapshot_mismatch")
        if canon.content_hash != snapshot.content_hash:
            raise CompilerContractError("compiler_manifest_canon_hash_mismatch")
    elif canon is not None:
        raise CompilerContractError("compiler_manifest_preview_canon_forbidden")

    profile = manifest.profile
    if canonical_json_sha256(profile.frozen_payload) != profile.content_hash:
        raise CompilerContractError("compiler_manifest_profile_hash_mismatch")

    exposure = manifest.exposure
    if exposure is not None:
        if exposure.draft_id != snapshot.draft_id:
            raise CompilerContractError("compiler_manifest_exposure_draft_mismatch")
        if canonical_json_sha256(exposure.frozen_payload) != exposure.content_hash:
            raise CompilerContractError("compiler_manifest_exposure_hash_mismatch")
    return manifest


def validate_diagnostic(diagnostic: CompilerDiagnostic) -> CompilerDiagnostic:
    """Validate ordered diagnostic refs without normalizing their authored order."""

    source_hashes_by_logical_key: dict[tuple[str, str, str], str] = {}
    for source_ref in diagnostic.source_refs:
        validate_source_ref(source_ref)
        object_ref = source_ref.object_ref.model_dump(mode="json")
        logical_key = (
            str(object_ref["object_type"]),
            str(object_ref["object_id"]),
            source_ref.field_path,
        )
        existing_hash = source_hashes_by_logical_key.get(logical_key)
        if existing_hash == source_ref.source_fragment_hash:
            raise CompilerContractError("compiler_source_ref_duplicate")
        if existing_hash is not None:
            raise CompilerContractError("compiler_source_ref_hash_conflict")
        source_hashes_by_logical_key[logical_key] = source_ref.source_fragment_hash
    return diagnostic


def build_artifact_ref(
    artifact_kind: ArtifactKind | str,
    artifact_key: str,
    schema_id: str,
    payload: Any,
) -> CompilerArtifactRef:
    """Build a validated content-addressed reference for an immutable artifact."""

    content_hash = canonical_json_sha256(payload)
    try:
        resolved_kind = ArtifactKind(artifact_kind)
        return CompilerArtifactRef(
            artifact_kind=resolved_kind,
            artifact_key=artifact_key,
            schema_id=schema_id,
            content_hash=content_hash,
        )
    except (ValidationError, ValueError) as error:
        raise CompilerContractError("compiler_artifact_ref_invalid") from error
