"""Frozen TaskRun input accessors and canonical hash helpers."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


def required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimeError(f"Frozen TaskRun input is missing object field: {key}")
    return result


def required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise RuntimeError(f"Frozen TaskRun input is missing string field: {key}")
    return result


def optional_string(value: dict[str, Any], key: str) -> str | None:
    result = value.get(key)
    if result is not None and (not isinstance(result, str) or not result):
        raise RuntimeError(f"Frozen TaskRun input has an invalid string field: {key}")
    return result


def required_integer(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 1:
        raise RuntimeError(f"Frozen TaskRun input is missing integer field: {key}")
    return result


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


__all__ = [
    "json_hash",
    "optional_string",
    "required_integer",
    "required_object",
    "required_string",
    "text_hash",
]
