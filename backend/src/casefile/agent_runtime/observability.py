"""Deterministic, observational metrics for Brief-to-Draft runs."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

SEMANTIC_COVERAGE_VERSION = "brief-semantic-coverage-v1"
COST_USAGE_VERSION = "generation-cost-usage-v1"
_COVERAGE_THRESHOLD = 0.6
_USAGE_KEYS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)
_NON_SEMANTIC_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "object_type",
        "conclusion_mode",
        "strength",
        "revision",
        "created_at",
        "updated_at",
        "deleted_at",
        "tags",
        "x",
        "y",
        "latitude",
        "longitude",
    }
)


def brief_semantic_coverage(
    brief: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Measure lexical evidence for frozen Brief concepts in one valid candidate.

    This is deliberately an observational proxy, not a semantic validator. It never
    raises for low coverage and must not be used to accept or reject a candidate.
    """

    target_grams = _candidate_grams(candidate)
    field_sources = {
        "author_answer": _strings(brief.get("author_answer")),
        "author_anchors": _statements(brief.get("author_anchors")),
        "hard_constraints": _constraint_statements(brief, strength="hard"),
        "soft_constraints": _constraint_statements(brief, strength="soft"),
        "creative_intent": _strings(brief.get("creative_intent")),
        "reasoning_proposition": _strings(brief.get("reasoning_proposition")),
        "core_selling_points": _strings(brief.get("core_selling_points")),
        "content_outline": _strings(brief.get("content_outline")),
    }

    fields: dict[str, dict[str, Any]] = {}
    source_items = 0
    covered_items = 0
    source_gram_count = 0
    covered_gram_count = 0
    covered_fields = 0
    applicable_fields = 0
    for field_name, values in field_sources.items():
        scores: list[float] = []
        field_source_grams = 0
        field_covered_grams = 0
        for value in values:
            grams = _character_bigrams(value)
            if not grams:
                continue
            matched = len(grams & target_grams)
            scores.append(matched / len(grams))
            field_source_grams += len(grams)
            field_covered_grams += matched

        applicable = bool(scores)
        matched_items = sum(score >= _COVERAGE_THRESHOLD for score in scores)
        coverage_rate = (
            round(field_covered_grams / field_source_grams, 4) if field_source_grams else None
        )
        item_match_rate = round(matched_items / len(scores), 4) if scores else None
        fields[field_name] = {
            "applicable": applicable,
            "source_items": len(scores),
            "covered_items": matched_items,
            "coverage_rate": coverage_rate,
            "item_match_rate": item_match_rate,
        }
        if applicable:
            applicable_fields += 1
            covered_fields += int(
                bool(coverage_rate is not None and coverage_rate >= _COVERAGE_THRESHOLD)
            )
        source_items += len(scores)
        covered_items += matched_items
        source_gram_count += field_source_grams
        covered_gram_count += field_covered_grams

    return {
        "version": SEMANTIC_COVERAGE_VERSION,
        "method": "normalized_character_bigram_recall",
        "threshold": _COVERAGE_THRESHOLD,
        "observational_only": True,
        "overall": {
            "applicable_fields": applicable_fields,
            "covered_fields": covered_fields,
            "source_items": source_items,
            "covered_items": covered_items,
            "coverage_rate": (
                round(covered_gram_count / source_gram_count, 4) if source_gram_count else None
            ),
            "item_match_rate": (round(covered_items / source_items, 4) if source_items else None),
        },
        "fields": fields,
    }


def standardize_generation_cost_usage(
    usage: dict[str, Any],
    *,
    provider: str,
    model_id: str,
) -> dict[str, Any]:
    """Normalize usage and expose cost inputs without inventing model pricing."""

    normalized = {key: _non_negative_int(usage.get(key)) for key in _USAGE_KEYS}
    computed_total = normalized["input_tokens"] + normalized["output_tokens"]
    reported_total = normalized["total_tokens"]
    total_source = "reported" if reported_total > 0 or computed_total == 0 else "derived"
    if total_source == "derived":
        normalized["total_tokens"] = computed_total

    cached_tokens = normalized["cached_tokens"]
    reasoning_tokens = normalized["reasoning_tokens"]
    return {
        "version": COST_USAGE_VERSION,
        "provider": provider,
        "model_id": model_id,
        "requests": normalized["requests"],
        "tokens": {
            "input": normalized["input_tokens"],
            "cached_input": cached_tokens,
            "uncached_input": max(normalized["input_tokens"] - cached_tokens, 0),
            "output": normalized["output_tokens"],
            "reasoning_output": reasoning_tokens,
            "total": normalized["total_tokens"],
            "total_source": total_source,
        },
        "consistency": {
            "reported_total_matches_input_plus_output": (
                reported_total == 0 or reported_total == computed_total
            ),
            "cached_input_within_input": cached_tokens <= normalized["input_tokens"],
            "reasoning_output_within_output": reasoning_tokens <= normalized["output_tokens"],
        },
        "monetary_cost": {
            "available": False,
            "amount": None,
            "currency": None,
            "reason": "provider_pricing_not_frozen_with_task_run",
        },
    }


def _constraint_statements(brief: dict[str, Any], *, strength: str) -> list[str]:
    constraints = brief.get("creative_constraints")
    if not isinstance(constraints, list):
        return []
    return [
        statement.strip()
        for item in constraints
        if isinstance(item, dict)
        and item.get("strength") == strength
        and isinstance((statement := item.get("statement")), str)
        and statement.strip()
    ]


def _statements(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        statement.strip()
        for item in value
        if isinstance(item, dict)
        and isinstance((statement := item.get("statement")), str)
        and statement.strip()
    ]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _candidate_grams(candidate: dict[str, Any]) -> set[str]:
    grams: set[str] = set()
    for value in _text_leaves(candidate):
        grams.update(_character_bigrams(value))
    return grams


def _text_leaves(value: Any, *, field_name: str | None = None) -> Iterable[str]:
    if isinstance(value, str):
        if field_name is not None and (
            field_name in _NON_SEMANTIC_KEYS
            or field_name == "id"
            or field_name.endswith("_id")
            or field_name.endswith("_ref")
            or field_name.endswith("_refs")
        ):
            return
        if value.strip():
            yield value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _text_leaves(item, field_name=str(key))
        return
    if isinstance(value, list):
        for item in value:
            yield from _text_leaves(item, field_name=field_name)


def _character_bigrams(value: str) -> set[str]:
    normalized = "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", value)
        if character.isalnum()
    )
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


__all__ = [
    "COST_USAGE_VERSION",
    "SEMANTIC_COVERAGE_VERSION",
    "brief_semantic_coverage",
    "standardize_generation_cost_usage",
]
