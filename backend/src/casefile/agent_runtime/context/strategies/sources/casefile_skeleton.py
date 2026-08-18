"""Deterministic CaseFile skeleton source: full objects stay tool-retrievable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.chat_tools import CASEFILE_COLLECTIONS
from casefile.agent_runtime.context.estimators import estimate_jsonable_tokens
from casefile.agent_runtime.context.models import ContextBlock, StageResult
from casefile.agent_runtime.context.protocols import ContextRun

_LABEL_FIELDS = ("name", "title", "statement", "proposition", "description")


def _record_label(item: dict[str, Any]) -> str:
    for key in _LABEL_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_casefile_skeleton(casefile: dict[str, Any]) -> dict[str, Any]:
    """Project every record to id/collection/label/type plus collection counts.

    The executor only receives this skeleton in the v2 prompt contract; full
    record content stays in the frozen CaseFile and is fetched through the
    read-only tools.
    """

    collections: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for collection in CASEFILE_COLLECTIONS:
        count = 0
        for item in casefile.get(collection) or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            count += 1
            record: dict[str, Any] = {
                "id": str(item["id"]),
                "collection": collection,
                "label": _record_label(item) or str(item["id"]),
            }
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type:
                record["type"] = item_type
            records.append(record)
        collections[collection] = count
    return {"collection_counts": collections, "records": records}


@dataclass(slots=True)
class CaseFileSkeletonStage:
    """Policy stage producing the ``casefile_skeleton`` block."""

    name: str = "casefile_skeleton_v1"
    version: str = "casefile-skeleton-v1"
    capabilities: frozenset[str] = frozenset({"source", "chat", "deterministic"})

    def can_run(self, run: ContextRun) -> bool:
        return isinstance(run.frozen_input.get("casefile"), dict)

    def run(self, run: ContextRun) -> StageResult:
        casefile = run.frozen_input.get("casefile")
        if not isinstance(casefile, dict):
            return StageResult()
        skeleton = build_casefile_skeleton(casefile)
        return StageResult(
            added=(
                ContextBlock(
                    id="casefile_skeleton",
                    kind="casefile_skeleton",
                    payload=skeleton,
                    tokens=estimate_jsonable_tokens(skeleton, run.estimator),
                    metadata={"record_count": len(skeleton["records"])},
                ),
            ),
            metrics={"record_count": len(skeleton["records"])},
        )


__all__ = [
    "CaseFileSkeletonStage",
    "build_casefile_skeleton",
]
