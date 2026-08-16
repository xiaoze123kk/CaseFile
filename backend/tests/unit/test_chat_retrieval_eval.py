"""Retrieval Eval baseline tests: ΔRecall@K and help / neutral / harm."""

from __future__ import annotations

from casefile.agent_runtime.chat_tools import search_casefile_records
from casefile.benchmark.chat_retrieval_eval import (
    build_retrieval_casefile,
    build_retrieval_fixtures,
    evaluate_chat_retrieval,
    validate_retrieval_fixtures,
)

CASE_FILE = build_retrieval_casefile()
FIXTURES = build_retrieval_fixtures()
REPORT = evaluate_chat_retrieval(CASE_FILE, FIXTURES)


def test_fixture_corpus_and_labels_are_valid() -> None:
    validate_retrieval_fixtures(CASE_FILE, FIXTURES)
    assert len(FIXTURES) == 12
    assert search_casefile_records(CASE_FILE, "object:person_1")[0]["id"] == "object:person_1"


def test_retrieval_eval_baseline_improves_recall_without_harm() -> None:
    assert REPORT.delta_recall >= 0.05
    assert REPORT.harm_rate < 0.2
    assert REPORT.help_rate > REPORT.neutral_rate


def test_eval_report_classifies_help_neutral_and_harm() -> None:
    classifications = {entry["classification"] for entry in REPORT.per_fixture}

    assert {"help", "neutral", "harm"} <= classifications
    assert all(entry["recall_rewritten"] <= 1.0 for entry in REPORT.per_fixture)
    assert all(entry["recall_original"] <= 1.0 for entry in REPORT.per_fixture)
