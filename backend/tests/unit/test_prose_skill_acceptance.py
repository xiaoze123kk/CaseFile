"""Quality acceptance must use readable, reproducibly compiled public inputs."""

import json
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from casefile.benchmark.prose_cost_budget import PriceSnapshot
from casefile.benchmark.prose_skill_acceptance import Audit, load_cases


def test_clean_cases_keep_bound_references_and_authored_faults():
    suite, cases = load_cases()
    assert len(cases) == 3
    assert "\ufffd" not in json.dumps(suite, ensure_ascii=False)
    assert cases[0]["common"]["previous_scene_render"] is None
    assert cases[1]["common"]["previous_scene_render"]["scene_id"] == "scene_1"
    assert cases[2]["common"]["previous_scene_render"]["scene_id"] == "scene_2"
    assert all(c["gold"].consensus["scene_verdict"] == "fail" for c in cases)
    assert all(c["gold"].consensus["render_hash"] for c in cases)


def test_audit_records_retries_without_serializing_credentials(tmp_path):
    @dataclass
    class Request:
        api_key: str = "secret-canary"
        model_id: str = "deepseek-flash"
        max_output_tokens: int = 100
        request_fingerprint: str = "same"

    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, prompt_cache_hit_tokens=0),
        model="deepseek-flash",
        model_dump=lambda **kw: {"model": "deepseek-flash"},
    )
    provider = SimpleNamespace(_create_completion=lambda request: response)
    prices = PriceSnapshot(
        "deepseek-flash", "2026-09-15", "official", Decimal("0.04"), Decimal("2"), Decimal("8")
    )
    audit = Audit(tmp_path, prices, Decimal("1"))
    audit.context = {"role": "rewriter", "stage": "fidelity", "repetition": 1}
    audit.wrap(provider)
    provider._create_completion(Request())
    provider._create_completion(Request())
    audit.context["repetition"] = 2
    provider._create_completion(Request())
    assert [r["attempt"] for r in audit.rows] == [1, 2, 1]
    assert all(r["latency_ms"] >= 0 for r in audit.rows)
    assert "secret-canary" not in (tmp_path / "calls.json").read_text(encoding="utf-8")
