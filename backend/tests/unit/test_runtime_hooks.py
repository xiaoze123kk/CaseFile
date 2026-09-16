"""Isolation, ordering and error semantics shared by synchronous Agent hooks."""

import asyncio

import pytest

from casefile.agent_runtime.runtime_hooks import (
    HookBinding,
    HookEvent,
    HookExecutionError,
    HookInput,
    HookResult,
    SyncHookDispatcher,
)


def test_callbacks_cannot_change_inputs_or_other_callbacks_and_records_are_metadata_only():
    seen = []

    def mutate(event):
        event.payload["nested"]["text"] = "changed"
        return HookResult()

    def inspect(event):
        seen.append(event.payload["nested"]["text"])
        return HookResult()

    dispatcher = SyncHookDispatcher({("mutate", "1"): mutate, ("inspect", "1"): inspect})
    first = HookBinding("mutate", "1", HookEvent.AFTER_ARTIFACT)
    second = HookBinding("inspect", "1", HookEvent.AFTER_ARTIFACT)
    records = []
    payload = {"nested": {"text": "private manuscript"}}
    dispatcher.dispatch(
        (first, first, second),
        HookInput(HookEvent.AFTER_ARTIFACT, "scene", payload=payload),
        records=records,
    )
    assert payload["nested"]["text"] == "private manuscript"
    assert seen == ["private manuscript"]
    assert [r["handler_id"] for r in records] == ["mutate", "inspect"]
    assert "private manuscript" not in repr(records)


def test_unknown_binding_fails_before_any_callback():
    calls = []
    dispatcher = SyncHookDispatcher({("known", "1"): lambda _: calls.append(1)})
    with pytest.raises(HookExecutionError, match="Unknown hook"):
        dispatcher.dispatch(
            (
                HookBinding("known", "1", HookEvent.BEFORE_STAGE),
                HookBinding("unknown", "1", HookEvent.BEFORE_STAGE),
            ),
            HookInput(HookEvent.BEFORE_STAGE, "prepare"),
            records=[],
        )
    assert calls == []


def test_required_hook_preserves_original_exception_for_repair():
    original = ValueError("private text must never be recorded")

    def fail(_):
        raise original

    records = []
    with pytest.raises(ValueError) as caught:
        SyncHookDispatcher({("fail", "1"): fail}).dispatch(
            (HookBinding("fail", "1", HookEvent.AFTER_ARTIFACT),),
            HookInput(HookEvent.AFTER_ARTIFACT, "validate"),
            records=records,
        )
    assert caught.value is original
    assert records[0]["status"] == "failed"
    assert "private text" not in repr(records)


def test_optional_observer_failure_does_not_block_and_cancellation_propagates():
    def fail(_):
        raise ValueError("observer failed")

    def cancel(_):
        raise asyncio.CancelledError()

    records = []
    with pytest.raises(asyncio.CancelledError):
        SyncHookDispatcher({("fail", "1"): fail, ("cancel", "1"): cancel}).dispatch(
            (
                HookBinding("fail", "1", HookEvent.STAGE_FINISHED, required=False),
                HookBinding("cancel", "1", HookEvent.STAGE_FINISHED, required=False),
            ),
            HookInput(HookEvent.STAGE_FINISHED, "done"),
            records=records,
        )
    assert [r["status"] for r in records] == ["failed", "cancelled"]


@pytest.mark.parametrize(
    "result", [None, HookResult(resources=("prompt",)), HookResult(issues=({"code": "repair"},))]
)
def test_sync_boundary_never_silently_discards_unsupported_results(result):
    with pytest.raises((TypeError, ValueError, HookExecutionError)):
        SyncHookDispatcher({("check", "1"): lambda _: result}).dispatch(
            (HookBinding("check", "1", HookEvent.AFTER_ARTIFACT),),
            HookInput(HookEvent.AFTER_ARTIFACT, "validate"),
            records=[],
        )
