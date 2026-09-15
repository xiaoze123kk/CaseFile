"""Release isolation, deterministic prefix and missing-resource fail-closed checks."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from casefile.agent_runtime.prose_skills import (
    assemble_skill,
    bind_skill_request,
    completion_body,
)
from casefile.agent_runtime.prose_writer import PROSE_WRITER_CANDIDATE_SCHEMA
from casefile.agent_runtime.skill_resources import read_skill_resource
from casefile.benchmark.prose_cost_fixtures import smoke_request, smoke_tasks


@pytest.fixture(scope="module")
def tasks():
    return smoke_tasks()


def test_prefix_reused_across_scenes_and_task_changes_fingerprint(tasks):
    first = smoke_request(tasks[0], "prose-writer-v5", "canary")
    second = smoke_request(tasks[1], "prose-writer-v5", "canary")
    schema = PROSE_WRITER_CANDIDATE_SCHEMA
    a = completion_body(first, schema, "focus")
    b = completion_body(second, schema, "focus")
    assert a["messages"][0] == b["messages"][0]
    assert a["messages"][1] != b["messages"][1]
    assert first.request_fingerprint != second.request_fingerprint
    assert "canary" not in str(a)
    assert "stable_prefix_hash" not in str(a)
    again = smoke_request(tasks[0], "prose-writer-v5", "other-key")
    assert first.request_fingerprint == again.request_fingerprint


def test_legacy_and_repair_messages(tasks):
    old = smoke_request(tasks[0], "prose-writer-v4", "")
    schema = PROSE_WRITER_CANDIDATE_SCHEMA
    assert not old.prompt_metadata
    assert bind_skill_request(old, schema) is old
    new = smoke_request(tasks[0], "prose-writer-v5", "")
    repaired = replace(
        new, input_payload={**new.input_payload, "generation_repair": {"issue": "x"}}
    )
    a = completion_body(new, schema, "focus")["messages"]
    b = completion_body(repaired, schema, "focus")["messages"]
    assert a[:2] == b[:2]
    assert "generation_repair" in b[2]["content"]
    assert b[-1] == a[-1]


def test_skill_and_contract_binding_fail_before_transport(tasks, monkeypatch):
    request = smoke_request(tasks[0], "prose-writer-v5", "")
    with pytest.raises(ValueError, match="binding mismatch"):
        assemble_skill(request, {})
    import casefile.agent_runtime.prose_skills as module

    original = module.read_skill_resource

    def changed(*args):
        return original(*args) + "\nchanged"

    monkeypatch.setattr(module, "read_skill_resource", changed)
    with pytest.raises(ValueError, match="changed after request freeze"):
        completion_body(request, PROSE_WRITER_CANDIDATE_SCHEMA, "focus")


def test_resource_hash_and_path_validation(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_skill_resource(tmp_path, "SKILL.md", "0" * 64)
    with pytest.raises(ValueError, match="path"):
        read_skill_resource(tmp_path, "../outside", "0" * 64)
    with pytest.raises(FileNotFoundError):
        read_skill_resource(tmp_path, "missing.md", "0" * 64)


def test_order_dedup_and_manifest_hash_change(tasks, monkeypatch):
    import casefile.agent_runtime.prose_skills as module

    request = smoke_request(tasks[0], "prose-writer-v5", "")
    original = module.json.loads

    def changed_manifest(value):
        result = deepcopy(original(value))
        result["order"] = ["skill", "skill"]
        return result

    monkeypatch.setattr(
        module, "json", SimpleNamespace(loads=changed_manifest, dumps=module.json.dumps)
    )
    prefix, metadata = assemble_skill(request, PROSE_WRITER_CANDIDATE_SCHEMA)
    assert metadata["resources"] == ["skill"]
    assert metadata["stable_prefix_hash"] == request.prompt_metadata["stable_prefix_hash"]
    assert metadata["manifest_hash"] != request.prompt_metadata["manifest_hash"]
    assert prefix.count("正文必须真实实现") == 1


def test_model_view_removes_only_known_trace_envelopes(tasks):
    import json

    from casefile.agent_runtime.prose_model_view import model_view, model_view_text

    request = smoke_request(tasks[2], "prose-rewriter-v9", "")
    original = deepcopy(request.input_payload)
    view = model_view(original)
    assert original == request.input_payload
    before, after = original["untrusted_data"], view["untrusted_data"]
    assert before["checklist"]["checks"] == after["checklist"]["checks"]
    assert before["current_render"]["blocks"] == after["current_render"]["blocks"]
    assert before["repair_findings"] == after["repair_findings"]
    assert before["preserve_checks"] == after["preserve_checks"]
    assert before["revision_decision"] == after["revision_decision"]
    assert before["continuity_reference"]["text"] == after["continuity_reference"]["text"]
    assert (
        original["server_bindings"]["length_contract"] == view["server_bindings"]["length_contract"]
    )
    for old, new in zip(
        before["scene_context"]["object_catalog"],
        after["scene_context"]["object_catalog"],
        strict=True,
    ):
        assert old["value"] == new["value"]
        assert old["object_ref"] == new["object_ref"]
    assert "component_input_hash" not in view["server_bindings"]
    assert json.loads(model_view_text(original)) == view
    assert len(model_view_text(original)) < len(json.dumps(original, ensure_ascii=False))
    arbitrary = {
        "untrusted_data": {
            "scene_context": {
                "object_catalog": [
                    {
                        "value": {"source_fragment_hash": "author-owned", "text": "keep"},
                        "source_ref": {"source_fragment_hash": "compiler-owned"},
                    }
                ]
            }
        }
    }
    item = model_view(arbitrary)["untrusted_data"]["scene_context"]["object_catalog"][0]
    assert item["value"]["source_fragment_hash"] == "author-owned"
    assert not item["source_ref"]
