"""Shared accounting for chat execution and prose provider calls."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def merge_usage_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum integer counters, preserving the latest non-counter metadata."""
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
            else:
                merged[key] = value
    return merged


def prose_response_usage(response: Any) -> dict[str, int]:
    """Read the prose providers' completion response without changing token semantics."""
    value = response.usage
    return {
        "requests": 1,
        "input_tokens": int(getattr(value, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(value, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(value, "total_tokens", 0) or 0),
        "cached_tokens": int(getattr(value, "prompt_cache_hit_tokens", 0) or 0),
        "reasoning_tokens": 0,
    }


def fake_prose_usage() -> dict[str, int]:
    """Count one fake request with zero tokens, returning independent mutable data."""
    return {
        "requests": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
