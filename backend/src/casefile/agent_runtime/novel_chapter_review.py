"""Chapter review protocol and provider, without compiler IR or persistence."""

from difflib import SequenceMatcher
from typing import Any

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    ProseRewriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile_contracts import NovelChapterReviewCandidate


def chapter_review_prompt(payload: dict[str, Any] | None = None) -> Any:
    version = (
        "novel-chapter-review-v2"
        if payload and payload.get("editorial_policy") == "chapter-editorial-v2"
        else "novel-chapter-review-v1"
    )
    return load_prompt("novel_chapter_review", version)


def chapter_change_evidence(source: str, candidate: str) -> dict[str, Any]:
    """Measured text differences for the editor; never a literary acceptance gate."""
    matcher = SequenceMatcher(None, source, candidate, autojunk=False)
    changes = [
        {"before": source[a:b], "after": candidate[c:d]}
        for operation, a, b, c, d in matcher.get_opcodes()
        if operation != "equal"
    ]
    return {
        "count_unit": "Unicode code points including whitespace and punctuation",
        "source_chars": len(source),
        "candidate_chars": len(candidate),
        "unchanged_chars": sum(block.size for block in matcher.get_matching_blocks()),
        "change_count": len(changes),
        "changes": [
            {"before": item["before"][:300], "after": item["after"][:300]} for item in changes[:20]
        ],
        "excerpts_may_be_truncated": True,
    }


def review_chapter(payload: dict[str, Any], key: str, model: str) -> Any:
    prompt = chapter_review_prompt(payload)
    data = {
        "response_schema": NovelChapterReviewCandidate.model_json_schema(),
        "untrusted_data": payload,
    }
    digest = canonical_json_sha256(data)
    return DeepSeekProseRewriterProvider().rewrite_scene(
        ProseRewriterRequest(
            model_id=model,
            api_key=key,
            system_prompt=prompt.system_prompt,
            prompt_version=prompt.version,
            prompt_hash=prompt.system_prompt_sha256,
            input_payload=data,
            input_hash=digest,
            component_input_hash=digest,
            request_fingerprint=digest,
            rewrite_round=1,
            remaining_scene_call_budget=2,
            max_output_tokens=8192,
        )
    )


def validate_chapter_review(candidate: Any, payload: dict[str, Any]) -> dict[str, Any]:
    review = NovelChapterReviewCandidate.model_validate(candidate).model_dump(mode="json")
    if not review["summary"].strip():
        raise ValueError("novel_review_summary_missing")
    if review["action"] == "revise" and (
        payload["revision_exhausted"] or not review["revision_plan"].strip()
    ):
        raise ValueError("novel_review_revision_invalid")
    for finding in review["findings"]:
        if not finding["message"].strip():
            raise ValueError("novel_review_finding_empty")
        if finding["source_quote"] and finding["source_quote"] not in payload["target"]:
            raise ValueError("novel_review_source_quote_invalid")
        if (
            finding["candidate_quote"]
            and finding["candidate_quote"] not in payload["candidate"]["text"]
        ):
            raise ValueError("novel_review_candidate_quote_invalid")
        if finding["severity"] == "major" and review["action"] == "accept":
            raise ValueError("novel_review_major_unresolved")
    return review
