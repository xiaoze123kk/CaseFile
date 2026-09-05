"""DeepSeek compiler responses use the provider ceiling and reject cut-off JSON."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from casefile.agent_runtime.constraint_first_story_planner import SkeletonProposalRequest
from casefile.agent_runtime.provider_adapters.deepseek import DeepSeekAgentsProvider
from casefile.agent_runtime.story_planner import (
    COMPILER_JSON_MAX_OUTPUT_TOKENS,
    CompilerProviderOutputError,
)
from casefile_contracts import SkeletonProposal


def invoke(raw: str, finish: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=8192, total_tokens=8292),
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock()
    )
    request = SkeletonProposalRequest(
        task_run_id=1,
        prompt_version="story-planner-skeleton-v1",
        planning_problem={},
        model_view={},
        input_hash="a" * 64,
        model_id="deepseek-v4-pro",
        api_key="test-secret",
    )
    with patch(
        "casefile.agent_runtime.provider_adapters.deepseek.AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            DeepSeekAgentsProvider()._story_planner_json_object(
                request,
                instructions="JSON",
                input_text="{}",
                output_type=SkeletonProposal,
                schema_id="compiler.skeleton-proposal.v1",
                stage="skeleton_proposal",
            )
        )
    return result, create


def test_compiler_explicitly_uses_full_provider_output_allowance():
    result, create = invoke('{"schema_id":"compiler.skeleton-proposal.v1","scenes":[]}', "stop")
    assert result[0]["schema_id"] == "compiler.skeleton-proposal.v1"
    assert create.call_args.kwargs["max_tokens"] == COMPILER_JSON_MAX_OUTPUT_TOKENS == 393216


@pytest.mark.parametrize(
    "raw,finish,code",
    [
        ('{"scenes":[', "length", "compiler_model_output_truncated"),
        ('{"scenes":[]}', "length", "compiler_model_output_truncated"),
        ('{"scenes":[', "stop", "compiler_model_output_invalid_json"),
        ("{}", "insufficient_system_resource", "compiler_model_output_incomplete"),
    ],
)
def test_incomplete_output_retains_evidence_and_is_never_a_valid_candidate(raw, finish, code):
    with pytest.raises(CompilerProviderOutputError) as caught:
        invoke(raw, finish)
    assert caught.value.reason_code == code
    assert caught.value.raw_output == raw
    assert caught.value.finish_reason == finish
    assert caught.value.usage["output_tokens"] == 8192
