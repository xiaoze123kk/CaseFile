"""Bounded, provider-backed editorial recommendation before Story Planner."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime.deepseek_transport import model_checked_client as OpenAI
from casefile.agent_runtime.model_call_audit import model_call_binding
from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile_contracts import NovelRecommendation


def recommend_novel(
    document: dict[str, Any], preferences: str, api_key: str
) -> tuple[NovelRecommendation, dict[str, int]]:
    """One request, no hidden retries or fake fallback; never returns credentials."""
    data = json.dumps({"casefile": document, "preferences": preferences}, ensure_ascii=False)
    if len(data) > 180_000:
        raise ValueError("novel_recommendation_input_too_large")
    prompt = load_prompt("novel_recommendation")
    with model_call_binding(
        {
            "agent": "novel_recommendation",
            "prompt_version": prompt.version,
            "skill_release": prompt.skill_metadata["release"],
            "skill_metadata": prompt.skill_metadata,
        }
    ):
        client_context = OpenAI(
            api_key=api_key, base_url="https://api.deepseek.com", timeout=90, max_retries=0
        )
        with client_context as client:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL_ID,
                messages=[
                    {"role": "system", "content": prompt.system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            NovelRecommendation.model_json_schema(), ensure_ascii=False
                        )
                        + "\n"
                        + data,
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=3000,
                temperature=0,
                extra_body={"thinking": {"type": "disabled"}},
            )
    if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
        raise ValueError("novel_recommendation_incomplete")
    recommendation = NovelRecommendation.model_validate_json(
        response.choices[0].message.content or ""
    )
    if recommendation.chapters > recommendation.scenes:
        raise ValueError("novel_recommendation_structure_invalid")
    usage = response.usage
    return recommendation, {
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
    }
