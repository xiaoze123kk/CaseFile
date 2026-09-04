"""Behavioral coverage for the isolated four-call public Quality experiment."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    DeepSeekProseQualityCriticProvider,
    ProseQualityInfrastructureError,
    ProseQualityProtocolError,
    ProseQualityProviderResult,
    ProseQualityRequest,
)
from casefile.agent_runtime.prose_quality_diagnostic import (
    execute_diagnostic_quality,
    validate_single_assessment,
)
from casefile.benchmark import prose_quality_diagnostic as benchmark
from casefile.benchmark.prose_quality_diagnostic import DiagnosticFakeProvider
from casefile.benchmark.prose_quality_diagnostic_report import diagnostic_comparison
from casefile.benchmark.prose_quality_eval import load_prose_quality_dev_suite
from casefile.domain.narrative_compiler import canonical_json_sha256


@pytest.fixture(scope="module")
def package() -> dict[str, Any]:
    return load_prose_quality_dev_suite()


class ObservedProvider(DiagnosticFakeProvider):
    def __init__(self, gold: dict[str, Any], fault: str = "") -> None:
        super().__init__(gold)
        self.requests: list[ProseQualityRequest] = []
        self.fault = fault

    def assess_quality(self, request: ProseQualityRequest) -> ProseQualityProviderResult:
        self.requests.append(request)
        if self.fault == "infrastructure" and len(self.requests) == 2:
            raise ProseQualityInfrastructureError("simulated_transport_failure")
        result = super().assess_quality(request)
        if self.fault == "binding":
            return replace(result, component_input_hash="0" * 64)
        assert result.candidate is not None
        candidate = copy.deepcopy(result.candidate)
        if request.request_kind == "assessment":
            if self.fault == "assessment":
                candidate["dimensions"][0]["severity"] = "high"
            elif self.fault == "observation":
                candidate["dimensions"][0]["observation"] = "另一份有效观察。"
        elif self.fault in ("always_a", "tie", "dimension"):
            if self.fault == "dimension":
                candidate["dimension_preferences"][0]["preference"] = "tie"
            else:
                value = "a" if self.fault == "always_a" else "tie"
                candidate["overall_preference"] = value
                for item in candidate["dimension_preferences"]:
                    item["preference"] = value
        return replace(result, candidate=candidate)


def execute(asset: dict[str, Any], provider: ObservedProvider) -> Any:
    return execute_diagnostic_quality(
        provider,
        checklist=asset["checklist"],
        original_render=asset["render_a"],
        polished_render=asset["render_b"],
        original_consensus=asset["semantic_consensus_a"],
        preservation_consensus=asset["semantic_consensus_b"],
        profile=asset["profile"],
        api_key="fake",
    )


def test_four_calls_isolate_sides_and_swap_assessments_with_text(package: dict[str, Any]) -> None:
    asset = package["tasks"][0]["asset"]
    provider = ObservedProvider(asset["gold"])
    result = execute(asset, provider)
    assert result.status == "completed"
    assert len(result.calls) == 4
    assert [request.request_kind for request in provider.requests] == [
        "assessment",
        "assessment",
        "pairwise",
        "pairwise",
    ]
    first, second, forward, reverse = [request.input_payload for request in provider.requests]
    for single, source in ((first, asset["render_a"]), (second, asset["render_b"])):
        assert set(single["untrusted_data"]) == {"profile", "render"}
        assert [b["text"] for b in single["untrusted_data"]["render"]["blocks"]] == [
            b["text"] for b in source["blocks"]
        ]
        for item in single["server_evidence_catalog"]:
            assert item["block_id"].startswith("block_")
        serialized = json.dumps(single)
        for forbidden in (
            "gold",
            "original",
            "polished",
            "stage",
            "scene_id",
            "previous_render_hash",
            "component_hash",
        ):
            assert f'"{forbidden}"' not in serialized
    assert forward["untrusted_data"]["a"] == reverse["untrusted_data"]["b"]
    assert forward["untrusted_data"]["b"] == reverse["untrusted_data"]["a"]
    assert "overall_preference" not in json.dumps(forward)
    assert all(request.candidate_schema is not None for request in provider.requests)
    assert all(
        request.model_id == "deepseek-v4-flash" and request.network_retries == 0
        for request in provider.requests
    )
    assert PROSE_QUALITY_PAIRWISE_PROMPT_VERSION == "prose-quality-pairwise-v1"
    assert PROSE_QUALITY_COMPONENT_HASH == package["suite"]["quality_component_hash"]


@pytest.mark.parametrize("fault,calls", [("assessment", 1), ("binding", 1), ("infrastructure", 2)])
def test_failure_stops_trial_without_decision(
    package: dict[str, Any], fault: str, calls: int
) -> None:
    asset = package["tasks"][0]["asset"]
    provider = ObservedProvider(asset["gold"], fault)
    result = execute(asset, provider)
    assert result.status == ("inconclusive" if fault == "infrastructure" else "protocol_failed")
    assert result.decision is None
    assert len(provider.requests) == calls


def test_invalid_semantic_upstream_makes_zero_calls(package: dict[str, Any]) -> None:
    for field in ("semantic_consensus_a", "semantic_consensus_b"):
        asset = copy.deepcopy(package["tasks"][0]["asset"])
        asset[field]["render_hash"] = "0" * 64
        provider = ObservedProvider(asset["gold"])
        assert execute(asset, provider).status == "protocol_failed"
        assert provider.requests == []


def test_profile_and_assessment_changes_alter_comparison_fingerprint(
    package: dict[str, Any],
) -> None:
    asset = package["tasks"][0]["asset"]
    first = ObservedProvider(asset["gold"])
    second = ObservedProvider(asset["gold"], "observation")
    assert execute(asset, first).status == execute(asset, second).status == "completed"
    assert first.requests[0].request_fingerprint == second.requests[0].request_fingerprint
    assert first.requests[2].request_fingerprint != second.requests[2].request_fingerprint
    from casefile.agent_runtime.prose_quality_diagnostic import build_diagnostic_request

    request = first.requests[2]
    payload = copy.deepcopy(request.input_payload)
    binding = {"profile_hash": "first"}
    original = build_diagnostic_request(
        kind="pairwise", payload=payload, binding=binding, api_key="fake"
    )
    changed = build_diagnostic_request(
        kind="pairwise", payload=payload, binding={"profile_hash": "second"}, api_key="fake"
    )
    assert original.request_fingerprint != changed.request_fingerprint
    payload["untrusted_data"]["a"]["render"]["blocks"][0]["text"] += "文字变化。"
    changed = build_diagnostic_request(
        kind="pairwise", payload=payload, binding=binding, api_key="fake"
    )
    assert original.request_fingerprint != changed.request_fingerprint


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "foreign", "repeat_evidence", "missing_evidence"]
)
def test_assessment_validation(package: dict[str, Any], fault: str) -> None:
    provider = ObservedProvider(package["tasks"][0]["asset"]["gold"])
    execute(package["tasks"][0]["asset"], provider)
    request = provider.requests[0]
    candidate = copy.deepcopy(
        DiagnosticFakeProvider(provider.gold).assess_quality(request).candidate
    )
    assert candidate is not None
    first = candidate["dimensions"][0]
    evidence = request.input_payload["server_evidence_catalog"][0]["evidence_id"]
    if fault == "missing":
        candidate["dimensions"].pop()
    elif fault == "duplicate":
        candidate["dimensions"][1] = copy.deepcopy(first)
    elif fault == "foreign":
        first["evidence_ids"] = ["not_in_this_catalog"]
    elif fault == "repeat_evidence":
        first["evidence_ids"] = [evidence, evidence]
    else:
        first["severity"] = "high"
    with pytest.raises(ProseQualityProtocolError):
        validate_single_assessment(candidate, request.input_payload["server_evidence_catalog"])


def test_complete_fake_has_fixed_denominators_budget_and_no_promotion(tmp_path: Path) -> None:
    report = benchmark.run_quality_diagnostic(attempt_id="test", output_dir=tmp_path / "attempt")
    assert report["status"] == "completed"
    assert len(report["rows"]) == 48
    for arm, calls in (("baseline", 48), ("candidate", 96)):
        summary = report["arms"][arm]
        assert summary["call_count"] == summary["transport_attempt_count"] == calls
        assert (
            summary["first_accuracy"]
            == summary["reverse_accuracy"]
            == summary["mirrored_consistency"]
            == {"passed": 24, "total": 24}
        )
        assert summary["first_dimension_accuracy"] == {"passed": 120, "total": 120}
        assert all(gate["passed"] for gate in summary["legacy_development_gates"])
    assert not report["comparison"]["worth_further_validation"]
    assert not report["qualified"]
    assert report["report_hash"] == canonical_json_sha256(
        {k: v for k, v in report.items() if k != "report_hash"}
    )
    with pytest.raises(FileExistsError):
        benchmark.run_quality_diagnostic(attempt_id="test", output_dir=tmp_path / "attempt")


@pytest.mark.parametrize("fault", ["always_a", "tie", "dimension", "assessment", "infrastructure"])
def test_diagnostic_fault_scoring_and_fixed_failure_denominators(
    tmp_path: Path, fault: str
) -> None:
    report = benchmark.run_quality_diagnostic(
        attempt_id=fault,
        output_dir=tmp_path / fault,
        fake_provider_factory=lambda task: ObservedProvider(task["asset"]["gold"], fault),
    )
    baseline, candidate = report["arms"]["baseline"], report["arms"]["candidate"]
    assert candidate["first_accuracy"]["total"] == 24
    assert candidate["first_dimension_accuracy"]["total"] == 120
    assert not report["comparison"]["worth_further_validation"]
    if fault == "always_a":
        assert candidate["mirrored_consistency"]["passed"] == 0
        assert candidate["first_accuracy"]["passed"] == 6
        assert candidate["reverse_accuracy"]["passed"] == 12
    elif fault == "tie":
        assert candidate["mirrored_consistency"]["passed"] == 24
        assert candidate["first_accuracy"]["passed"] == 6
    elif fault == "dimension":
        assert candidate["first_dimension_accuracy"]["passed"] < 120
    elif fault == "assessment":
        assert baseline["call_count"] == 48
        assert candidate["call_count"] == candidate["failure_counts"]["protocol"] == 24
    else:
        assert baseline["call_count"] == 2
        assert candidate["call_count"] == 0
        assert baseline["failure_counts"] == {"protocol": 0, "infrastructure": 1, "not_run": 23}
        assert candidate["failure_counts"]["not_run"] == 24


def test_comparison_requires_strict_improvement_and_both_position_nonloss(tmp_path: Path) -> None:
    report = benchmark.run_quality_diagnostic(
        attempt_id="criteria", output_dir=tmp_path / "criteria"
    )
    arms = report["arms"]
    arms["baseline"]["mirrored_consistency"]["passed"] = 20
    assert diagnostic_comparison(arms, complete=True, live=True)["worth_further_validation"]
    arms["candidate"]["reverse_dimension_accuracy"]["passed"] -= 1
    assert not diagnostic_comparison(arms, complete=True, live=True)["worth_further_validation"]


def test_live_preflight_and_experiment_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: dict[str, Any]
) -> None:
    live_root = tmp_path / "live"
    monkeypatch.setattr(benchmark, "LIVE_ROOT", live_root)
    source = {"revision": "abc", "clean": False}
    monkeypatch.setattr(benchmark, "quality_source_identity", lambda _: source.copy())
    with pytest.raises(benchmark.QualityDiagnosticError, match="clean_source_required"):
        benchmark.run_quality_diagnostic(attempt_id="dirty", mode="live", api_key="fake")
    assert not live_root.exists()
    source["clean"] = True
    monkeypatch.setattr(
        benchmark,
        "DeepSeekProseQualityCriticProvider",
        lambda: ObservedProvider(package["tasks"][0]["asset"]["gold"], "infrastructure"),
    )
    report = benchmark.run_quality_diagnostic(attempt_id="one", mode="live", api_key="fake")
    assert report["status"] == "inconclusive"
    with pytest.raises(FileExistsError):
        benchmark.run_quality_diagnostic(attempt_id="two", mode="live", api_key="fake")
    with pytest.raises(benchmark.QualityDiagnosticError, match="live_binding_invalid"):
        benchmark.run_quality_diagnostic(
            attempt_id="injection",
            mode="live",
            api_key="fake",
            fake_provider_factory=lambda task: DiagnosticFakeProvider(task["asset"]["gold"]),
        )


def test_descriptor_drift_fails_before_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = json.loads(benchmark.EXPERIMENT_PATH.read_text(encoding="utf-8"))
    descriptor["parameters"]["model_id"] = "other-model"
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    monkeypatch.setattr(benchmark, "EXPERIMENT_PATH", path)
    with pytest.raises(benchmark.QualityDiagnosticError, match="experiment_drift"):
        benchmark.run_quality_diagnostic(attempt_id="drift", output_dir=tmp_path / "attempt")
    assert not (tmp_path / "attempt").exists()


def test_deepseek_transport_uses_schema_and_fresh_two_message_calls(
    package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from casefile.agent_runtime import prose_quality_critic

    asset = package["tasks"][0]["asset"]
    observer = ObservedProvider(asset["gold"])
    assert execute(asset, observer).status == "completed"
    responses = [DiagnosticFakeProvider(asset["gold"]).assess_quality(r) for r in observer.requests]
    sent: list[dict[str, Any]] = []
    clients: list[dict[str, Any]] = []

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            clients.append(kwargs)
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs: Any) -> Any:
            sent.append(kwargs)
            candidate = responses[len(sent) - 1].candidate
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(candidate)))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(prose_quality_critic, "OpenAI", Client)
    result = execute_diagnostic_quality(
        DeepSeekProseQualityCriticProvider(),
        checklist=asset["checklist"],
        original_render=asset["render_a"],
        polished_render=asset["render_b"],
        original_consensus=asset["semantic_consensus_a"],
        preservation_consensus=asset["semantic_consensus_b"],
        profile=asset["profile"],
        api_key="fake",
    )
    assert result.status == "completed"
    assert len(clients) == len(sent) == 4
    for index, call in enumerate(sent):
        assert clients[index]["max_retries"] == 0
        assert call["model"] == "deepseek-v4-flash"
        assert call["temperature"] == 0 and call["max_tokens"] == 8192
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
        assert [message["role"] for message in call["messages"]] == ["system", "user"]
        assert (
            json.dumps(
                observer.requests[index].candidate_schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            in call["messages"][0]["content"]
        )


def test_parameter_drift_blocks_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(benchmark.quality_runtime, "PROSE_QUALITY_TEMPERATURE", 1)
    with pytest.raises(benchmark.QualityDiagnosticError, match="parameters_drift"):
        benchmark.run_quality_diagnostic(attempt_id="parameters", output_dir=tmp_path / "attempt")
    assert not (tmp_path / "attempt").exists()
