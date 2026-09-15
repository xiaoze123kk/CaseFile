"""Current model selection for newly created DeepSeek work.

Frozen TaskRun records and historical qualification descriptors are never edited.
All current DeepSeek product tasks use the canonical Flash API identifier.
"""

from typing import Final

DEEPSEEK_MODEL_ID: Final = "deepseek-flash"


def model_for_new_task(provider: str, configured_model: str) -> str:
    return DEEPSEEK_MODEL_ID if provider == "deepseek" else configured_model
