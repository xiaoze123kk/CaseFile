"""Public provider validation shared by Brief Intake and workflow requests."""

import pytest

from casefile.application.errors import ApplicationError
from casefile.application.provider_policy import normalize_provider


@pytest.mark.parametrize("value,expected", [(" OpenAI ", "openai"), ("DEEPSEEK", "deepseek")])
def test_supported_provider_names_are_normalized(value: str, expected: str) -> None:
    assert normalize_provider(value) == expected


@pytest.mark.parametrize("value", ["", " fake "])
def test_unsupported_provider_preserves_public_error_contract(value: str) -> None:
    with pytest.raises(ApplicationError) as raised:
        normalize_provider(value)
    assert raised.value.code == "provider_not_supported"
    assert raised.value.status_code == 422
    assert raised.value.message == f"不支持的模型服务：{value}。"
    assert raised.value.details == {"supported_providers": ["deepseek", "openai"]}
