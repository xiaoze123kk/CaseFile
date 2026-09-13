"""Supported provider names and public validation for application requests."""

from casefile.application.errors import ApplicationError

SUPPORTED_PROVIDERS = frozenset({"deepseek", "openai"})


def normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ApplicationError(
            "provider_not_supported",
            f"不支持的模型服务：{provider}。",
            status_code=422,
            details={"supported_providers": sorted(SUPPORTED_PROVIDERS)},
        )
    return normalized
