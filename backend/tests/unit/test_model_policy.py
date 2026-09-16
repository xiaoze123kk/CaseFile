"""New work selects Flash without rewriting frozen historical records."""

import httpx
import pytest

from casefile.agent_runtime.deepseek_transport import require_flash_request
from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID, model_for_new_task
from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.api.schemas import ProviderSettingRequest


@pytest.mark.parametrize("saved", ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"])
def test_new_deepseek_work_uses_canonical_flash(saved):
    assert model_for_new_task("deepseek", saved) == "deepseek-flash"


def test_explicit_other_provider_is_not_given_a_deepseek_model():
    assert model_for_new_task("openai", "gpt-5.6-sol") == "gpt-5.6-sol"


def test_settings_default_and_legacy_selection_resolve_to_flash():
    for fields in ({}, {"provider": "deepseek", "model_id": "deepseek-v4-pro"}):
        request = ProviderSettingRequest(api_key="test-key-only", **fields)
        assert request.provider == "deepseek"
        assert request.model_id == DEEPSEEK_MODEL_ID
        assert not request.model_is_custom
    assert (
        ProviderSettingRequest(provider="openai", api_key="test-key-only").model_id == "gpt-5.6-sol"
    )


def test_prose_generation_and_all_reviews_share_flash_binding():
    for mode in ("quick_draft", "full_polish"):
        binding = prose_runtime_binding(2, mode)
        assert binding["generation_model"] == binding["quality_model"] == DEEPSEEK_MODEL_ID
        assert binding["version"] != "prose-shadow-runtime-v10"


def test_http_guard_prevents_legacy_pro_or_alias_requests_before_network():
    for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
        with pytest.raises(ValueError, match="deepseek_flash_required"):
            require_flash_request(
                httpx.Request(
                    "POST",
                    "https://api.deepseek.com/chat/completions",
                    json={"model": model},
                )
            )
    require_flash_request(
        httpx.Request(
            "POST",
            "https://api.deepseek.com/chat/completions",
            json={"model": DEEPSEEK_MODEL_ID},
        )
    )
