"""Composition and durable replay adapters for the existing prose Provider ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from casefile.agent_runtime.prose_continuity import DeepSeekContinuityProvider
from casefile.agent_runtime.prose_judge import (
    DeepSeekProseJudgeProvider,
    ProseCouncilProtocolError,
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
    ProseQualityInfrastructureError,
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
    continuity: ProseWriterProvider | None = None

    @classmethod
    def deepseek(cls) -> ProseProviders:
        return cls(
            DeepSeekProseWriterProvider(),
            DeepSeekProseRewriterProvider(),
            DeepSeekProseJudgeProvider(),
            DeepSeekProseQualityCriticProvider(),
            DeepSeekProsePolisherProvider(),
            DeepSeekContinuityProvider(),
        )


class DurableProseProvider:
    allow_protocol_repair = True
    allow_generation_repair = True

    def __init__(self, sources: ProseProviders, store: ProseStore) -> None:
        self.sources = sources
        self.store = store
        self.steps: dict[str, int] = {}
        self.judge_requests: dict[str, set[str]] = {}
        self.scene_requests: dict[str, set[str]] = {}

    def record_generation_failure(self, fingerprint: str, details: dict[str, Any]) -> None:
        self.store.reject_response(self.steps[fingerprint], details)

    @property
    def remaining_judge_calls(self) -> int:
        return max(0, 3 - self.store.judge_call_count())

    def record_protocol_failure(self, fingerprint: str, details: dict[str, Any]) -> None:
        self.store.reject_response(self.steps[fingerprint], details)

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
        if judge:
            requests = self.judge_requests.setdefault(self.store.scene_id, set())
            if request.request_fingerprint not in requests:
                if len(requests) >= 3:
                    raise ProseCouncilProtocolError("prose_judge_scene_budget_exhausted")
                requests.add(request.request_fingerprint)
        scene_requests = self.scene_requests.setdefault(self.store.scene_id, set())
        if request.request_fingerprint not in scene_requests:
            if len(scene_requests) >= 23:
                raise ProseCouncilProtocolError("prose_scene_call_budget_exhausted")
            scene_requests.add(request.request_fingerprint)
        recovered = self.store.begin_request(component, request, result_type, transport_type)
        assert self.store.current_step_id is not None
        self.steps[request.request_fingerprint] = self.store.current_step_id
        if recovered is not None:
            return cast(T, recovered)
        physical_hooks = (
            judge
            and component != "prose_continuity"
            and isinstance(self.sources.judge, DeepSeekProseJudgeProvider)
        )
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
            else:
                self.store.failed_transport(
                    transport_type(
                        attempt_index=1,
                        status="failed",
                        latency_ms=0,
                        error_code=f"{component}_provider_failed:{type(error).__name__}",
                        response_observed=False,
                        usage=None,
                    )
                )
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

    def review_continuity(self, request: Any) -> ProseWriterProviderResult:
        assert self.sources.continuity is not None
        return self._invoke(
            "prose_continuity",
            self.sources.continuity.write_scene,
            request,
            ProseWriterProviderResult,
            ProseWriterTransportAttempt,
            judge=True,
        )

    def rewrite_scene(self, request: Any) -> ProseRewriterProviderResult:
        return self._invoke(
            "prose_revision"
            if request.input_payload.get("output_schema_id")
            == "compiler.prose-revision-decision.v1"
            else "prose_rewrite",
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
        try:
            return self._assess_quality(request)
        except ProseQualityInfrastructureError:
            # One explicit transport retry; both physical calls stay in the journal.
            return self._assess_quality(request)

    def _assess_quality(self, request: Any) -> ProseQualityProviderResult:
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
