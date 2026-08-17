"""Deterministic, conservative token estimators for multi-provider BYOK chat."""

from __future__ import annotations

from dataclasses import dataclass


def estimate_conservative_tokens(text: str) -> int:
    """Estimate an upper-bound token count without a provider tokenizer.

    Non-ASCII characters count one token each; every group of up to four ASCII
    characters counts one token. Provider tokenizers differ, so Phase 1 will
    register per-provider estimators and calibrate them from real usage records;
    this estimator only needs to be stable and conservative.
    """

    if not text:
        return 0
    ascii_chars = 0
    non_ascii_chars = 0
    for character in text:
        if ord(character) < 128:
            ascii_chars += 1
        else:
            non_ascii_chars += 1
    return max(1, non_ascii_chars + (ascii_chars + 3) // 4)


@dataclass(slots=True)
class CharTokenEstimator:
    """Default estimator registered for every provider/model combination."""

    name: str = "char_conservative_v1"

    def estimate(self, text: str) -> int:
        return estimate_conservative_tokens(text)

    def supports(self, provider: str, model_id: str) -> bool:
        return True


CONSERVATIVE_TOKEN_ESTIMATOR = CharTokenEstimator()

__all__ = [
    "CONSERVATIVE_TOKEN_ESTIMATOR",
    "CharTokenEstimator",
    "estimate_conservative_tokens",
]
