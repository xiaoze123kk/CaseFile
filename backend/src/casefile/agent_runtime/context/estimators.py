"""Deterministic, conservative token estimators for multi-provider BYOK chat."""

from __future__ import annotations

from dataclasses import dataclass, field

from casefile.agent_runtime.context.protocols import TokenEstimator


def estimate_conservative_tokens(text: str) -> int:
    """Estimate an upper-bound token count without a provider tokenizer.

    Non-ASCII characters count one token each; every group of up to four ASCII
    characters counts one token. The estimator is stable, monotonic and
    conservative; per-provider estimators register over it and calibrate from
    real usage records as they accumulate.
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


class TokenEstimatorRegistryError(RuntimeError):
    """An estimator registration or selection contract violation."""


@dataclass(slots=True)
class TokenEstimatorRegistry:
    """Ordered estimator table; the first ``supports`` match wins."""

    _estimators: list[TokenEstimator] = field(default_factory=list)

    def register(self, estimator: TokenEstimator) -> None:
        if any(item.name == estimator.name for item in self._estimators):
            raise TokenEstimatorRegistryError(
                f"Token estimator {estimator.name!r} is already registered"
            )
        self._estimators.append(estimator)

    def select(self, provider: str, model_id: str) -> TokenEstimator:
        for estimator in self._estimators:
            if estimator.supports(provider, model_id):
                return estimator
        raise TokenEstimatorRegistryError(
            f"No token estimator supports provider {provider!r} model {model_id!r}"
        )

    def names(self) -> tuple[str, ...]:
        return tuple(estimator.name for estimator in self._estimators)


def default_token_estimator_registry() -> TokenEstimatorRegistry:
    """Build the estimator table with the shipped conservative fallback."""

    registry = TokenEstimatorRegistry()
    registry.register(CONSERVATIVE_TOKEN_ESTIMATOR)
    return registry


@dataclass(frozen=True, slots=True)
class UsageTokenSample:
    """One real provider usage record used to calibrate future estimators."""

    provider: str
    model_id: str
    estimated_input_tokens: int
    actual_input_tokens: int


def usage_calibration_ratio(samples: list[UsageTokenSample]) -> float | None:
    """Return the median actual/estimated ratio over usable samples.

    Values above 1.0 mean the estimator is under-counting. Callers may persist
    this ratio and fold it into a calibrated estimator; the conservative
    estimator remains the fallback until per-provider entries prove stable.
    """

    ratios = [
        sample.actual_input_tokens / sample.estimated_input_tokens
        for sample in samples
        if sample.estimated_input_tokens > 0 and sample.actual_input_tokens >= 0
    ]
    if not ratios:
        return None
    ordered = sorted(ratios)
    return ordered[len(ordered) // 2]


__all__ = [
    "CONSERVATIVE_TOKEN_ESTIMATOR",
    "CharTokenEstimator",
    "TokenEstimatorRegistry",
    "TokenEstimatorRegistryError",
    "UsageTokenSample",
    "default_token_estimator_registry",
    "estimate_conservative_tokens",
    "usage_calibration_ratio",
]
