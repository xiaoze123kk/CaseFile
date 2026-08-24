from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_artifact_ref,
    canonical_json_sha256,
    validate_compile_input_manifest,
    validate_diagnostic,
    validate_source_ref,
)
from casefile_contracts import (
    ArtifactKind,
    CompileInputManifest,
    CompilerDiagnostic,
    CompilerSourceRef,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "compiler" / "foundation"


def _load(name: str) -> Any:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _manifest(name: str) -> CompileInputManifest:
    return CompileInputManifest.model_validate(_load(name))


def test_canonical_json_hash_is_key_order_stable_and_array_order_sensitive() -> None:
    left = {"profile": {"language": "zh-CN", "constraints": ["a", "b"]}, "version": 1}
    right = {"version": 1, "profile": {"constraints": ["a", "b"], "language": "zh-CN"}}
    reordered = {"version": 1, "profile": {"constraints": ["b", "a"], "language": "zh-CN"}}

    assert canonical_json_sha256(left) == canonical_json_sha256(right)
    assert canonical_json_sha256(left) != canonical_json_sha256(reordered)

    with pytest.raises(CompilerContractError) as captured:
        canonical_json_sha256({"invalid": float("nan")})
    assert captured.value.reason_code == "compiler_hash_value_invalid"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "preview_minimal.input_manifest.json",
        "canonical.input_manifest.json",
        "preview_with_exposure.input_manifest.json",
    ],
)
def test_valid_manifests_pass_semantic_binding(fixture_name: str) -> None:
    manifest = _manifest(fixture_name)
    assert validate_compile_input_manifest(manifest) is manifest
    assert len(canonical_json_sha256(manifest.model_dump(mode="json"))) == 64


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda value: value.update(mode="canonical"),
            "compiler_manifest_canon_required",
        ),
        (
            lambda value: value["source_canon"].update(source_snapshot_id=999),
            "compiler_manifest_canon_snapshot_mismatch",
        ),
        (
            lambda value: value["source_canon"].update(content_hash="b" * 64),
            "compiler_manifest_canon_hash_mismatch",
        ),
        (
            lambda value: value["profile"]["frozen_payload"].update(language="en-US"),
            "compiler_manifest_profile_hash_mismatch",
        ),
        (
            lambda value: value["exposure"].update(draft_id=12),
            "compiler_manifest_exposure_draft_mismatch",
        ),
        (
            lambda value: value["exposure"]["frozen_payload"]["entries"].append(
                {"title": "开场"}
            ),
            "compiler_manifest_exposure_hash_mismatch",
        ),
    ],
)
def test_manifest_semantic_failures_expose_stable_reason_codes(
    mutate: Any,
    reason_code: str,
) -> None:
    if "canon" in reason_code:
        value = _load("canonical.input_manifest.json")
        if reason_code == "compiler_manifest_canon_required":
            value = _load("preview_minimal.input_manifest.json")
    elif "exposure" in reason_code:
        value = _load("preview_with_exposure.input_manifest.json")
    else:
        value = _load("preview_minimal.input_manifest.json")
    mutate(value)
    manifest = CompileInputManifest.model_validate(value)

    with pytest.raises(CompilerContractError) as captured:
        validate_compile_input_manifest(manifest)
    assert captured.value.reason_code == reason_code


def test_preview_manifest_rejects_canon_even_when_dto_can_represent_it() -> None:
    value = _load("canonical.input_manifest.json")
    value["mode"] = "preview"
    manifest = CompileInputManifest.model_validate(value)

    with pytest.raises(CompilerContractError) as captured:
        validate_compile_input_manifest(manifest)
    assert captured.value.reason_code == "compiler_manifest_preview_canon_forbidden"


@pytest.mark.parametrize("field_path", ["", "/time/start", "/a~1b/~0value"])
def test_source_ref_accepts_root_fields_and_escaped_pointer_segments(field_path: str) -> None:
    value = _load("source_ref.json")
    value["field_path"] = field_path
    source_ref = CompilerSourceRef.model_validate(value)
    assert validate_source_ref(source_ref) is source_ref


@pytest.mark.parametrize("field_path", ["/participant_refs/0", "/items/01/value"])
def test_source_ref_rejects_numeric_array_segments(field_path: str) -> None:
    value = _load("source_ref.json")
    value["field_path"] = field_path

    with pytest.raises(CompilerContractError) as captured:
        validate_source_ref(CompilerSourceRef.model_validate(value))
    assert captured.value.reason_code == "compiler_source_ref_array_index_forbidden"


def test_artifact_ref_is_content_addressed_and_validated() -> None:
    payload = {"target": "novel", "scenes": ["scene_001", "scene_002"]}
    same = deepcopy(payload)
    changed = {"target": "novel", "scenes": ["scene_002", "scene_001"]}

    first = build_artifact_ref(
        ArtifactKind.input_manifest,
        "compiler.input_manifest",
        "compiler.input-manifest.v1",
        payload,
    )
    second = build_artifact_ref(
        "input_manifest",
        "compiler.input_manifest",
        "compiler.input-manifest.v1",
        same,
    )
    third = build_artifact_ref(
        "input_manifest",
        "compiler.input_manifest",
        "compiler.input-manifest.v1",
        changed,
    )

    assert first == second
    assert first.content_hash != third.content_hash

    with pytest.raises(CompilerContractError) as captured:
        build_artifact_ref("unknown", "Invalid Key", "schema", payload)
    assert captured.value.reason_code == "compiler_artifact_ref_invalid"


def test_diagnostic_preserves_ref_order_and_rejects_duplicates() -> None:
    value = _load("diagnostic.json")
    diagnostic = CompilerDiagnostic.model_validate(value)
    assert validate_diagnostic(diagnostic) is diagnostic

    duplicate = CompilerDiagnostic.model_validate(
        {**value, "source_refs": [*value["source_refs"], *value["source_refs"]]}
    )
    with pytest.raises(CompilerContractError) as captured:
        validate_diagnostic(duplicate)
    assert captured.value.reason_code == "compiler_diagnostic_source_ref_duplicate"
