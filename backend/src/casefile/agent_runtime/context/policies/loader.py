"""Versioned, immutable context policy resources (Policy-as-data)."""

from __future__ import annotations

import json
import re
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any, Final

import jsonschema

from casefile.agent_runtime.context.models import (
    ContextBudget,
    ContextPolicy,
    ContextPolicyStage,
)

CONTEXT_POLICY_SCHEMA_VERSION: Final = 1
CONTEXT_POLICY_RESOURCE_PACKAGE: Final = "casefile.agent_runtime.context.policies"
CHAT_CONTEXT_POLICY_VERSION: Final = "casefile-chat-context-v1"
#: Phase 3 rolling/layered compaction policy, gated behind the rollout flag.
CHAT_CONTEXT_POLICY_V2_VERSION: Final = "casefile-chat-context-v2"
_POLICY_SCHEMA_FILE: Final = "schema.json"
_POLICY_VERSION = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class ContextPolicyError(RuntimeError):
    """The packaged context policy violates its immutable resource contract."""


def _schema_resource() -> Traversable:
    resource = files(CONTEXT_POLICY_RESOURCE_PACKAGE).joinpath(_POLICY_SCHEMA_FILE)
    if not resource.is_file():
        raise ContextPolicyError("Context policy schema resource is missing")
    return resource


@cache
def _policy_schema() -> dict[str, Any]:
    try:
        raw = json.loads(_schema_resource().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContextPolicyError("Context policy schema resource is unreadable") from error
    if not isinstance(raw, dict):
        raise ContextPolicyError("Context policy schema must be a JSON object")
    return raw


@cache
def _policy_resources() -> dict[str, Traversable]:
    root = files(CONTEXT_POLICY_RESOURCE_PACKAGE)
    result: dict[str, Traversable] = {}
    for item in root.iterdir():
        if item.name == _POLICY_SCHEMA_FILE or not item.name.endswith(".json"):
            continue
        version = item.name.removesuffix(".json")
        if item.is_file() and _POLICY_VERSION.fullmatch(version):
            result[version] = item
    if not result:
        raise ContextPolicyError("No context policy resources are packaged")
    return result


def load_context_policy(version: str) -> ContextPolicy:
    """Load and validate one immutable policy version from packaged resources."""

    if _POLICY_VERSION.fullmatch(version) is None:
        raise ContextPolicyError(f"Invalid context policy version: {version!r}")
    resource = _policy_resources().get(version)
    if resource is None:
        raise ContextPolicyError(f"Unknown context policy version: {version!r}")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContextPolicyError(f"Context policy {version!r} is unreadable") from error
    if not isinstance(raw, dict):
        raise ContextPolicyError(f"Context policy {version!r} must be a JSON object")
    try:
        jsonschema.validate(raw, _policy_schema())
    except (jsonschema.ValidationError, jsonschema.SchemaError) as error:
        raise ContextPolicyError(
            f"Context policy {version!r} failed schema validation: {error.message}"
        ) from error
    return _policy_from_document(raw)


def known_context_policy_versions() -> tuple[str, ...]:
    """Return packaged policy versions in stable order."""

    return tuple(sorted(_policy_resources()))


def _policy_from_document(raw: dict[str, Any]) -> ContextPolicy:
    stages = tuple(
        ContextPolicyStage(
            id=str(stage["id"]),
            strategy=str(stage["strategy"]),
            config=dict(stage.get("config") or {}),
        )
        for stage in raw["stages"]
    )
    budget_raw = raw.get("budget") or {}
    block_limits_raw = budget_raw.get("block_limits") or {}
    trim_order_raw = budget_raw.get("trim_order") or []
    return ContextPolicy(
        schema_version=int(raw["schema_version"]),
        version=str(raw["version"]),
        task_type=str(raw["task_type"]),
        stages=stages,
        budget=ContextBudget(
            total_input_tokens=(
                None
                if budget_raw.get("total_input_tokens") is None
                else int(budget_raw["total_input_tokens"])
            ),
            enforce_budget=bool(budget_raw.get("enforce_budget", False)),
            block_limits={
                str(key): int(value)
                for key, value in block_limits_raw.items()
                if int(value) >= 1
            },
            trim_order=tuple(str(value) for value in trim_order_raw),
        ),
        guardrails={
            str(key): bool(value) for key, value in (raw.get("guardrails") or {}).items()
        },
    )


__all__ = [
    "CHAT_CONTEXT_POLICY_VERSION",
    "CHAT_CONTEXT_POLICY_V2_VERSION",
    "CONTEXT_POLICY_RESOURCE_PACKAGE",
    "CONTEXT_POLICY_SCHEMA_VERSION",
    "ContextPolicyError",
    "known_context_policy_versions",
    "load_context_policy",
]
