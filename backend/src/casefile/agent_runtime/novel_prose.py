"""Chapter adapters for the existing prose Judge, editor, Rewriter and quality roles."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    DeepSeekProseJudgeProvider,
    ProseCouncilProtocolError,
    ProseJudgeRequest,
    build_server_evidence_catalog,
    validate_judge_assessments,
)
from casefile.agent_runtime.prose_polisher import (
    DeepSeekProsePolisherProvider,
    ProsePolisherRequest,
)
from casefile.agent_runtime.prose_quality_critic import (
    DeepSeekProseQualityCriticProvider,
    ProseQualityRequest,
    parse_quality_findings,
    parse_quality_pairwise,
)
from casefile.agent_runtime.prose_revision import validate_revision_decision
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    ProseRewriterRequest,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.domain.narrative_compiler.prose_quality import select_mirrored_preferences
from casefile_contracts import ProseRevisionDecisionCandidate, SceneRenderCandidate

POLICY = "chapter-prose-v1"
PHASES = ("checklist", "judge", "revision", "rewriter", "quality_critic", "polisher", "pairwise")


class ChapterCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str = Field(min_length=1, max_length=120)
    category: Literal["intent", "preservation", "meaning", "continuity"]
    polarity: Literal["required", "forbidden"]
    requirement: str = Field(min_length=1, max_length=1500)
    source_field: Literal["instruction", "preserve", "allow_changes", "target"]
    source_quote: str = Field(min_length=1, max_length=2000)


class ChapterChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[ChapterCheck] = Field(min_length=1, max_length=40)


def current_prompt_versions() -> dict[str, str]:
    return {
        phase: "novel-" + phase.replace("_", "-") + ("-v2" if phase == "revision" else "-v1")
        for phase in PHASES
    }


def phase_prompt(phase: str, context: dict[str, Any] | None = None) -> Any:
    if phase not in PHASES:
        raise ValueError("novel_prose_phase_invalid")
    version = (
        (context or {})
        .get("prose_prompt_versions", {})
        .get(phase, "novel-" + phase.replace("_", "-") + "-v1")
    )
    return load_prompt("novel_" + phase, version)


def prompt_hashes(context: dict[str, Any] | None = None) -> dict[str, str]:
    return {phase: phase_prompt(phase, context).system_prompt_sha256 for phase in PHASES}


def evidence(text: str) -> list[dict[str, Any]]:
    return build_server_evidence_catalog({"blocks": [{"block_id": "chapter", "text": text}]})


def validate_checklist(candidate: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = ChapterChecklist.model_validate(candidate).model_dump(mode="json")
    checks = result["checks"]
    if len({item["check_id"] for item in checks}) != len(checks):
        raise ValueError("novel_checklist_duplicate")
    fields = {
        "instruction": context["instruction"],
        "target": context["target"],
        **context["requirements"],
    }
    for item in checks:
        if item["source_quote"] not in fields[item["source_field"]]:
            raise ValueError(
                "novel_checklist_quote_invalid:"
                + item["check_id"]
                + ":quote_must_be_exact_substring_of:"
                + item["source_field"]
            )
    for field in ("instruction", "preserve", "allow_changes"):
        if fields[field].strip() and not any(item["source_field"] == field for item in checks):
            raise ValueError("novel_checklist_source_uncovered:" + field)
    return result


def validate_judge(candidate: Any, checks: list[dict[str, Any]], text: str) -> dict[str, Any]:
    try:
        assessments = validate_judge_assessments(candidate, checks, evidence(text))
    except ProseCouncilProtocolError as error:
        raise ValueError(str(error)) from error
    return {"assessments": assessments, "candidate_hash": canonical_json_sha256(text)}


def validate_decision(
    candidate: Any, checks: list[dict[str, Any]], exhausted: bool
) -> dict[str, Any]:
    return validate_revision_decision(
        candidate, [item["check_id"] for item in checks], exhausted=exhausted
    )


def validate_text(candidate: Any) -> str:
    parsed = SceneRenderCandidate.model_validate(candidate)
    text = "\n\n".join(block.text for block in parsed.blocks)
    if not text.strip() or len(text) > 16000:
        raise ValueError("novel_prose_text_length_invalid")
    return text


def validate_critic(candidate: Any, text: str) -> dict[str, Any]:
    parsed = parse_quality_findings(candidate)
    catalog = {item["evidence_id"]: item for item in evidence(text)}
    for finding in parsed["findings"]:
        ids = finding["evidence_ids"]
        if len(ids) != len(set(ids)) or any(key not in catalog for key in ids):
            raise ValueError("novel_quality_evidence_invalid")
        finding["evidence"] = [catalog[key] for key in ids]
    return parsed


def select_pair(first: Any, second: Any) -> tuple[bool, str]:
    reports = [parse_quality_pairwise(first), parse_quality_pairwise(second)]
    mappings = [
        {"a": "original", "b": "polished", "tie": "tie"},
        {"a": "polished", "b": "original", "tie": "tie"},
    ]
    overall = tuple(
        mapping[report["overall_preference"]]
        for report, mapping in zip(reports, mappings, strict=True)
    )
    dimensions = [
        mapping[item["preference"]]
        for report, mapping in zip(reports, mappings, strict=True)
        for item in report["dimension_preferences"]
    ]
    return select_mirrored_preferences((overall[0], overall[1]), dimensions)


class ChapterProseProvider:
    """Use the real heavy-chain role adapters, with chapter inputs and frozen prompts."""

    def invoke(self, phase: str, payload: dict[str, Any], key: str, model: str) -> Any:
        prompt = phase_prompt(phase, payload)
        schemas: dict[str, Any] = {
            "checklist": ChapterChecklist,
            "revision": ProseRevisionDecisionCandidate,
            "rewriter": SceneRenderCandidate,
        }
        data = {"untrusted_data": payload}
        if phase in schemas:
            data["response_schema"] = schemas[phase].model_json_schema()
        digest = canonical_json_sha256(data)
        common = dict(
            model_id=model,
            api_key=key,
            system_prompt=prompt.system_prompt,
            prompt_version=prompt.version,
            prompt_hash=prompt.system_prompt_sha256,
            input_payload=data,
            input_hash=digest,
            request_fingerprint=digest,
            network_retries=0,
        )
        if phase == "judge":
            return DeepSeekProseJudgeProvider().judge_scene(
                ProseJudgeRequest(role="fidelity", **common)
            )
        if phase in {"quality_critic", "pairwise"}:
            return DeepSeekProseQualityCriticProvider().assess_quality(
                ProseQualityRequest(
                    request_kind="findings" if phase == "quality_critic" else "pairwise",
                    component_input_hash=digest,
                    position_mapping=None,
                    **common,
                )
            )
        if phase == "polisher":
            return DeepSeekProsePolisherProvider().polish_scene(
                ProsePolisherRequest(
                    component_input_hash=digest,
                    max_output_tokens=32768,
                    generation_instruction="请依据质量意见和作者要求润色当前完整章节，保留事实与修改边界。",
                    **common,
                )
            )
        return DeepSeekProseRewriterProvider().rewrite_scene(
            ProseRewriterRequest(
                component_input_hash=digest,
                rewrite_round=1,
                remaining_scene_call_budget=1,
                max_output_tokens=32768 if phase == "rewriter" else 8192,
                **common,
            )
        )
