"""Retrieval Eval for the R3 bounded tool loop.

Purely deterministic: the same ``search_casefile_records`` backend used by the
tools and by FakeProvider measures whether a rewritten
``retrieval_queries`` list improves recall over the original query, without
hitting forbidden evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from casefile.agent_runtime.chat_tools import find_casefile_object, search_casefile_records


@dataclass(frozen=True, slots=True)
class ChatRetrievalFixture:
    fixture_id: str
    original_query: str
    retrieval_queries: tuple[str, ...]
    expected_object_ids: tuple[str, ...]
    forbidden_object_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatRetrievalEvalReport:
    delta_recall: float
    help_rate: float
    neutral_rate: float
    harm_rate: float
    per_fixture: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "delta_recall": self.delta_recall,
            "help_rate": self.help_rate,
            "neutral_rate": self.neutral_rate,
            "harm_rate": self.harm_rate,
            "per_fixture": list(self.per_fixture),
        }


def build_retrieval_casefile() -> dict[str, Any]:
    return {
        "resolution_specs": [],
        "entities": [
            {
                "id": "object:person_1",
                "name": "张三",
                "description": "仓储管理员，负责三号库区。",
            },
            {"id": "object:person_2", "name": "李四", "description": "装卸组长。"},
            {"id": "object:person_3", "name": "王五", "description": "货车司机。"},
            {"id": "object:company_1", "name": "远洋物流", "description": "承运方。"},
        ],
        "relationships": [
            {
                "id": "rel_1",
                "from_ref": "object:person_1",
                "to_ref": "object:company_1",
                "description": "张三受雇于远洋物流",
            },
            {
                "id": "rel_2",
                "from_ref": "object:person_2",
                "to_ref": "object:person_3",
                "description": "李四与王五共同押车",
            },
        ],
        "locations": [
            {"id": "loc_1", "name": "三号库区", "description": "火灾现场"}
        ],
        "events": [
            {
                "id": "event:fire",
                "title": "三号库区失火",
                "description": "2026-07-02 夜间发生。",
            },
            {
                "id": "event:entry",
                "title": "入库记录",
                "description": "值班登记显示当天有一批货物入库。",
            },
            {
                "id": "event:shipment",
                "title": "提货记录",
                "description": "货车于次日出库。",
            },
        ],
        "information_units": [
            {
                "id": "info_1",
                "description": "目击者看到仓库管理员在库区门口。",
            }
        ],
        "claims": [
            {"id": "claim_1", "statement": "张三主张当天不在库区"},
            {"id": "claim_2", "statement": "李四说自己只负责装车"},
        ],
        "hypotheses": [
            {"id": "hyp_1", "statement": "外来火源引燃包装材料"}
        ],
        "reasoning_paths": [
            {"id": "path_1", "description": "由入库登记与班次表推知管理员在岗"}
        ],
        "constraints": [],
        "structure_locks": [],
    }


def build_retrieval_fixtures() -> tuple[ChatRetrievalFixture, ...]:
    return (
        ChatRetrievalFixture(
            "r01_exact_entity_is_neutral",
            "object:person_1",
            ("object:person_1",),
            ("object:person_1",),
        ),
        ChatRetrievalFixture(
            "r02_ambiguous_prose_to_multiquery",
            "当时谁在库区",
            ("张三 三号库区", "目击者 库区门口"),
            ("object:person_1", "loc_1", "info_1"),
        ),
        ChatRetrievalFixture(
            "r03_event_plus_actor",
            "那次火灾是谁负责的库区",
            ("三号库区失火", "张三 仓储管理员 三号库区"),
            ("event:fire", "loc_1", "object:person_1"),
        ),
        ChatRetrievalFixture(
            "r04_entity_plus_event",
            "李四",
            ("李四", "提货记录 李四"),
            ("object:person_2", "event:shipment"),
        ),
        ChatRetrievalFixture(
            "r05_claim_attribution",
            "他说了什么",
            ("张三 主张 不在库区",),
            ("claim_1", "object:person_1"),
        ),
        ChatRetrievalFixture(
            "r06_entry_timing",
            "张三什么时候进的库",
            ("入库记录 张三", "张三 三号库区 值班"),
            ("event:entry", "object:person_1", "loc_1"),
        ),
        ChatRetrievalFixture(
            "r07_carrier_question",
            "承运方是谁",
            ("远洋物流 承运方",),
            ("object:company_1",),
        ),
        ChatRetrievalFixture(
            "r08_responsibility",
            "三号库区谁负责",
            ("三号库区", "张三 仓储管理员"),
            ("loc_1", "object:person_1"),
        ),
        ChatRetrievalFixture(
            "r09_fire_cause",
            "火灾原因",
            ("外来火源 包装材料", "三号库区失火"),
            ("hyp_1", "event:fire", "loc_1"),
        ),
        ChatRetrievalFixture(
            "r10_relation_probe",
            "李四和王五的关系",
            ("李四", "王五", "李四 王五 押车"),
            ("object:person_2", "object:person_3", "rel_2"),
        ),
        ChatRetrievalFixture(
            "r11_forbidden_evidence_detected",
            "远洋物流",
            ("远洋物流", "三号库区"),
            ("object:company_1",),
            ("loc_1", "event:fire"),
        ),
        ChatRetrievalFixture(
            "r12_time_of_fire",
            "那天晚上的事情",
            ("2026-07-02 三号库区失火", "张三 不在库区"),
            ("event:fire", "loc_1", "claim_1"),
        ),
    )


def _recall(found_ids: set[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 1.0
    return len(found_ids & expected_ids) / len(expected_ids)


def validate_retrieval_fixtures(
    casefile: dict[str, Any],
    fixtures: tuple[ChatRetrievalFixture, ...],
) -> None:
    fixture_ids = [fixture.fixture_id for fixture in fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("retrieval fixture ids must be unique")
    for fixture in fixtures:
        if not fixture.original_query.strip():
            raise ValueError(f"{fixture.fixture_id}: original_query must not be empty")
        if not fixture.retrieval_queries or any(
            not query.strip() for query in fixture.retrieval_queries
        ):
            raise ValueError(f"{fixture.fixture_id}: retrieval_queries must be non-empty")
        if not fixture.expected_object_ids:
            raise ValueError(f"{fixture.fixture_id}: expected_object_ids must not be empty")
        overlap = set(fixture.expected_object_ids) & set(fixture.forbidden_object_ids)
        if overlap:
            raise ValueError(f"{fixture.fixture_id}: expected/forbidden overlap: {sorted(overlap)}")
        for object_id in (*fixture.expected_object_ids, *fixture.forbidden_object_ids):
            if find_casefile_object(casefile, object_id) is None:
                raise ValueError(f"{fixture.fixture_id}: object_id not found: {object_id}")


def evaluate_chat_retrieval(
    casefile: dict[str, Any],
    fixtures: tuple[ChatRetrievalFixture, ...],
    *,
    top_k: int = 8,
) -> ChatRetrievalEvalReport:
    """Measure ΔRecall@K and classify every fixture as help / neutral / harm."""

    validate_retrieval_fixtures(casefile, fixtures)
    per_fixture: list[dict[str, Any]] = []
    for fixture in fixtures:
        expected = set(fixture.expected_object_ids)
        forbidden = set(fixture.forbidden_object_ids)
        original_found = {
            str(record["id"])
            for record in search_casefile_records(
                casefile,
                fixture.original_query,
                limit=top_k,
            )
        }
        rewritten_found: set[str] = set()
        for query in fixture.retrieval_queries:
            rewritten_found.update(
                str(record["id"])
                for record in search_casefile_records(casefile, query, limit=top_k)
            )
        recall_original = _recall(original_found, expected)
        recall_rewritten = _recall(rewritten_found, expected)
        delta = round(recall_rewritten - recall_original, 4)
        if rewritten_found & forbidden:
            classification = "harm"
            reason_code = "forbidden_evidence_hit"
        elif recall_rewritten > recall_original:
            classification = "help"
            reason_code = "expected_recall_gained"
        elif recall_rewritten == recall_original:
            classification = "neutral"
            reason_code = "recall_unchanged"
        else:
            classification = "harm"
            reason_code = "expected_recall_lost"
        per_fixture.append(
            {
                "fixture_id": fixture.fixture_id,
                "classification": classification,
                "reason_code": reason_code,
                "recall_original": recall_original,
                "recall_rewritten": recall_rewritten,
                "delta_recall": delta,
                "retrieved_object_ids": sorted(rewritten_found),
                "forbidden_hits": sorted(rewritten_found & forbidden),
            }
        )
    total = max(1, len(per_fixture))
    classifications = [entry["classification"] for entry in per_fixture]
    return ChatRetrievalEvalReport(
        delta_recall=round(
            sum(entry["delta_recall"] for entry in per_fixture) / total,
            4,
        ),
        help_rate=classifications.count("help") / total,
        neutral_rate=classifications.count("neutral") / total,
        harm_rate=classifications.count("harm") / total,
        per_fixture=tuple(per_fixture),
    )


__all__ = [
    "ChatRetrievalEvalReport",
    "ChatRetrievalFixture",
    "build_retrieval_casefile",
    "build_retrieval_fixtures",
    "evaluate_chat_retrieval",
    "validate_retrieval_fixtures",
]
