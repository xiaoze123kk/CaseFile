"""Invalid model preference is missing evidence, never a tie."""

from types import SimpleNamespace

import pytest

from casefile.benchmark import prose_auto_edit_comparison as comparison


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        ("{}", None),
        ('{"preference":"tie"}', "tie"),
        ('{"preference":"a"}', "a"),
        ('{"preference":"anything"}', None),
    ],
)
def test_anonymous_comparison_preserves_invalid_denominator(monkeypatch, raw, expected):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason="stop")],
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)),
        close=lambda: None,
    )
    monkeypatch.setattr(comparison, "OpenAI", lambda **kwargs: client)
    result = comparison._blind_compare("fake", "甲稿", "乙稿")
    assert result["preference"] == expected
    assert result["protocol_valid"] is (expected is not None)
    assert result["raw_response"] == raw
