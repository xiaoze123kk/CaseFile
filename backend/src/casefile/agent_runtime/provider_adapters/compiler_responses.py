"""Journal native Responses output before the Agents SDK parses structured output."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agents import OpenAIResponsesModel
from agents.items import ModelResponse


class CompilerResponsesModel(OpenAIResponsesModel):
    on_response: Callable[[str, dict[str, Any], str], None]

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        response = await super().get_response(*args, **kwargs)
        raw = json.dumps(
            [item.model_dump(mode="json") for item in response.output], ensure_ascii=False
        )
        usage = response.usage
        self.on_response(
            raw,
            {
                "requests": usage.requests,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
            "responses_output_received",
        )
        return response
