"""Load and validate the immutable CaseFile 0.1.0 contract."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator

CASEFILE_SCHEMA_VERSION = "0.1.0"


class ContractValidationError(ValueError):
    """A stable collection of structural or reference-integrity errors."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("CaseFile contract validation failed")
        self.errors = errors


@lru_cache(maxsize=1)
def load_casefile_schema() -> dict[str, Any]:
    """Load the packaged CaseFile 0.1.0 JSON Schema."""

    resource = files("casefile.contracts.schemas").joinpath("casefile-0.1.0.schema.json")
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def validate_casefile(document: dict[str, Any]) -> None:
    """Validate JSON shape first and then all stable-ID relationships."""

    validator = Draft202012Validator(load_casefile_schema())
    schema_errors = [
        {
            "code": "schema_invalid",
            "path": _json_pointer(list(error.absolute_path)),
            "message": error.message,
        }
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    ]
    if schema_errors:
        raise ContractValidationError(schema_errors)

    integrity_errors = _validate_integrity(document)
    if integrity_errors:
        raise ContractValidationError(integrity_errors)


def _validate_integrity(document: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    registry: dict[str, tuple[str, dict[str, Any]]] = {}

    collections = {
        "narrative_phases": "narrative_phase",
        "entities": "entity",
        "events": "event",
        "information_units": "information_unit",
        "claims": "claim",
        "hypotheses": "hypothesis",
        "reasoning_paths": "reasoning_path",
        "constraints": "constraint",
        "knowledge_states": "knowledge_state",
    }
    for collection_name, object_type in collections.items():
        for index, item in enumerate(document[collection_name]):
            object_id = item["object_id"]
            if object_id in registry:
                errors.append(
                    _error(
                        "duplicate_object_id",
                        f"/{collection_name}/{index}/object_id",
                        f"object_id {object_id!r} is already registered",
                    )
                )
            else:
                registry[object_id] = (object_type, item)

    resolution = document["resolution_spec"]
    if resolution is not None:
        object_id = resolution["object_id"]
        if object_id in registry:
            errors.append(
                _error(
                    "duplicate_object_id",
                    "/resolution_spec/object_id",
                    f"object_id {object_id!r} is already registered",
                )
            )
        else:
            registry[object_id] = ("resolution_spec", resolution)

    _unique_integer(errors, document["narrative_phases"], "phase_order", "/narrative_phases")
    _unique_integer(errors, document["events"], "narrative_order", "/events")

    for index, entity in enumerate(document["entities"]):
        if entity["entity_kind"] == "location":
            for target_index, target_id in enumerate(
                entity["location"]["adjacent_location_object_ids"]
            ):
                _require_reference(
                    errors,
                    registry,
                    target_id,
                    f"/entities/{index}/location/adjacent_location_object_ids/{target_index}",
                    "entity",
                    subtype=("entity_kind", "location"),
                )
                if target_id == entity["object_id"]:
                    errors.append(
                        _error(
                            "self_reference",
                            f"/entities/{index}/location/adjacent_location_object_ids/{target_index}",
                            "a location cannot be adjacent to itself",
                        )
                    )

    for index, event in enumerate(document["events"]):
        _optional_reference(
            errors,
            registry,
            event["narrative_phase_object_id"],
            f"/events/{index}/narrative_phase_object_id",
            "narrative_phase",
        )
        _optional_reference(
            errors,
            registry,
            event["location_object_id"],
            f"/events/{index}/location_object_id",
            "entity",
            subtype=("entity_kind", "location"),
        )
        for actor_index, actor_id in enumerate(event["actor_object_ids"]):
            _require_reference(
                errors,
                registry,
                actor_id,
                f"/events/{index}/actor_object_ids/{actor_index}",
                "entity",
            )

    for index, unit in enumerate(document["information_units"]):
        _optional_reference(
            errors,
            registry,
            unit["visible_from_phase_object_id"],
            f"/information_units/{index}/visible_from_phase_object_id",
            "narrative_phase",
        )
        for field in ("supports_claim_object_ids", "refutes_claim_object_ids"):
            for target_index, target_id in enumerate(unit[field]):
                _require_reference(
                    errors,
                    registry,
                    target_id,
                    f"/information_units/{index}/{field}/{target_index}",
                    "claim",
                )
        if unit["information_kind"] == "evidence":
            _optional_reference(
                errors,
                registry,
                unit["evidence"]["source_event_object_id"],
                f"/information_units/{index}/evidence/source_event_object_id",
                "event",
            )
        if unit["information_kind"] == "testimony":
            _require_reference(
                errors,
                registry,
                unit["testimony"]["speaker_person_object_id"],
                f"/information_units/{index}/testimony/speaker_person_object_id",
                "entity",
                subtype=("entity_kind", "person"),
            )

    for index, hypothesis in enumerate(document["hypotheses"]):
        for field, expected in (
            ("claim_object_ids", "claim"),
            ("required_information_object_ids", "information_unit"),
        ):
            for target_index, target_id in enumerate(hypothesis[field]):
                _require_reference(
                    errors,
                    registry,
                    target_id,
                    f"/hypotheses/{index}/{field}/{target_index}",
                    expected,
                )

    for path_index, path in enumerate(document["reasoning_paths"]):
        _unique_integer(errors, path["nodes"], "ordinal", f"/reasoning_paths/{path_index}/nodes")
        node_keys: set[str] = set()
        for node_index, node in enumerate(path["nodes"]):
            node_key = node["node_key"]
            if node_key in node_keys:
                errors.append(
                    _error(
                        "duplicate_node_key",
                        f"/reasoning_paths/{path_index}/nodes/{node_index}/node_key",
                        f"node_key {node_key!r} is duplicated in the path",
                    )
                )
            node_keys.add(node_key)
            _optional_reference(
                errors,
                registry,
                node["source_object_id"],
                f"/reasoning_paths/{path_index}/nodes/{node_index}/source_object_id",
            )
        for edge_index, edge in enumerate(path["edges"]):
            for field in ("from_node_key", "to_node_key"):
                if edge[field] not in node_keys:
                    errors.append(
                        _error(
                            "missing_node_reference",
                            f"/reasoning_paths/{path_index}/edges/{edge_index}/{field}",
                            f"node_key {edge[field]!r} does not exist in this path",
                        )
                    )
            if edge["from_node_key"] == edge["to_node_key"]:
                errors.append(
                    _error(
                        "self_reference",
                        f"/reasoning_paths/{path_index}/edges/{edge_index}",
                        "a reasoning edge cannot point to itself",
                    )
                )

    if resolution is not None:
        _unique_integer(errors, resolution["slots"], "ordinal", "/resolution_spec/slots")
        slot_keys: set[str] = set()
        for index, slot in enumerate(resolution["slots"]):
            if slot["slot_key"] in slot_keys:
                errors.append(
                    _error(
                        "duplicate_slot_key",
                        f"/resolution_spec/slots/{index}/slot_key",
                        f"slot_key {slot['slot_key']!r} is duplicated",
                    )
                )
            slot_keys.add(slot["slot_key"])

    for index, constraint in enumerate(document["constraints"]):
        _optional_reference(
            errors,
            registry,
            constraint["target_object_id"],
            f"/constraints/{index}/target_object_id",
        )

    for state_index, state in enumerate(document["knowledge_states"]):
        _require_reference(
            errors,
            registry,
            state["entity_object_id"],
            f"/knowledge_states/{state_index}/entity_object_id",
            "entity",
        )
        _require_reference(
            errors,
            registry,
            state["narrative_phase_object_id"],
            f"/knowledge_states/{state_index}/narrative_phase_object_id",
            "narrative_phase",
        )
        _unique_integer(
            errors,
            state["entries"],
            "ordinal",
            f"/knowledge_states/{state_index}/entries",
        )
        for entry_index, entry in enumerate(state["entries"]):
            _require_reference(
                errors,
                registry,
                entry["information_unit_object_id"],
                f"/knowledge_states/{state_index}/entries/{entry_index}/information_unit_object_id",
                "information_unit",
            )
            _optional_reference(
                errors,
                registry,
                entry["acquired_from_object_id"],
                f"/knowledge_states/{state_index}/entries/{entry_index}/acquired_from_object_id",
            )

    return errors


def _require_reference(
    errors: list[dict[str, Any]],
    registry: dict[str, tuple[str, dict[str, Any]]],
    object_id: str,
    path: str,
    expected_type: str | None = None,
    *,
    subtype: tuple[str, str] | None = None,
) -> None:
    registered = registry.get(object_id)
    if registered is None:
        errors.append(_error("missing_reference", path, f"object_id {object_id!r} does not exist"))
        return
    actual_type, target = registered
    if expected_type is not None and actual_type != expected_type:
        errors.append(
            _error(
                "reference_type_mismatch",
                path,
                f"expected {expected_type}, got {actual_type}",
            )
        )
        return
    if subtype is not None and target.get(subtype[0]) != subtype[1]:
        errors.append(
            _error(
                "reference_type_mismatch",
                path,
                f"expected {subtype[0]}={subtype[1]}",
            )
        )


def _optional_reference(
    errors: list[dict[str, Any]],
    registry: dict[str, tuple[str, dict[str, Any]]],
    object_id: str | None,
    path: str,
    expected_type: str | None = None,
    *,
    subtype: tuple[str, str] | None = None,
) -> None:
    if object_id is not None:
        _require_reference(errors, registry, object_id, path, expected_type, subtype=subtype)


def _unique_integer(
    errors: list[dict[str, Any]], items: list[dict[str, Any]], field: str, path: str
) -> None:
    seen: set[int] = set()
    for index, item in enumerate(items):
        value = item[field]
        if value in seen:
            errors.append(
                _error(
                    "duplicate_order",
                    f"{path}/{index}/{field}",
                    f"{field} {value} is duplicated",
                )
            )
        seen.add(value)


def _json_pointer(parts: list[Any]) -> str:
    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _error(code: str, path: str, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message}
