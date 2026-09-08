"""Chapter adaptation keeps heavy-chain evidence, decision and selection floors."""

import json
from types import SimpleNamespace

import pytest

from casefile.agent_runtime.novel_prose import (
    evidence,
    select_pair,
    validate_checklist,
    validate_decision,
    validate_judge,
)
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS


def check():
    return {
        "check_id": "c1",
        "category": "intent",
        "polarity": "required",
        "requirement": "开门",
        "source_field": "instruction",
        "source_quote": "开门",
    }


def test_checklist_requires_exact_author_source_and_unique_checks():
    context = {
        "instruction": "开门",
        "target": "门开了。",
        "requirements": {"preserve": "人物不变", "allow_changes": ""},
    }
    with pytest.raises(ValueError, match="source_uncovered"):
        validate_checklist({"checks": [check()]}, context)
    context["requirements"]["preserve"] = ""
    assert validate_checklist({"checks": [check()]}, context)["checks"] == [check()]
    with pytest.raises(ValueError, match="duplicate"):
        validate_checklist({"checks": [check(), check()]}, context)
    with pytest.raises(ValueError, match="quote_invalid"):
        validate_checklist({"checks": [{**check(), "source_quote": "虚构"}]}, context)


def test_judge_uses_shared_coverage_and_evidence_rules():
    candidate = {
        "schema_id": "compiler.prose-judge-candidate.v1",
        "assessments": [
            {"check_id": "c1", "verdict": "pass", "evidence_ids": [], "rationale": "已保留"}
        ],
    }
    assert (
        validate_judge(candidate, [check()], "😀门开了。")["assessments"][0]["verdict"]
        == "uncertain"
    )
    catalog = evidence("😀门开了。")
    assert catalog[0]["text"] == "😀门开了。"
    candidate["assessments"][0]["evidence_ids"] = [catalog[0]["evidence_id"]]
    assert validate_judge(candidate, [check()], "😀门开了。")["assessments"][0]["verdict"] == "pass"
    candidate["assessments"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ValueError, match="evidence_catalog_mismatch"):
        validate_judge(candidate, [check()], "😀门开了。")


def test_editor_cannot_retain_fatal_or_extend_exhausted_budget():
    decision = {
        "action": "retain",
        "rationale": "解释",
        "revision_plan": "",
        "findings": [
            {"check_id": "c1", "assessment": "valid", "severity": "fatal", "reason": "事实错误"}
        ],
    }
    with pytest.raises(ValueError, match="fatal_retention"):
        validate_decision(decision, [check()], False)
    decision.update(action="full_rewrite", revision_plan="修正事实")
    with pytest.raises(ValueError, match="budget"):
        validate_decision(decision, [check()], True)


def test_pairwise_reuses_stable_win_and_no_dimension_regression():
    def report(preference):
        return {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            "overall_preference": preference,
            "dimension_preferences": [
                {"dimension": dimension, "preference": preference}
                for dimension in QUALITY_DIMENSIONS
            ],
        }

    assert select_pair(report("b"), report("a"))[0]
    assert not select_pair(report("a"), report("a"))[0]
    second = report("a")
    second["dimension_preferences"][0]["preference"] = "b"
    assert not select_pair(report("b"), second)[0]


def test_real_polisher_adapter_builds_chapter_request_without_compiler_profile(monkeypatch):
    from casefile.agent_runtime import prose_polisher
    from casefile.agent_runtime.novel_prose import ChapterProseProvider

    requests = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"schema_id":"compiler.scene-render-candidate.v1","blocks":[{"text":"新稿"}]}'
                        ),
                        finish_reason="length",
                    )
                ],
            )

        def close(self):
            pass

    monkeypatch.setattr(prose_polisher, "OpenAI", Client)
    result = ChapterProseProvider().invoke(
        "polisher", {"candidate": "原稿"}, "fake", "deepseek-v4-pro"
    )
    assert result.finish_reason == "length"
    assert len(requests) == 1
    assert "完整章节" in requests[0]["messages"][-1]["content"]
    assert requests[0]["max_tokens"] == 32768


def test_new_decision_prompt_is_frozen_without_reinterpreting_old_tasks():
    from casefile.agent_runtime.novel_prose import (
        current_prompt_versions,
        phase_prompt,
        prompt_hashes,
    )

    old = {}
    new = {"prose_prompt_versions": current_prompt_versions()}
    assert phase_prompt("revision", old).version == "novel-revision-v1"
    assert phase_prompt("revision", new).version == "novel-revision-v2"
    assert prompt_hashes(old)["revision"] != prompt_hashes(new)["revision"]


@pytest.mark.parametrize(
    "phase,module_name",
    [
        ("checklist", "prose_rewriter"),
        ("judge", "prose_judge"),
        ("revision", "prose_rewriter"),
        ("rewriter", "prose_rewriter"),
        ("quality_critic", "prose_quality_critic"),
        ("polisher", "prose_polisher"),
        ("pairwise", "prose_quality_critic"),
    ],
)
def test_all_role_adapters_preserve_inputs_and_frozen_prompt(monkeypatch, phase, module_name):
    from importlib import import_module

    from casefile.agent_runtime.novel_prose import (
        ChapterProseProvider,
        current_prompt_versions,
        phase_prompt,
    )
    from casefile.domain.narrative_compiler import canonical_json_sha256

    calls, options = [], []

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
                ],
            )

        def close(self):
            pass

    monkeypatch.setattr(import_module("casefile.agent_runtime." + module_name), "OpenAI", Client)
    payload = {
        "candidate": "😀原稿",
        "instruction": "修订",
        "prose_prompt_versions": current_prompt_versions(),
    }
    result = ChapterProseProvider().invoke(phase, payload, "fake", "deepseek-v4-pro")
    prompt = phase_prompt(phase, payload)
    assert len(calls) == 1 and options[0]["max_retries"] == 0
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["model"] == result.model_id == "deepseek-v4-pro"
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert sent["untrusted_data"] == payload
    assert result.request_payload == sent
    assert result.input_hash == result.request_fingerprint == canonical_json_sha256(sent)
    assert result.prompt_hash == prompt.system_prompt_sha256
    assert result.prompt_version == prompt.version
    assert result.usage["total_tokens"] == 12 and result.finish_reason == "stop"
