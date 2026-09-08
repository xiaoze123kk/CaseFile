"""Bounded, journaled chapter review with at most one content revision."""

from functools import partial
from typing import Any

from casefile.agent_runtime.novel_chapter_review import (
    chapter_change_evidence,
    chapter_review_prompt,
    validate_chapter_review,
)
from casefile.agent_runtime.novel_collaboration import collaboration_prompt, validate_edits
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.handlers.novel_model_calls import (
    NovelCallError,
    NovelModelJournal,
    NovelProtocolError,
)


def review_candidate(
    journal: NovelModelJournal,
    provider: Any,
    payload: dict[str, Any],
    candidate: dict[str, Any],
    key: str,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    revision_count = 0

    def finish(status: str, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return candidate, {
            "status": status,
            "message": message,
            "revision_count": revision_count,
            "candidate_hash": canonical_json_sha256(candidate["text"]),
            "reports": reports,
        }

    for round_no in range(2):
        review_payload = {**payload, "candidate": candidate, "revision_exhausted": round_no == 1}
        if payload.get("editorial_policy") == "chapter-editorial-v2":
            review_payload["change_evidence"] = chapter_change_evidence(
                payload["target"], candidate["text"]
            )
        journal.ctx.emit(
            journal.ctx.task,
            "novel.activity",
            "reviewing",
            {
                "text": "正在复核修订后的章节…"
                if round_no
                else "正在核对改写要求、保留项与前后衔接…",
            },
        )
        repair = None
        try:
            for attempt in range(2):
                data = {**review_payload, "protocol_repair": repair}
                try:
                    review = journal.call(
                        "chapter_review",
                        chapter_review_prompt(payload),
                        data,
                        partial(provider.review, data, key, model),
                        partial(_validate_review, payload=review_payload),
                    )
                    break
                except NovelProtocolError as error:
                    if attempt:
                        raise
                    repair = str(error)
        except NovelCallError:
            return finish("incomplete", "编辑审阅未完成，候选已保留，请自行核对后决定是否采纳。")
        reports.append(
            {
                "candidate_hash": canonical_json_sha256(candidate["text"]),
                "round": round_no,
                "review": review,
            }
        )
        if review["action"] != "revise":
            return finish("completed", "编辑审阅完成，仍由你决定是否采纳。")
        journal.ctx.emit(
            journal.ctx.task,
            "novel.activity",
            "revising",
            {
                "text": "正在根据编辑意见进行一次修订…",
            },
        )
        revision_payload = {
            **payload,
            "revision_context": {"candidate": candidate, "review": review},
        }
        repair = None
        try:
            for attempt in range(2):
                try:
                    revised = journal.call(
                        "chapter_revision",
                        collaboration_prompt(payload),
                        {"context": revision_payload, "repair": repair},
                        partial(provider.edit, revision_payload, key, model, repair),
                        lambda result: _validate_revision(result, payload),
                    )
                    break
                except NovelProtocolError as error:
                    if attempt:
                        raise
                    repair = str(error)
        except NovelCallError:
            return finish("revision_failed", "自动修订未完成，保留修订前候选及其编辑意见。")
        if revised["text"] == candidate["text"]:
            return finish("revision_failed", "自动修订未产生正文变化，保留候选及待处理意见。")
        candidate = revised
        revision_count = 1
    raise RuntimeError("novel_editorial_unreachable")


def _validate_revision(result: Any, payload: dict[str, Any]) -> dict[str, Any]:
    validate_edits(result.candidate, payload)
    return dict(result.candidate)


def _validate_review(result: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return validate_chapter_review(result.candidate, payload)
