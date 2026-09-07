"""Private, attempt-fenced compiler response journal before domain validation."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.data_postgres.models import AgentModelCall
from casefile.worker.executors.prose_store import fence_prose_step
from casefile.worker.failures import CompilerExecutionError


class CompilerEvidenceProvider:
    """Wrap compiler-only calls; private response evidence never becomes a TaskEvent."""

    def __init__(
        self, provider: Any, factory: sessionmaker[Session], worker_id: str, step_id: int
    ) -> None:
        self.provider = provider
        self.factory = factory
        self.worker_id = worker_id
        self.step_id = step_id

    def _save(self, raw: str, usage: dict[str, Any], phase: str) -> None:
        encoded = raw.encode("utf-8")
        with self.factory() as session, session.begin():
            fence_prose_step(session, self.worker_id, self.step_id)
            call = session.scalar(
                select(AgentModelCall)
                .where(
                    AgentModelCall.agent_step_run_id == self.step_id,
                    AgentModelCall.status == "running",
                )
                .order_by(AgentModelCall.call_no.desc())
                .with_for_update()
            )
            if call is None:
                raise CompilerExecutionError("compiler_response_call_missing")
            # Keep the exact full response separately from the bounded display column.
            entry = {
                "raw_response": raw,
                "sha256": sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
                "phase": phase,
            }
            previous = (call.response_jsonb or {}).get("responses", [])
            call.response_jsonb = {**entry, "responses": [*previous, entry]}
            call.raw_output_text = encoded[:262144].decode("utf-8", errors="ignore")
            call.raw_output_truncated = len(encoded) > len(call.raw_output_text.encode("utf-8"))
            call.output_size_bytes = len(encoded)
            call.usage_jsonb = usage
            call.parse_status = "response_saved"

    def _call(self, method: str, request: Any) -> Any:
        saved = False

        def received(raw: str, usage: dict[str, Any], phase: str) -> None:
            nonlocal saved
            self._save(raw, usage, phase)
            saved = True

        original_emit = request.emit

        def emit(kind: str, stage: str, payload: dict[str, Any]) -> None:
            if kind == "agent.model_call.failed":
                self._save_error(payload)
                raw = payload.get("_raw_output")
                if isinstance(raw, str) and not saved:
                    received(raw, payload.get("usage", {}), "provider_failed")
            # Do not forward private raw output to public event consumers.
            original_emit(
                kind,
                stage,
                {
                    k: v
                    for k, v in payload.items()
                    if k not in {"raw_output_text", "raw_output", "_raw_output"}
                },
            )

        try:
            result = getattr(self.provider, method)(
                replace(request, on_response=received, emit=emit)
            )
        except Exception as error:
            raw = getattr(error, "raw_output", None)
            if isinstance(raw, str) and not saved:
                received(raw, getattr(error, "usage", {}), "provider_failed")
            self._save_error(
                {
                    "exception_type": type(error).__name__,
                    "code": getattr(error, "reason_code", "compiler_provider_failed"),
                }
            )
            raise
        if not saved:
            value = next(
                getattr(result, name)
                for name in ("proposal", "fill", "candidate", "patch")
                if hasattr(result, name)
            )
            received(
                result.raw_output
                if result.raw_output is not None
                else json.dumps(value, ensure_ascii=False),
                result.usage,
                "provider_returned",
            )
        return result

    def _save_error(self, evidence: dict[str, Any]) -> None:
        with self.factory() as session, session.begin():
            fence_prose_step(session, self.worker_id, self.step_id)
            call = session.scalar(
                select(AgentModelCall)
                .where(
                    AgentModelCall.agent_step_run_id == self.step_id,
                    AgentModelCall.status == "running",
                )
                .order_by(AgentModelCall.call_no.desc())
                .with_for_update()
            )
            if call is not None:
                if call.response_jsonb is None:
                    call.parse_status = "response_unavailable"
                    call.usage_jsonb = {**call.usage_jsonb, "usage_known": False}
                call.issues_jsonb = [
                    *call.issues_jsonb,
                    {
                        k: v
                        for k, v in evidence.items()
                        if k not in {"raw_output_text", "raw_output", "_raw_output"}
                    },
                ]

    def plan_story(self, request: Any) -> Any:
        return self._call("plan_story", request)

    def patch_story(self, request: Any) -> Any:
        return self._call("patch_story", request)

    def propose_skeleton(self, request: Any) -> Any:
        return self._call("propose_skeleton", request)

    def fill_semantics(self, request: Any) -> Any:
        return self._call("fill_semantics", request)

    def fill_scene_batch(self, request: Any) -> Any:
        return self._call("fill_scene_batch", request)


def compiler_error_evidence(error: Exception) -> dict[str, Any]:
    """Keep deterministic validation details without exception reprs or request secrets."""
    from pydantic import ValidationError

    evidence: dict[str, Any] = {
        "exception_type": type(error).__name__,
        **getattr(error, "evidence", {}),
    }
    cause = error if isinstance(error, ValidationError) else error.__cause__
    if isinstance(cause, ValidationError):
        evidence["validation_errors"] = cause.errors(
            include_input=False, include_context=False, include_url=False
        )
    return evidence
