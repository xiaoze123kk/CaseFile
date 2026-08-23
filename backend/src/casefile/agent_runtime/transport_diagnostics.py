"""Stable, redacted diagnostics for Provider transport and protocol failures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

TransportErrorClass = Literal[
    "timeout",
    "connection",
    "rate_limit",
    "provider_4xx",
    "provider_5xx",
    "protocol_unsupported",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class TransportDiagnostics:
    transport_error_class: TransportErrorClass
    http_status_class: Literal["4xx", "5xx"] | None
    protocol: str
    protocol_phase: str
    network_retry_budget: int
    network_retry_count: int | None
    retry_exhausted: bool
    retry_after_present: bool
    fallback_attempted: bool
    fallback_succeeded: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_transport_error(
    error: BaseException,
    *,
    protocol: str,
    protocol_phase: str,
    network_retry_budget: int,
    retry_exhausted: bool,
    fallback_attempted: bool = False,
    fallback_succeeded: bool = False,
) -> TransportDiagnostics:
    """Classify an exception chain without retaining messages, URLs, or bodies."""

    chain = _exception_chain(error)
    status = next((_status_code(item) for item in chain if _status_code(item)), None)
    names = {type(item).__name__.lower() for item in chain}
    if status == 429 or any("ratelimit" in name for name in names):
        error_class: TransportErrorClass = "rate_limit"
    elif status is not None and 400 <= status < 500:
        error_class = "provider_4xx"
    elif status is not None and 500 <= status < 600:
        error_class = "provider_5xx"
    elif any(
        isinstance(item, TimeoutError) or "timeout" in type(item).__name__.lower() for item in chain
    ):
        error_class = "timeout"
    elif any(
        isinstance(item, (ConnectionError, OSError)) or "connection" in type(item).__name__.lower()
        for item in chain
    ):
        error_class = "connection"
    elif any(
        "protocol" in type(item).__name__.lower() or "unsupported" in type(item).__name__.lower()
        for item in chain
    ):
        error_class = "protocol_unsupported"
    else:
        error_class = "unknown"
    return TransportDiagnostics(
        transport_error_class=error_class,
        http_status_class=(
            "4xx"
            if status is not None and 400 <= status < 500
            else "5xx"
            if status is not None and 500 <= status < 600
            else None
        ),
        protocol=protocol,
        protocol_phase=protocol_phase,
        network_retry_budget=max(network_retry_budget, 0),
        network_retry_count=_network_retry_count(chain),
        retry_exhausted=retry_exhausted,
        retry_after_present=any(_has_retry_after(item) for item in chain),
        fallback_attempted=fallback_attempted,
        fallback_succeeded=fallback_succeeded,
    )


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    values: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(current)
        current = current.__cause__ or current.__context__
    return tuple(values)


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _network_retry_count(chain: tuple[BaseException, ...]) -> int | None:
    for error in chain:
        for name in ("retry_count", "retries", "attempts"):
            value = getattr(error, name, None)
            if isinstance(value, int) and value >= 0:
                return value
    return None


def _has_retry_after(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)
    if headers is None:
        return False
    try:
        return "retry-after" in {str(key).lower() for key in headers}
    except TypeError:
        return False


__all__ = [
    "TransportDiagnostics",
    "TransportErrorClass",
    "classify_transport_error",
]
