"""Production scene-wide Judge budget is checked before persistence or transport."""

from types import SimpleNamespace

import pytest

from casefile.agent_runtime.prose_judge import ProseCouncilProtocolError
from casefile.worker.executors.prose_providers import DurableProseProvider


def test_fourth_judge_request_is_rejected_before_provider_or_database_call():
    begun = []
    store = SimpleNamespace(scene_id="scene_1", current_step_id=1)
    store.begin_request = lambda *args: begun.append(args) or object()
    store.judge_call_count = lambda: len(begun) if store.scene_id == "scene_1" else 0
    provider = DurableProseProvider(SimpleNamespace(judge=SimpleNamespace(judge_scene=None)), store)
    for number in range(3):
        provider.judge_scene(SimpleNamespace(role="fidelity", request_fingerprint=str(number)))
    assert provider.remaining_judge_calls == 0
    with pytest.raises(ProseCouncilProtocolError, match="prose_judge_scene_budget_exhausted"):
        provider.judge_scene(SimpleNamespace(role="fidelity", request_fingerprint="fourth"))
    assert len(begun) == 3
    store.scene_id = "scene_2"
    assert provider.remaining_judge_calls == 3
