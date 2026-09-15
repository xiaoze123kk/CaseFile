"""Shared accounting for chat execution and prose provider calls."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def response_usage_details(response: Any) -> dict[str, Any]:
    """Lossless usage alongside legacy counters; unknown is never a measured zero."""
    value = getattr(response, "usage", None)
    raw = (
        value.model_dump(mode="json")
        if value is not None and hasattr(value, "model_dump")
        else dict(vars(value))
        if value is not None
        else {}
    )
    fields = {
        "input": "prompt_tokens",
        "output": "completion_tokens",
        "cached_input": "prompt_cache_hit_tokens",
        "uncached_input": "prompt_cache_miss_tokens",
    }
    tokens = {key: raw.get(source) for key, source in fields.items()}
    errors = []
    for key, count in tokens.items():
        if count is not None and (type(count) is not int or count < 0):
            errors.append(key)
            tokens[key] = None
    total, cached, missed = tokens["input"], tokens["cached_input"], tokens["uncached_input"]
    if total is not None and cached is not None:
        if cached > total or (missed is not None and cached + missed != total):
            errors.append("cache_consistency")
            tokens["cached_input"] = tokens["uncached_input"] = None
        elif missed is None:
            tokens["uncached_input"] = total - cached
    return {
        "raw": raw,
        "tokens": tokens,
        "errors": errors,
        "usage_available": total is not None and tokens["output"] is not None,
    }


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
