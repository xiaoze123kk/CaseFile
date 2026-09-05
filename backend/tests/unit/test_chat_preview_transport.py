import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents import ModelSettings
from casefile.agent_runtime.models import CaseFileChatCandidateV2, CaseFileChatRequest
from casefile.agent_runtime.provider_adapters.shared import _run_auxiliary_agent
from casefile.agent_runtime.structured_output import STRICT_OUTPUT_TOOL_NAME, _stream_strict_answer


def test_strict_stream_extracts_arguments_and_preserves_usage() -> None:
    raw = json.dumps({"answer": "正在检查。"})

    async def chunks():
        for index, fragment in enumerate([raw[:8], raw[8:]]):
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        index=0,
                        delta=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    function=SimpleNamespace(
                                        name=STRICT_OUTPUT_TOOL_NAME if index == 0 else None,
                                        arguments=fragment,
                                    ),
                                )
                            ]
                        ),
                    )
                ],
            )
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=12, completion_tokens=8, total_tokens=20, prompt_cache_hit_tokens=3
            ),
        )

    create = AsyncMock(return_value=chunks())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    text: list[str] = []
    result = asyncio.run(_stream_strict_answer(client, {"model": "test"}, text.append))
    assert "".join(text) == raw == result.raw_output
    assert result.usage["total_tokens"] == 20
    assert result.usage["cached_tokens"] == 3
    assert create.call_args.kwargs["stream"] is True


def test_native_structured_stream_preserves_result_and_excludes_reasoning() -> None:
    answer = "请先核对已经记录的线索。" * 90
    candidate = CaseFileChatCandidateV2(answer=answer)
    raw = candidate.model_dump_json()
    captured: list[tuple[str, dict]] = []

    class Result:
        final_output = candidate
        context_wrapper = SimpleNamespace(
            usage=SimpleNamespace(
                requests=1,
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            )
        )

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(
                    type="response.reasoning_text.delta", delta="PRIVATE REASONING"
                ),
            )
            for offset in range(0, len(raw), 100):
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(
                        type="response.output_text.delta", delta=raw[offset : offset + 100]
                    ),
                )

        def final_output_as(self, *_args, **_kwargs):
            return candidate

    request = CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v17",
        casefile={},
        history=(),
        message="分析当前卷宗",
        editable_fields_by_collection={},
        input_hash="a" * 64,
        model_id="test",
        api_key="sk-private-test-key",
        max_turns=1,
        emit=lambda *_args: None,
        feedback=lambda event, payload: captured.append((event, payload)),
    )
    with patch(
        "casefile.agent_runtime.provider_adapters.shared.Runner.run_streamed", return_value=Result()
    ):
        output, usage = asyncio.run(
            _run_auxiliary_agent(
                request,
                model="test",
                model_settings=ModelSettings(),
                instructions="test",
                input_text="test",
                output_type=CaseFileChatCandidateV2,
                stage="finalizing",
                structured_output=True,
                tracing_disabled=True,
            )
        )
    assert output["answer"] == answer
    assert usage["total_tokens"] == 30
    assert "PRIVATE" not in str(captured)
    assert (
        "".join(payload["text"] for event, payload in captured if event.endswith("delta")) == answer
    )
