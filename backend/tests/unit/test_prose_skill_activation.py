"""Default rollout binds skills; v11 frozen jobs keep their explicit old prompts."""

from copy import deepcopy

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_rewriter import PROSE_REWRITER_PROMPT_VERSION
from casefile.agent_runtime.prose_runtime import matches_prose_runtime, prose_runtime_binding
from casefile.agent_runtime.prose_writer import PROSE_WRITER_PROMPT_VERSION


def test_new_default_and_previous_runtime_are_separate():
    current = prose_runtime_binding(3)
    old = prose_runtime_binding(3, runtime_version="prose-shadow-runtime-v11")
    assert load_prompt("prose_writer").version == PROSE_WRITER_PROMPT_VERSION == "prose-writer-v6"
    assert (
        load_prompt("prose_rewriter").version
        == PROSE_REWRITER_PROMPT_VERSION
        == "prose-rewriter-v9"
    )
    assert current["prompts"]["prose_writer"]["version"] == "prose-writer-v6"
    assert old["prompts"]["prose_writer"]["version"] == "prose-writer-v4"
    assert old["prompts"]["prose_rewriter"]["version"] == "prose-rewriter-v7"
    assert "skills" not in old
    assert current["skills"]["prose_writer"]["manifest_hash"]
    assert matches_prose_runtime(current, 3)
    assert matches_prose_runtime(old, 3)
    damaged = deepcopy(current)
    damaged["skills"]["prose_writer"]["manifest_hash"] = "0" * 64
    assert not matches_prose_runtime(damaged, 3)
    assert not matches_prose_runtime({"version": "unknown"}, 3)
