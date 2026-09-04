"""Composition and durable replay adapters for the existing prose Provider ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from casefile.agent_runtime.prose_judge import (
    DeepSeekProseJudgeProvider,
    ProseJudgeProvider,
    ProseJudgeProviderResult,
    ProseJudgeTransportAttempt,
)
from casefile.agent_runtime.prose_polisher import (
    DeepSeekProsePolisherProvider,
    ProsePolisherProvider,
    ProsePolisherProviderResult,
    ProsePolisherTransportAttempt,
)
from casefile.agent_runtime.prose_quality_critic import (
    DeepSeekProseQualityCriticProvider,
    ProseQualityCriticProvider,
    ProseQualityProviderResult,
    ProseQualityTransportAttempt,
)
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    ProseRewriterProvider,
    ProseRewriterProviderResult,
    ProseRewriterTransportAttempt,
)
from casefile.agent_runtime.prose_writer import (
    DeepSeekProseWriterProvider,
    ProseWriterProvider,
    ProseWriterProviderResult,
    ProseWriterTransportAttempt,
)
from casefile.worker.executors.prose_store import ProseStore


@dataclass(frozen=True)
class ProseProviders:
    writer: ProseWriterProvider
    rewriter: ProseRewriterProvider
    judge: ProseJudgeProvider
    quality: ProseQualityCriticProvider
    polisher: ProsePolisherProvider

    @classmethod
    def deepseek(cls) -> ProseProviders:
        return cls(
            DeepSeekProseWriterProvider(),
            DeepSeekProseRewriterProvider(),
            DeepSeekProseJudgeProvider(),
            DeepSeekProseQualityCriticProvider(),
            DeepSeekProsePolisherProvider(),
        )


class DurableProseProvider:
    def __init__(self, sources: ProseProviders, store: ProseStore) -> None:
        self.sources = sources
        self.store = store
        self.steps: dict[str, int] = {}

    def _invoke[T](
        self,
        component: str,
        method: Any,
        request: Any,
        result_type: type[T],
        transport_type: Any,
        *,
        judge: bool = False,
    ) -> T:
        recovered = self.store.begin_request(component, request, result_type, transport_type)
        assert self.store.current_step_id is not None
        self.steps[request.request_fingerprint] = self.store.current_step_id
        if recovered is not None:
            return cast(T, recovered)
        physical_hooks = judge and isinstance(self.sources.judge, DeepSeekProseJudgeProvider)
        if physical_hooks and isinstance(self.sources.judge, DeepSeekProseJudgeProvider):
            self.sources.judge.before_transport = self.store.before_transport
            self.sources.judge.failed_transport = self.store.failed_transport
        else:
            self.store.before_transport(request, 1)
        try:
            with self.store.heartbeat():
                result = method(request)
        except Exception as error:
            failure = getattr(error, "failed_call", None)
            if failure is not None:
                for attempt in failure.transport_attempts:
                    self.store.failed_transport(attempt)
            raise
        self.store.save_response(result)
        return cast(T, result)

    def write_scene(self, request: Any) -> ProseWriterProviderResult:
        return self._invoke(
            "prose_writer",
            self.sources.writer.write_scene,
            request,
            ProseWriterProviderResult,
            ProseWriterTransportAttempt,
        )

    def rewrite_scene(self, request: Any) -> ProseRewriterProviderResult:
        return self._invoke(
            "prose_rewrite",
            self.sources.rewriter.rewrite_scene,
            request,
            ProseRewriterProviderResult,
            ProseRewriterTransportAttempt,
        )

    def judge_scene(self, request: Any) -> ProseJudgeProviderResult:
        return self._invoke(
            f"prose_{request.role}_judge",
            self.sources.judge.judge_scene,
            request,
            ProseJudgeProviderResult,
            ProseJudgeTransportAttempt,
            judge=True,
        )

    def arbitrate_scene(self, request: Any) -> ProseJudgeProviderResult:
        return self._invoke(
            "prose_arbiter",
            self.sources.judge.arbitrate_scene,
            request,
            ProseJudgeProviderResult,
            ProseJudgeTransportAttempt,
            judge=True,
        )

    def assess_quality(self, request: Any) -> ProseQualityProviderResult:
        return self._invoke(
            "prose_quality_critic",
            self.sources.quality.assess_quality,
            request,
            ProseQualityProviderResult,
            ProseQualityTransportAttempt,
        )

    def polish_scene(self, request: Any) -> ProsePolisherProviderResult:
        return self._invoke(
            "prose_polisher",
            self.sources.polisher.polish_scene,
            request,
            ProsePolisherProviderResult,
            ProsePolisherTransportAttempt,
        )
