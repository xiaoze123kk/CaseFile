"""Provider construction and frozen credential resolution for Worker tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    AgentProvider,
    DeepSeekAgentsProvider,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.data_postgres.models import TaskRun, UserProviderSetting

ProviderFactory = Callable[[TaskRun], AgentProvider]


def required_provider_binding(task: TaskRun) -> tuple[str, str]:
    if not task.provider or not task.model_id:
        raise RuntimeError(f"TaskRun requires a frozen Provider binding: {task.task_type}")
    return task.provider, task.model_id


def provider_for_task(task: TaskRun) -> AgentProvider:
    provider = task.provider
    if provider == "openai":
        return OpenAIAgentsProvider()
    if provider == "deepseek":
        return DeepSeekAgentsProvider()
    raise RuntimeError(f"Unsupported provider frozen on TaskRun: {provider}")


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    provider: AgentProvider
    api_key: str = field(repr=False)


class ProviderResolver:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider_factory: ProviderFactory,
    ) -> None:
        self._session_factory = session_factory
        self._provider_factory = provider_factory

    def resolve(self, task: TaskRun) -> ResolvedProvider:
        provider, _model_id = required_provider_binding(task)
        with self._session_factory() as session, session.begin():
            setting = session.get(UserProviderSetting, task.provider_setting_id)
            if setting is None:
                raise RuntimeError("Frozen provider setting is missing")
            if setting.user_id != task.actor_user_id or setting.provider != provider:
                raise RuntimeError("Frozen provider setting does not match TaskRun provenance")
            if setting.config_version != task.provider_config_version:
                raise RuntimeError("Frozen provider setting version no longer matches TaskRun")
            if (
                setting.credential_status == "deleted"
                or setting.secret_ciphertext is None
                or setting.secret_nonce is None
                or setting.key_version is None
            ):
                raise RuntimeError("Frozen provider credential has been deleted")
            api_key = decrypt_api_key(
                setting.secret_ciphertext,
                setting.secret_nonce,
                user_id=setting.user_id,
                provider=setting.provider,
                key_version=setting.key_version,
            )
        return ResolvedProvider(provider=self._provider_factory(task), api_key=api_key)


__all__ = [
    "ProviderFactory",
    "ProviderResolver",
    "ResolvedProvider",
    "provider_for_task",
    "required_provider_binding",
]
