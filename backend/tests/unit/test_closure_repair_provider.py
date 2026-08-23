from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from casefile.agent_runtime import (
    CLOSURE_REPAIR_PROMPT_VERSION,
    ClosureRepairOutputV1,
    ClosureRepairRequest,
    DeepSeekAgentsProvider,
    FakeProvider,
    OpenAIAgentsProvider,
    ProviderRepairProposer,
)
from casefile.agent_runtime.closure_repair_prompt import render_closure_repair_prompt
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError
from casefile.agent_runtime.structured_output import validate_model_json
from casefile.contracts import ContractValidationError
from casefile.domain.logical_mutation import CLOSURE_POLICY_V2, MutationSet, UpdateField
from casefile.domain.logical_mutation.repair.engine import run_closure_repair
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairContextV1,
    RepairContextObject,
    RepairObjectPaths,
    ScopedRepairObligation,
)
from casefile.domain.verification_engine import VerificationEngine

ROOT = Path(__file__).resolve().parents[3]


def _context() -> ClosureRepairContextV1:
    paths = RepairObjectPaths("claim_subject", ("/status",))
    obligation = ScopedRepairObligation(
        obligation_key="obligation-1",
        source_finding_key="finding-1",
        rule_code="claim_dependency_incompatible",
        subject_object_ids=("claim_subject",),
        effective_repair_kinds=("update_claim_status",),
        allowed_paths=(paths,),
    )
    return ClosureRepairContextV1(
        context_version="closure-repair-context-v1",
        scope_version="closure-repair-scope-v1",
        closure_policy_version="logical-mutation-v2",
        repair_policy_version="closure-repair-policy-v1",
        base_draft_id=7,
        base_revision=11,
        baseline_hash="b" * 64,
        candidate_hash="c" * 64,
        original_intent="调整前置主张",
        primary_operations=(),
        obligations=(obligation,),
        objects=(
            RepairContextObject(
                object_id="claim_subject",
                object_type="claim",
                access="read_write",
                object_value={"id": "claim_subject", "status": "supported"},
            ),
        ),
        allowed_paths=(paths,),
        protected_paths=(),
        structure_lock_ids=(),
        dependency_paths=(),
        relevant_edges=(),
        max_operations=8,
        max_context_objects=24,
        max_write_objects=6,
        context_hash="a" * 64,
    )


def _events() -> tuple[list[tuple[str, str, dict[str, Any]]], Any]:
    events: list[tuple[str, str, dict[str, Any]]] = []

    def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
        events.append((event_type, stage, payload))

    return events, emit


def _request(*, api_key: str | None = "test-key") -> ClosureRepairRequest:
    _recorded, emit = _events()
    return ClosureRepairRequest(
        prompt_version=CLOSURE_REPAIR_PROMPT_VERSION,
        context=_context().as_dict(),
        round_no=1,
        model_id="test-model",
        api_key=api_key,
        max_turns=1,
        emit=emit,
    )


def _candidate() -> dict[str, Any]:
    return {
        "operations": [
            {
                "obligation_keys": ["obligation-1"],
                "object_id": "claim_subject",
                "field_path": "/status",
                "value_json": '"unresolved"',
                "reason": "解除不相容依赖造成的状态冲突。",
            }
        ]
    }


def test_closure_repair_prompt_package_binds_frozen_contracts() -> None:
    rendered = render_closure_repair_prompt(_request())

    assert rendered.package_version == "closure-repair-v1"
    assert rendered.component_id == "repair"
    assert rendered.input_contract_id == "closure-repair-input-v1"
    assert rendered.output_schema_id == "closure-repair-output-v1"
    assert rendered.tool_policy_id == "closure-repair-no-tools-v1"
    assert "不得声明闭包成功" in rendered.instructions
    assert '"round_no":1' in rendered.input_text


def test_closure_repair_output_is_strict_and_value_json_is_reparsed() -> None:
    with pytest.raises(ValidationError):
        ClosureRepairOutputV1.model_validate(
            {**_candidate(), "closure_succeeded": True}
        )
    with pytest.raises(ContractValidationError):
        validate_model_json(
            ClosureRepairOutputV1,
            json.dumps({**_candidate(), "closure_succeeded": True}),
            discard_forbidden_fields=False,
        )

    events, emit = _events()
    proposer = ProviderRepairProposer(
        provider=FakeProvider(),
        model_id="fake",
        api_key=None,
        emit=emit,
    )
    proposal = proposer.propose(_context(), round_no=1)

    assert proposal.context_hash == "a" * 64
    assert proposal.operations[0].new_value == "unresolved"
    assert proposer.results[0].usage["requests"] == 0
    completed = next(item for item in events if item[0] == "agent.model_call.completed")
    assert completed[1] == "closure_repair"
    assert completed[2]["schema_id"] == "closure-repair-output-v1"
    assert completed[2]["_raw_output"]
    assert completed[2]["raw_output_truncated"] is False


def test_provider_repair_proposer_rejects_invalid_value_json() -> None:
    class InvalidJsonProvider(FakeProvider):
        def repair_closure(self, request: ClosureRepairRequest):  # type: ignore[no-untyped-def]
            result = super().repair_closure(request)
            invalid = result.candidate.model_copy(deep=True)
            invalid.operations[0].value_json = "not-json"
            return type(result)(candidate=invalid, usage=result.usage)

    _events_list, emit = _events()
    proposer = ProviderRepairProposer(
        provider=InvalidJsonProvider(),
        model_id="fake",
        api_key=None,
        emit=emit,
    )

    with pytest.raises(ValueError, match="repair_proposal_value_json_invalid"):
        proposer.propose(_context(), round_no=1)


def _provider_repair_case(rule: str) -> tuple[dict[str, Any], MutationSet]:
    document = json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    if rule != "claim_dependency_incompatible":
        supported = rule == "claim_supported_without_support"
        suffix = "support" if supported else "refutation"
        claim_field = "support_refs" if supported else "refute_refs"
        information_field = (
            "supports_claim_refs" if supported else "refutes_claim_refs"
        )
        information = deepcopy(document["information_units"][0])
        information.update(
            id=f"info_provider_{suffix}",
            supports_claim_refs=[],
            refutes_claim_refs=[],
        )
        claim = deepcopy(document["claims"][0])
        claim.update(
            id=f"claim_provider_{suffix}",
            support_refs=[],
            refute_refs=[],
            dependency_claim_refs=[],
            status="supported" if supported else "refuted",
            materiality="minor",
        )
        information[information_field] = [
            {"object_type": "claim", "object_id": claim["id"]}
        ]
        claim[claim_field] = [
            {"object_type": "information_unit", "object_id": information["id"]}
        ]
        document["information_units"].append(information)
        document["claims"].append(claim)
        return document, MutationSet(
            f"provider-{suffix}-test",
            7,
            11,
            (
                UpdateField(
                    f"remove-{suffix}",
                    claim["id"],
                    f"/{claim_field}",
                    [],
                ),
            ),
            actor="agent",
            closure_policy_version=CLOSURE_POLICY_V2,
        )

    template = document["claims"][0]
    prerequisite = deepcopy(template)
    prerequisite.update(
        id="claim_provider_prerequisite",
        dependency_claim_refs=[],
    )
    subject = deepcopy(template)
    subject.update(
        id="claim_provider_subject",
        dependency_claim_refs=[
            {"object_type": "claim", "object_id": prerequisite["id"]}
        ],
    )
    document["claims"].extend((prerequisite, subject))
    document["information_units"][0]["supports_claim_refs"].extend(
        (
            {"object_type": "claim", "object_id": prerequisite["id"]},
            {"object_type": "claim", "object_id": subject["id"]},
        )
    )
    return document, MutationSet(
        "provider-repair-test",
        7,
        11,
        (
            UpdateField(
                "primary-status",
                prerequisite["id"],
                "/status",
                "unresolved",
            ),
        ),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V2,
    )


@pytest.mark.parametrize(
    "rule",
    (
        "claim_supported_without_support",
        "claim_refuted_without_refutation",
        "claim_dependency_incompatible",
    ),
)
def test_fake_provider_proposal_still_passes_full_engine_proof(rule: str) -> None:
    document, mutation = _provider_repair_case(rule)
    simulation = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, mutation)
    _events_list, emit = _events()

    result = run_closure_repair(
        document,
        mutation,
        simulation,
        ProviderRepairProposer(
            provider=FakeProvider(),
            model_id="fake",
            api_key=None,
            emit=emit,
        ),
        original_intent="修改前置主张",
    )

    assert result.status == "repaired"
    assert result.final_simulation is not None
    assert result.final_simulation.can_apply is True


@pytest.mark.parametrize(
    ("provider_type", "expected_protocol"),
    (
        (OpenAIAgentsProvider, None),
        (DeepSeekAgentsProvider, "strict_tool"),
    ),
)
def test_live_adapters_use_dedicated_strict_schema_and_normalized_usage(
    monkeypatch: pytest.MonkeyPatch,
    provider_type: type[OpenAIAgentsProvider] | type[DeepSeekAgentsProvider],
    expected_protocol: str | None,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_auxiliary(
        _self: Any,
        _request: ClosureRepairRequest,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        captured.update(kwargs)
        return _candidate(), {
            "requests": 1,
            "input_tokens": 21,
            "output_tokens": 9,
            "total_tokens": 30,
            "cached_tokens": 3,
            "reasoning_tokens": 0,
        }

    monkeypatch.setattr(provider_type, "_run_auxiliary", fake_run_auxiliary)

    result = provider_type().repair_closure(_request())

    assert result.candidate.operations[0].field_path == "/status"
    assert result.usage["total_tokens"] == 30
    assert captured["output_type"] is ClosureRepairOutputV1
    assert captured["component_id"] == "closure_repair_round_1"
    assert captured["schema_id"] == "closure-repair-output-v1"
    assert captured["strict_validation"] is True
    assert captured.get("deepseek_output_protocol") == expected_protocol


@pytest.mark.parametrize("provider", (OpenAIAgentsProvider(), DeepSeekAgentsProvider()))
def test_live_adapters_fail_without_credentials(provider: Any) -> None:
    with pytest.raises(ProviderProtocolError, match="API key is required"):
        provider.repair_closure(_request(api_key=None))


@pytest.mark.parametrize("provider_type", (OpenAIAgentsProvider, DeepSeekAgentsProvider))
def test_live_adapters_propagate_network_failure(
    monkeypatch: pytest.MonkeyPatch,
    provider_type: type[OpenAIAgentsProvider] | type[DeepSeekAgentsProvider],
) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("network unavailable")

    monkeypatch.setattr(provider_type, "_run_auxiliary", fail)

    with pytest.raises(OSError, match="network unavailable"):
        provider_type().repair_closure(_request())
