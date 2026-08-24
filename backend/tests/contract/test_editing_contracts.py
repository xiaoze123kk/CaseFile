from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "schemas"
FIXTURE_ROOT = REPO_ROOT / "fixtures"
GENERATED_PYTHON_SRC = REPO_ROOT / "contracts" / "generated" / "python" / "src"
sys.path.insert(0, str(GENERATED_PYTHON_SRC))

from casefile_contracts import (  # noqa: E402
    Brief,
    CaseFile,
    CompileInputManifest,
    CompilerArtifactRef,
    CompilerDiagnostic,
    CompilerSourceRef,
    PatchCandidate,
    TaskRun,
    ValidationIssue,
)

CORE_COLLECTIONS = {
    "resolution_specs",
    "entities",
    "relationships",
    "locations",
    "events",
    "information_units",
    "claims",
    "hypotheses",
    "reasoning_paths",
    "constraints",
    "structure_locks",
}

OBJECT_PREFIXES = {
    "casefile": "case_",
    "resolution_spec": "res_",
    "entity": "ent_",
    "relationship": "rel_",
    "location": "loc_",
    "event": "evt_",
    "information_unit": "info_",
    "claim": "claim_",
    "hypothesis": "hyp_",
    "reasoning_path": "path_",
    "constraint": "con_",
    "structure_lock": "lock_",
    "source_fragment": "src_",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict[str, Any]]:
    return {
        schema["$id"]: schema
        for path in sorted(SCHEMA_ROOT.rglob("*.json"))
        if (schema := load_json(path))
    }


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    result = Registry()
    for schema_id, schema in schemas.items():
        result = result.with_resource(schema_id, Resource.from_contents(schema))
    return result


@pytest.fixture(scope="module")
def validators(
    schemas: dict[str, dict[str, Any]], registry: Registry
) -> dict[str, Draft202012Validator]:
    checker = FormatChecker()
    return {
        "casefile": Draft202012Validator(
            schemas["https://casefile.local/schemas/v2/casefile/casefile.schema.json"],
            registry=registry,
            format_checker=checker,
        ),
        "brief": Draft202012Validator(
            schemas["https://casefile.local/schemas/v2/brief/brief.schema.json"],
            registry=registry,
            format_checker=checker,
        ),
        "validation_issue": Draft202012Validator(
            schemas["https://casefile.local/schemas/v2/validation/validation-issue.schema.json"],
            registry=registry,
            format_checker=checker,
        ),
        "patch_candidate": Draft202012Validator(
            schemas["https://casefile.local/schemas/v2/casefile/patch-candidate.schema.json"],
            registry=registry,
            format_checker=checker,
        ),
        "task": Draft202012Validator(
            {
                "$ref": (
                    "https://casefile.local/schemas/v2/task/task.schema.json"
                    "#/$defs/TaskRun"
                )
            },
            registry=registry,
            format_checker=checker,
        ),
        "compiler_manifest": Draft202012Validator(
            {
                "$ref": (
                    "https://casefile.local/schemas/v2/compiler/compiler.schema.json"
                    "#/$defs/CompileInputManifest"
                )
            },
            registry=registry,
            format_checker=checker,
        ),
    }


def decode_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"Invalid JSON Pointer: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def apply_mutation(document: Any, mutation: dict[str, Any]) -> Any:
    result = copy.deepcopy(document)
    parts = decode_pointer(mutation["path"])
    if not parts:
        raise ValueError("Fixture mutations must address a child of the document root")

    parent = result
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]

    key = parts[-1]
    operation = mutation["op"]
    if isinstance(parent, list):
        index = len(parent) if key == "-" else int(key)
        if operation == "add":
            parent.insert(index, mutation["value"])
        elif operation == "remove":
            parent.pop(index)
        else:
            parent[index] = mutation["value"]
    elif operation == "remove":
        del parent[key]
    else:
        parent[key] = mutation["value"]
    return result


def apply_manifest(base: Any, manifest: dict[str, Any]) -> Any:
    mutations = manifest.get("mutations", [manifest.get("mutation")])
    result = base
    for mutation in mutations:
        if mutation is not None:
            result = apply_mutation(result, mutation)
    return result


def error_path(error: Any) -> str:
    path = "/".join(str(part) for part in error.absolute_path)
    return f"/{path}" if path else "/"


def walk_object_refs(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if isinstance(value, dict):
        if set(value) == {"object_type", "object_id"}:
            refs.append(value)
        for child in value.values():
            refs.extend(walk_object_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(walk_object_refs(child))
    return refs


def test_all_schema_files_are_valid_draft_2020_12(
    schemas: dict[str, dict[str, Any]],
) -> None:
    assert len(schemas) == 10
    for schema in schemas.values():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


def test_casefiles_validate_and_cover_contract_foundation(
    validators: dict[str, Draft202012Validator],
) -> None:
    paths = sorted((FIXTURE_ROOT / "casefiles").glob("*.casefile.json"))
    assert len(paths) == 4

    question_types: set[str] = set()
    conclusion_modes: set[str] = set()
    populated_collections: set[str] = set()
    confirmation_statuses: set[str] = set()

    for path in paths:
        casefile = load_json(path)
        validators["casefile"].validate(casefile)
        generated = CaseFile.model_validate(casefile)
        roundtripped = generated.model_dump(mode="json", by_alias=True, exclude_unset=True)
        assert json.loads(json.dumps(roundtripped, ensure_ascii=False)) == casefile

        for resolution in casefile["resolution_specs"]:
            question_types.add(resolution["question_type"])
            conclusion_modes.add(resolution["conclusion_mode"])
        for collection in CORE_COLLECTIONS:
            if casefile[collection]:
                populated_collections.add(collection)
        for value in casefile.values():
            if isinstance(value, list):
                confirmation_statuses.update(
                    item["confirmation_status"]
                    for item in value
                    if isinstance(item, dict) and "confirmation_status" in item
                )

        for object_ref in walk_object_refs(casefile):
            assert object_ref["object_id"].startswith(OBJECT_PREFIXES[object_ref["object_type"]])

    assert len(question_types) >= 4
    assert len(conclusion_modes) >= 3
    assert populated_collections == CORE_COLLECTIONS
    assert {"user_confirmed", "ai_inferred"} <= confirmation_statuses


def test_location_spatial_positions_are_optional_strict_and_bounded(
    validators: dict[str, Draft202012Validator],
) -> None:
    fixture = load_json(FIXTURE_ROOT / "casefiles" / "restart_loop.casefile.json")
    positions = [location["spatial_position"] for location in fixture["locations"]]
    assert positions == [
        {"coordinate_system": "schematic", "x": 28, "y": 42},
        {
            "coordinate_system": "wgs84",
            "latitude": 31.2304,
            "longitude": 121.4737,
        },
    ]

    generated = CaseFile.model_validate(fixture)
    assert generated.model_dump(mode="json", by_alias=True, exclude_unset=True) == fixture

    legacy = copy.deepcopy(fixture)
    for location in legacy["locations"]:
        location.pop("spatial_position")
    validators["casefile"].validate(legacy)
    assert (
        CaseFile.model_validate(legacy).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )
        == legacy
    )

    invalid_positions = (
        {"coordinate_system": "schematic", "x": -0.01, "y": 50},
        {"coordinate_system": "schematic", "x": 50, "y": 100.01},
        {"coordinate_system": "schematic", "x": 50, "y": 50, "latitude": 0},
        {"coordinate_system": "wgs84", "latitude": -90.01, "longitude": 0},
        {"coordinate_system": "wgs84", "latitude": 0, "longitude": 180.01},
        {"coordinate_system": "wgs84", "latitude": 0, "longitude": 0, "x": 50},
        {"coordinate_system": "local", "x": 50, "y": 50},
    )
    for position in invalid_positions:
        invalid = copy.deepcopy(fixture)
        invalid["locations"][0]["spatial_position"] = position
        errors = list(validators["casefile"].iter_errors(invalid))
        assert errors, position
        assert any(
            error_path(error).startswith("/locations/0/spatial_position")
            for error in errors
        )
        with pytest.raises(ValidationError):
            CaseFile.model_validate(invalid)


def test_validation_issue_and_patch_candidate_validate_in_both_python_layers(
    validators: dict[str, Draft202012Validator],
) -> None:
    issue = load_json(FIXTURE_ROOT / "editing" / "validation_issue.json")
    patch = load_json(FIXTURE_ROOT / "editing" / "patch_candidate.json")

    validators["validation_issue"].validate(issue)
    validators["patch_candidate"].validate(patch)
    assert (
        ValidationIssue.model_validate(issue).model_dump(mode="json", exclude_unset=True) == issue
    )
    assert PatchCandidate.model_validate(patch).model_dump(mode="json", exclude_unset=True) == patch

    operation = patch["operations"][0]
    assert operation["target_ref"]["object_id"] == "evt_restart_seven"
    assert operation["path"] == "/time/start"
    assert "/events/0" not in operation["path"]


def test_target_neutral_brief_roundtrips_and_enforces_resolution_mode(
    validators: dict[str, Draft202012Validator],
) -> None:
    fixture = load_json(FIXTURE_ROOT / "benchmark" / "brief_to_draft.json")["brief"]
    validators["brief"].validate(fixture)
    generated = Brief.model_validate(fixture)
    assert generated.model_dump(mode="json", exclude_unset=True) == fixture
    assert "player_goal" not in fixture

    anchored_without_answer = copy.deepcopy(fixture)
    anchored_without_answer["author_answer"] = None
    assert list(validators["brief"].iter_errors(anchored_without_answer))

    open_with_hidden_answer = copy.deepcopy(fixture)
    open_with_hidden_answer["resolution_mode"] = "open"
    assert list(validators["brief"].iter_errors(open_with_hidden_answer))

    duplicate_sources = copy.deepcopy(fixture)
    duplicate_sources["source_record_ids"] = [1, 1]
    assert list(validators["brief"].iter_errors(duplicate_sources))


def test_casefile_chat_task_roundtrips_with_message_lineage(
    validators: dict[str, Draft202012Validator],
) -> None:
    task = {
        "task_run_id": 21,
        "project_id": 8,
        "task_type": "casefile_chat",
        "status": "succeeded",
        "stage": "completed",
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "input_draft_revision": 4,
        "input_brief_revision": None,
        "input_source_record_id": None,
        "input_brief_intake_id": None,
        "input_brief_intake_revision": None,
        "base_brief_intake_candidate_id": None,
        "agent_thread_id": 34,
        "input_message_id": 55,
        "output_message_id": 56,
        "input_hash": "a" * 64,
        "attempt_count": 1,
        "usage": {"total_tokens": 120},
        "result": {
            "answer": "已结合完整卷宗给出建议。",
            "referenced_object_ids": ["evt_restart"],
            "patch_set_id": 13,
            "stale": False,
        },
        "failure": None,
        "candidate_strategy": None,
        "component_steps": [],
    }

    validators["task"].validate(task)
    assert (
        TaskRun.model_validate(task).model_dump(mode="json", exclude_unset=True) == task
    )

    missing_lineage = copy.deepcopy(task)
    del missing_lineage["agent_thread_id"]
    assert list(validators["task"].iter_errors(missing_lineage))


def test_compiler_foundation_contracts_roundtrip_and_reject_invalid_shapes(
    validators: dict[str, Draft202012Validator],
    registry: Registry,
) -> None:
    compiler_root = FIXTURE_ROOT / "compiler" / "foundation"
    for name in (
        "preview_minimal.input_manifest.json",
        "canonical.input_manifest.json",
        "preview_with_exposure.input_manifest.json",
    ):
        value = load_json(compiler_root / name)
        validators["compiler_manifest"].validate(value)
        assert (
            CompileInputManifest.model_validate(value).model_dump(mode="json")
            == value
        )

    source_ref = load_json(compiler_root / "source_ref.json")
    artifact_ref = load_json(compiler_root / "artifact_ref.json")
    diagnostic = load_json(compiler_root / "diagnostic.json")
    assert CompilerSourceRef.model_validate(source_ref).model_dump(mode="json") == source_ref
    assert CompilerArtifactRef.model_validate(artifact_ref).model_dump(mode="json") == artifact_ref
    assert CompilerDiagnostic.model_validate(diagnostic).model_dump(mode="json") == diagnostic

    duplicate_sources = copy.deepcopy(diagnostic)
    duplicate_sources["source_refs"].append(copy.deepcopy(source_ref))
    diagnostic_validator = Draft202012Validator(
        {
            "$ref": (
                "https://casefile.local/schemas/v2/compiler/compiler.schema.json"
                "#/$defs/CompilerDiagnostic"
            )
        },
        registry=registry,
        format_checker=FormatChecker(),
    )
    assert list(diagnostic_validator.iter_errors(duplicate_sources))

    invalid_cases = load_json(compiler_root / "invalid_cases.json")["cases"]
    validators_by_fixture = {
        "source_ref.json": Draft202012Validator(
            {
                "$ref": (
                    "https://casefile.local/schemas/v2/compiler/compiler.schema.json"
                    "#/$defs/CompilerSourceRef"
                )
            },
            registry=registry,
        ),
        "artifact_ref.json": Draft202012Validator(
            {
                "$ref": (
                    "https://casefile.local/schemas/v2/compiler/compiler.schema.json"
                    "#/$defs/CompilerArtifactRef"
                )
            },
            registry=registry,
        ),
    }
    for invalid_case in invalid_cases:
        if invalid_case["expected_layer"] != "schema":
            continue
        base_name = invalid_case["base_fixture"]
        invalid_value = apply_manifest(load_json(compiler_root / base_name), invalid_case)
        validator = validators_by_fixture.get(base_name, validators["compiler_manifest"])
        assert list(validator.iter_errors(invalid_value)), invalid_case["name"]


def test_structural_invalid_fixtures_are_rejected_at_expected_paths(
    validators: dict[str, Draft202012Validator],
) -> None:
    fixture_dir = FIXTURE_ROOT / "invalid" / "schema"
    manifests = sorted(fixture_dir.glob("*.json"))
    assert len(manifests) == 5

    for manifest_path in manifests:
        manifest = load_json(manifest_path)
        base = load_json((manifest_path.parent / manifest["base_fixture"]).resolve())
        invalid_casefile = apply_manifest(base, manifest)
        errors = list(validators["casefile"].iter_errors(invalid_casefile))
        assert errors, f"{manifest_path.name} unexpectedly passed JSON Schema"
        paths = {error_path(error) for error in errors}
        assert manifest["expected_error_path"] in paths, (
            manifest_path.name,
            sorted(paths),
        )


def test_semantic_fixtures_are_classified_but_not_schema_failures(
    validators: dict[str, Draft202012Validator],
) -> None:
    fixture_path = FIXTURE_ROOT / "invalid" / "semantic" / "core_invariants.json"
    fixture_group = load_json(fixture_path)
    scenarios = fixture_group["scenarios"]
    assert {scenario["invariant_id"] for scenario in scenarios} == {
        f"CF-I-{index:03d}" for index in range(1, 11)
    }

    for scenario in scenarios:
        if "base_casefile" not in scenario:
            continue
        base = load_json((fixture_path.parent / scenario["base_casefile"]).resolve())
        mutated = apply_mutation(base, scenario["mutation"])
        validators["casefile"].validate(mutated)


def test_import_and_three_way_conflict_fixtures_preserve_provenance_and_stable_refs() -> None:
    import_fixture = load_json(FIXTURE_ROOT / "imports" / "mixed_confirmation.import.json")
    expected_statuses = {
        fragment["expected_confirmation_status"] for fragment in import_fixture["source_fragments"]
    }
    assert expected_statuses == {"user_confirmed", "ai_inferred"}

    scenario = load_json(FIXTURE_ROOT / "editing" / "three_way_conflict.scenario.json")
    current_change = scenario["current_change"]
    assert current_change["target_ref"] == {
        "object_type": "event",
        "object_id": "evt_restart_seven",
    }
    assert current_change["path"] == "/time/start"
    assert scenario["expected_result"] == "three_way_diff_required"
