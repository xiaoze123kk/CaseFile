"""Typed, invocation-local hooks for the brief-to-draft runtime.

Handlers are injected at composition time, never imported from resource text.
Every handler receives its own snapshot; only typed results cross the boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter
from types import MappingProxyType
from typing import Any


class HookEvent(StrEnum):
    BEFORE_STAGE = "BeforeStage"
    BEFORE_COMPONENT = "BeforeComponent"
    AFTER_ARTIFACT = "AfterArtifact"
    BEFORE_REPAIR = "BeforeRepair"
    STAGE_FINISHED = "StageFinished"
    STAGE_FAILED = "StageFailed"


@dataclass(frozen=True)
class HookBinding:
    handler_id: str
    version: str
    event: HookEvent
    component: str | None = None
    stage: str | None = None
    timeout: float = 5.0
    required: bool = True

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("Hook timeout must be positive")
        if not self.required and self.event not in {
            HookEvent.STAGE_FINISHED,
            HookEvent.STAGE_FAILED,
        }:
            raise ValueError("Only observation hooks may be optional")


@dataclass(frozen=True)
class HookInput:
    event: HookEvent
    stage: str
    component: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    resources: tuple[str, ...] = ()
    issues: tuple[dict[str, Any], ...] = ()
    block_reason: str | None = None
    observations: tuple[str, ...] = ()


HookHandler = Callable[[HookInput], Awaitable[HookResult]]


class HookExecutionError(RuntimeError):
    """A required extension failed, timed out, or blocked execution."""


class HookDispatcher:
    def __init__(self, handlers: Mapping[tuple[str, str], HookHandler]) -> None:
        self.handlers = MappingProxyType(dict(handlers))

    def validate(self, bindings: tuple[HookBinding, ...]) -> None:
        for binding in bindings:
            if (binding.handler_id, binding.version) not in self.handlers:
                raise HookExecutionError(f"Unknown hook: {binding.handler_id}@{binding.version}")

    async def dispatch(
        self,
        bindings: tuple[HookBinding, ...],
        event: HookInput,
        *,
        records: list[dict[str, Any]],
    ) -> HookResult:
        self.validate(bindings)
        resources: list[str] = []
        issues: list[dict[str, Any]] = []
        observations: list[str] = []
        for binding in dict.fromkeys(bindings):
            if binding.event != event.event:
                continue
            if binding.component is not None and binding.component != event.component:
                continue
            if binding.stage is not None and binding.stage != event.stage:
                continue
            started = perf_counter()
            record: dict[str, Any] = {
                "handler_id": binding.handler_id,
                "version": binding.version,
                "event": event.event.value,
                "status": "succeeded",
            }
            try:
                snapshot = HookInput(
                    event.event,
                    event.stage,
                    event.component,
                    MappingProxyType(deepcopy(dict(event.payload))),
                )
                result = await asyncio.wait_for(
                    self.handlers[(binding.handler_id, binding.version)](snapshot),
                    timeout=binding.timeout,
                )
                if not isinstance(result, HookResult):
                    raise TypeError("Hook must return HookResult")
                self._validate_result(event.event, result)
                if result.block_reason:
                    raise HookExecutionError(result.block_reason)
                resources.extend(result.resources)
                for issue in result.issues:
                    if issue not in issues:
                        issues.append(deepcopy(issue))
                observations.extend(result.observations)
                record["issue_count"] = len(result.issues)
                record["resources"] = list(result.resources)
                record["observations"] = list(result.observations)
            except asyncio.CancelledError:
                record["status"] = "cancelled"
                raise
            except Exception as error:
                record.update(status="failed", error_type=type(error).__name__)
                if binding.required:
                    raise HookExecutionError(
                        f"Required hook failed: {binding.handler_id}"
                    ) from error
            finally:
                record["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
                records.append(record)
        return HookResult(
            tuple(dict.fromkeys(resources)), tuple(issues), observations=tuple(observations)
        )

    @staticmethod
    def _validate_result(event: HookEvent, result: HookResult) -> None:
        if result.resources and event not in {HookEvent.BEFORE_COMPONENT, HookEvent.BEFORE_REPAIR}:
            raise ValueError("Resources are only allowed during component preparation")
        if result.issues and event != HookEvent.AFTER_ARTIFACT:
            raise ValueError("Issues are only allowed after an artifact")
        if result.block_reason and event in {HookEvent.STAGE_FINISHED, HookEvent.STAGE_FAILED}:
            raise ValueError("Observers cannot block completed execution")
