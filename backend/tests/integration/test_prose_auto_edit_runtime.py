"""Actual Worker and PostgreSQL coverage for model-owned bounded editing."""

from dataclasses import replace
from typing import Any

import pytest
from test_prose_shadow_runtime import _prepare, _providers, _result, _run

from casefile.agent_runtime.prose_rewriter import FakeProseRewriterProvider

pytestmark = pytest.mark.postgres


class EditorialProvider:
    def __init__(self, action: str, invalid: bool = False) -> None:
        self.action = action
        self.invalid = invalid
        self.requests: list[Any] = []

    def rewrite_scene(self, request: Any) -> Any:
        self.requests.append(request)
        data = request.input_payload["untrusted_data"]
        stage = request.input_payload.get("auto_edit_stage")
        if stage is None:
            candidate = {
                "schema_id": "compiler.scene-render-candidate.v1",
                "blocks": [
                    {"text": b["text"] + "他停顿了一下。"} for b in data["current_render"]["blocks"]
                ],
            }
        else:
            if stage == "selection":
                assert all(set(c) == {"blocks"} for c in data["candidates"].values())
            candidate = (
                {}
                if self.invalid
                else {
                    "action": self.action if stage == "review" else "select_candidate_a",
                    "decision_stage": stage,
                    "findings": [
                        {
                            "check_id": c["check_id"],
                            "assessment": "valid",
                            "severity": "none",
                            "reason": "内容符合要求。",
                        }
                        for c in data["checklist"]["checks"]
                    ],
                    "revision_plan": "调整停顿，保留事件与人物认知。",
                    "rationale": "保留连贯的内容。",
                    "unresolved_issues": [],
                }
            )
        return FakeProseRewriterProvider(candidates=(candidate,)).rewrite_scene(request)


@pytest.mark.parametrize("action", ["retain", "full_rewrite", "polish"])
def test_worker_auto_edit_persists_and_delivers(workflow_database, action):
    factory, _, run, _ = _prepare(workflow_database, prose_mode="auto_edit")
    editorial = EditorialProvider(action)
    providers = replace(_providers(), rewriter=editorial)
    _run(factory, run, providers, workflow_database[2])
    _, manifest, artifacts = _result(factory, run)
    assert manifest["shadow_status"] == "succeeded", manifest["incomplete_reason"]
    assert manifest["runtime"]["version"] == "prose-shadow-runtime-v14"
    assert len(manifest["scenes"]) == 2
    assert all(s["auto_edit_review"] == "completed" for s in manifest["scenes"])
    assert all(s["literary_review"] == "completed" for s in manifest["scenes"])
    assert all(
        s["physical_request_count"] == (2 if action == "retain" else 4) for s in manifest["scenes"]
    )
    assert providers.judge.calls == 0
    judge_requests = [
        request for request in editorial.requests if request.input_payload.get("auto_edit_stage")
    ]
    assert all(
        request.input_payload["response_schema"]["properties"]["unresolved_issues"]["type"]
        == "array"
        for request in judge_requests
    )
    reports = [a for a in artifacts if a.schema_id == "compiler.prose-revision-decision.v1"]
    assert len(reports) == (2 if action == "retain" else 4)


def test_worker_auto_edit_bad_judge_retries_once_and_delivers_unreviewed(workflow_database):
    factory, _, run, _ = _prepare(workflow_database, prose_mode="auto_edit")
    editorial = EditorialProvider("retain", invalid=True)
    _run(factory, run, replace(_providers(), rewriter=editorial), workflow_database[2])
    _, manifest, _ = _result(factory, run)
    assert manifest["shadow_status"] == "succeeded", manifest["incomplete_reason"]
    assert len(editorial.requests) == 4
    assert all(
        request.input_payload["protocol_repair"]["validation_errors"]
        for request in editorial.requests
        if "protocol_repair" in request.input_payload
    )
    assert all(s["auto_edit_review"] == "incomplete" for s in manifest["scenes"])
    assert all(not s["strict_semantic_pass"] for s in manifest["scenes"])
    assert all(s["physical_request_count"] == 3 for s in manifest["scenes"])
