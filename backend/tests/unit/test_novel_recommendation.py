"""The editorial recommender is a bounded real-provider adapter, never a mock UI."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from casefile.agent_runtime.novel_recommendation import recommend_novel

RECOMMENDATION = {
    "concept": "紧凑的渡轮谜案",
    "rationale": "单一谜题适合集中调查与揭晓。",
    "chapters": 2,
    "scenes": 4,
    "style": "克制，信息通过行动呈现。",
}


def test_recommendation_uses_document_and_preferences_without_hidden_retry():
    with patch("casefile.agent_runtime.novel_recommendation.OpenAI") as client:
        call = client.return_value.__enter__.return_value.chat.completions.create
        call.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(RECOMMENDATION)),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=30),
        )
        result, usage = recommend_novel(
            {"case": {"title": "午夜回航"}}, "一口气读完", "test-secret"
        )
        assert result.model_dump() == RECOMMENDATION
        assert usage == {"input_tokens": 40, "output_tokens": 30}
        assert client.call_args.kwargs["max_retries"] == 0
        request = call.call_args.kwargs
        assert "午夜回航" in request["messages"][1]["content"]
        assert "一口气读完" in request["messages"][1]["content"]
        assert "test-secret" not in json.dumps(request)
        call.assert_called_once()


@pytest.mark.parametrize(
    "content,finish",
    [
        ("{}", "stop"),
        (json.dumps({**RECOMMENDATION, "chapters": 8}), "stop"),
        (json.dumps(RECOMMENDATION), "length"),
    ],
)
def test_invalid_recommendations_fail_without_fabricated_defaults(content, finish):
    with patch("casefile.agent_runtime.novel_recommendation.OpenAI") as client:
        client.return_value.__enter__.return_value.chat.completions.create.return_value = (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))
                ]
            )
        )
        with pytest.raises(ValueError):
            recommend_novel({}, "", "test-secret")


def test_oversized_input_is_rejected_before_network():
    with patch("casefile.agent_runtime.novel_recommendation.OpenAI") as client:
        with pytest.raises(ValueError, match="too_large"):
            recommend_novel({"text": "x" * 180_001}, "", "test-secret")
        client.assert_not_called()


def test_changed_planning_fingerprint_rejects_approval_without_fallback():
    from casefile.worker.executors.compiler import CompilerExecutionError
    from casefile.worker.executors.story_planner import _approved_or_reusable

    worker = MagicMock()
    worker.session_factory.return_value.__enter__.return_value.scalar.return_value = None
    with patch("casefile.worker.executors.story_planner._find_reusable") as fallback:
        with pytest.raises(CompilerExecutionError, match="approved_plan_stale"):
            _approved_or_reusable(
                worker,
                7,
                "changed_fingerprint",
                {
                    "approved_novel_plan": {"content_hash": "approved_hash"},
                },
            )
        fallback.assert_not_called()
