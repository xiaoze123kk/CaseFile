"""Accounting boundaries shared by the chat runner, Worker and prose providers."""

from types import SimpleNamespace

from casefile.agent_runtime.usage import (
    fake_prose_usage,
    merge_usage_records,
    prose_response_usage,
)


def test_merge_preserves_metadata_and_does_not_count_booleans() -> None:
    first = {"requests": 1, "input_tokens": 12, "unknown_usage": True, "model": "first"}
    second = {"requests": 2, "input_tokens": 3, "unknown_usage": False, "model": "last"}
    assert merge_usage_records(iter((first, second))) == {
        "requests": 3, "input_tokens": 15, "unknown_usage": False, "model": "last",
    }
    assert first["input_tokens"] == 12
    assert merge_usage_records([]) == {}


def test_prose_response_counts_one_request_even_without_reported_usage() -> None:
    assert prose_response_usage(SimpleNamespace(usage=None)) == fake_prose_usage()
    assert prose_response_usage(SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=12, completion_tokens=3, total_tokens=15, prompt_cache_hit_tokens=4,
    ))) == {
        "requests": 1, "input_tokens": 12, "output_tokens": 3,
        "total_tokens": 15, "cached_tokens": 4, "reasoning_tokens": 0,
    }


def test_fake_usage_is_not_shared_between_calls() -> None:
    first = fake_prose_usage()
    first["input_tokens"] = 10
    assert fake_prose_usage()["input_tokens"] == 0
