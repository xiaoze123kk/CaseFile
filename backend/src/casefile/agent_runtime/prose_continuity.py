"""Pre-generation continuity review; immutable plans are never silently rewritten."""

from __future__ import annotations

import json
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_writer import DeepSeekProseWriterProvider, ProseWriterRequest
from casefile.domain.narrative_compiler import canonical_json_sha256


class ContinuityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_ids: list[str] = Field(min_length=1, max_length=3)
    reason: str = Field(min_length=1, max_length=2000)
    required_plan_change: str = Field(min_length=1, max_length=2000)


class ContinuityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "blocked"]
    issues: list[ContinuityIssue] = Field(max_length=16)

    @model_validator(mode="after")
    def consistent(self) -> ContinuityReview:
        if (self.verdict == "pass") != (not self.issues):
            raise ValueError("continuity_verdict_issue_mismatch")
        return self


class DeepSeekContinuityProvider(DeepSeekProseWriterProvider):
    """Reuse only the journal-compatible transport envelope, not Writer semantics."""

    def _create_completion(self, request: ProseWriterRequest) -> Any:
        with OpenAI(api_key=request.api_key, base_url=self.base_url, max_retries=0) as client:
            return client.chat.completions.create(
                model=request.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": request.system_prompt
                        + "\n"
                        + json.dumps(ContinuityReview.model_json_schema(), ensure_ascii=False),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(request.input_payload, ensure_ascii=False),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=request.max_output_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )


def continuity_request(
    contexts: list[dict[str, Any]], previous: dict[str, Any] | None, *, api_key: str
) -> ProseWriterRequest:
    prompt = load_prompt("prose_continuity")
    payload = {
        "output_schema_id": "compiler.prose-continuity-review.v1",
        "untrusted_data": {"scenes": contexts, "previous_accepted": previous},
    }
    digest = canonical_json_sha256(payload)
    return ProseWriterRequest(
        model_id="deepseek-v4-pro",
        api_key=api_key,
        system_prompt=prompt.system_prompt,
        prompt_version=prompt.version,
        prompt_hash=prompt.system_prompt_sha256,
        input_payload=payload,
        input_hash=digest,
        component_input_hash=digest,
        request_fingerprint=canonical_json_sha256(
            {"payload": digest, "prompt": prompt.system_prompt_sha256}
        ),
        remaining_scene_call_budget=23,
        max_output_tokens=4096,
    )
