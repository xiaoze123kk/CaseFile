"""Release order, selection, tamper detection and dynamic-context isolation."""

from hashlib import sha256
from pathlib import Path

import pytest

from casefile.agent_runtime.skill_assembly import (
    InstructionResource,
    assemble_instructions,
    request_identity,
)


def test_selection_order_and_tamper(tmp_path: Path) -> None:
    resources = []
    for name in ("first", "second", "unused"):
        (tmp_path / f"{name}.md").write_text(name)
        resources.append(InstructionResource(name, f"{name}.md", sha256(name.encode()).hexdigest()))
    kwargs = dict(
        root=tmp_path,
        role="role",
        contract="\ncontract",
        resources=resources,
        selected=["second", "first", "first"],
        reasons={"first": "stage", "second": "repair"},
    )
    result = assemble_instructions(**kwargs)
    assert result.resources == ("first", "second")
    assert result.prefix == "role\ncontract\n\nfirst\n\nsecond"
    assert result == assemble_instructions(**kwargs)
    (tmp_path / "first.md").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        assemble_instructions(**kwargs)


def test_request_identity_preserves_message_and_tool_order() -> None:
    body = {
        "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": "scene1"}],
        "tools": ["a", "b"],
    }
    first = request_identity(body)
    body["messages"][1]["content"] = "scene2"
    second = request_identity(body)
    assert first["stable_prefix_hash"] == second["stable_prefix_hash"]
    assert first["request_fingerprint"] != second["request_fingerprint"]
    body["tools"].reverse()
    assert request_identity(body)["tool_definition_hash"] != first["tool_definition_hash"]
