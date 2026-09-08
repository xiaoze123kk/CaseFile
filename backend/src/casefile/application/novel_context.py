"""Load novel-only history and its last successful rolling-memory checkpoint."""

from typing import Any

from sqlalchemy import select

from casefile.agent_runtime.novel_context import POLICY, plan_history
from casefile.data_postgres.models import TaskRun
from casefile.data_postgres.models.novel_editor import NovelEdit, NovelEditDecision, NovelExchange


def load_history(
    session: Any, manuscript: int, chapter: str, original: bool, history_after: int = 0
) -> dict[str, Any]:
    memory: dict[str, Any] = {}
    turns = []
    rows = session.execute(
        select(NovelExchange, TaskRun)
        .join(TaskRun, TaskRun.id == NovelExchange.task_id)
        .where(
            NovelExchange.manuscript_id == manuscript,
            NovelExchange.chapter_key == chapter,
            TaskRun.status == "succeeded",
        )
        .order_by(NovelExchange.id.desc())
    )
    for exchange, task in rows:
        if exchange.id <= history_after:
            break
        request = task.input_jsonb["request"]
        if request.get("history_after_exchange_id", 0) != history_after:
            continue
        if bool((request.get("anchor") or {}).get("original")) != original:
            continue
        if exchange.id <= memory.get("through_id", 0):
            break
        decisions = list(
            session.execute(
                select(NovelEdit.id, NovelEditDecision.action)
                .outerjoin(NovelEditDecision, NovelEditDecision.edit_id == NovelEdit.id)
                .where(NovelEdit.exchange_id == exchange.id)
            )
        )
        result = task.result_jsonb or {}
        turns.append(
            {
                "id": exchange.id,
                "revision": exchange.revision,
                "scope": request["scope"],
                "instruction": exchange.instruction,
                "answer": result.get("message", ""),
                "edit_status": {
                    "proposed": len(decisions),
                    "accepted": sum(action == "accept" for _, action in decisions),
                    "rejected": sum(action == "reject" for _, action in decisions),
                },
            }
        )
        checkpoint = result.get("context_memory", {})
        if not memory and checkpoint.get("policy") == POLICY:
            memory = checkpoint
    return plan_history(list(reversed(turns)), memory)
