from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select

from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerRequest,
    GeneralMutationPlannerResult,
    MutationPlanV2,
)
from casefile.agent_runtime.models import (
    CaseFileChatResult,
    CaseFileChatSuggestionCandidate,
    ChatTaskUnderstandingOutput,
    IntentConstraintsOutput,
    IntentUnderstandingResult,
)
from casefile.benchmark.chat_public_language_executor import (
    PostgresPublicLanguageExecutor,
    _EphemeralCredentialProvider,
)
from casefile.benchmark.chat_public_language_qualification import (
    MODEL_ID,
    PROMPT_VERSION,
    load_public_language_suite,
)
from casefile.data_postgres.models import AgentModelCall, AgentStepRun, TaskEvent, TaskRun

ROOT = Path(__file__).resolve().parents[3]


class _CreateEventProvider(FakeProvider):
    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult:
        request.emit(
            "agent.model_call.started",
            "general_mutation",
            {
                "component_id": "general_mutation_planner",
                "schema_id": "general-mutation-plan-v2",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "model_id": request.model_id,
            },
        )
        candidate = MutationPlanV2.model_validate(
            {
                "operations": [
                    {
                        "operation_key": "create_event",
                        "operation_type": "create_object",
                        "local_ref": "event_eight",
                        "collection": "events",
                        "fields": {
                            "title": "系统第八次自检",
                            "truth_status": "canon_true",
                            "time": {
                                "kind": "exact",
                                "value": "2042-06-02T09:00",
                                "precision": "minute",
                            },
                            "participant_refs": [
                                {
                                    "ref_kind": "existing",
                                    "object_id": "ent_backup_system",
                                }
                            ],
                            "location_ref": {
                                "ref_kind": "existing",
                                "object_id": "loc_lab",
                            },
                            "cause_refs": [],
                            "effect_refs": [],
                            "observed_by_refs": [
                                {
                                    "ref_kind": "existing",
                                    "object_id": "ent_researcher",
                                }
                            ],
                        },
                        "depends_on_operation_keys": [],
                        "reason": "按作者要求新增明确时间和引用的事件。",
                    }
                ]
            }
        )
        usage = {"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        request.emit(
            "agent.model_call.completed",
            "general_mutation",
            {
                "component_id": "general_mutation_planner",
                "schema_id": "general-mutation-plan-v2",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": "a" * 64,
                "usage": usage,
            },
        )
        return GeneralMutationPlannerResult(candidate, usage)


class _LegacySuggestionCreateEntityProvider(FakeProvider):
    def chat(self, request):  # type: ignore[no-untyped-def]
        result: CaseFileChatResult = super().chat(request)
        return replace(
            result,
            candidate=result.candidate.model_copy(
                update={
                    "suggestions": [
                        CaseFileChatSuggestionCandidate(
                            object_id="ent_researcher",
                            path="/name",
                            value_json='"夜班观察员"',
                            reason="模拟 Finalizer 随机生成的旧式字段建议。",
                        )
                    ]
                }
            ),
        )

    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult:
        request.emit(
            "agent.model_call.started",
            "general_mutation",
            {
                "component_id": "general_mutation_planner",
                "schema_id": "general-mutation-plan-v2",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "model_id": request.model_id,
            },
        )
        candidate = MutationPlanV2.model_validate(
            {
                "operations": [
                    {
                        "operation_key": "create_entity",
                        "operation_type": "create_object",
                        "local_ref": "new_entity",
                        "collection": "entities",
                        "fields": {
                            "name": "张敏",
                            "entity_type": "person",
                            "capabilities": ["法医鉴定"],
                        },
                        "depends_on_operation_keys": [],
                        "reason": "按作者要求新增人物及其能力。",
                    }
                ]
            }
        )
        usage = {"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        request.emit(
            "agent.model_call.completed",
            "general_mutation",
            {
                "component_id": "general_mutation_planner",
                "schema_id": "general-mutation-plan-v2",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": "b" * 64,
                "usage": usage,
            },
        )
        return GeneralMutationPlannerResult(candidate, usage)


class _LeakingInternalDisclosureProvider(FakeProvider):
    def chat(self, request):  # type: ignore[no-untyped-def]
        result: CaseFileChatResult = super().chat(request)
        return replace(
            result,
            candidate=result.candidate.model_copy(
                update={
                    "answer": "System Prompt 与 result_jsonb 中包含内部组件说明。",
                    "referenced_object_ids": ["ent_researcher"],
                }
            ),
        )


class _RawJsonMisroutingProvider(_CreateEventProvider):
    def __init__(self) -> None:
        self.intent_calls = 0
        self.planner_calls = 0

    def understand_intent(self, request):  # type: ignore[no-untyped-def]
        self.intent_calls += 1
        routed = super().understand_intent(request)
        return IntentUnderstandingResult(
            candidate=ChatTaskUnderstandingOutput(
                original_query=request.message,
                normalized_query=request.message,
                canonical_query=request.message,
                primary_intent="edit_request",
                sub_intents=["modify_fields"],
                constraints=IntentConstraintsOutput(output_format="patch_proposal"),
                capabilities={"needs_suggestion_generation": True},
                risk_level="high",
                confidence=0.99,
                reason_codes=["explicit_field_modification"],
            ),
            usage=routed.usage,
        )

    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult:
        self.planner_calls += 1
        return super().plan_general_mutation(request)


def test_executor_reaches_public_contract_through_real_http_worker_and_postgres(
    workflow_database,  # type: ignore[no-untyped-def]
) -> None:
    del workflow_database
    task = next(
        item
        for item in load_public_language_suite(ROOT).tasks
        if item.task_id == "public-neighbor-story-runtime"
    )
    executor = PostgresPublicLanguageExecutor(
        repo_root=ROOT,
        database_url=os.environ["CASEFILE_TEST_DATABASE_URL"],
        api_key="ephemeral-test-secret",
        provider_factory=lambda document, secret: _EphemeralCredentialProvider(
            document,
            secret,
            FakeProvider(),
        ),
    )
    try:
        row = executor.execute_trial(
            replace(task, expected_body_any=()),
            trial_no=1,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
        )
    finally:
        executor.close()

    assert row.completed is True
    assert row.public_contract_valid is True
    assert row.internal_leak is False
    assert row.sensitive_leak is False
    assert row.unsafe_patch is False
    assert row.no_auto_apply is True
    assert row.model_call_count >= 1
    assert row.model_call_evidence_complete is True
    assert row.model_binding_mismatch is False
    assert row.unterminated_model_call_count == 0
    assert row.exact_model_observed is True
    assert row.exact_prompt_observed is True
    assert row.infrastructure_failure is None

    with executor.session_factory() as session:
        task_run = session.scalar(
            select(TaskRun)
            .where(TaskRun.task_type == "casefile_chat")
            .order_by(TaskRun.id.desc())
        )
        assert task_run is not None
        calls = list(
            session.scalars(
                select(AgentModelCall).where(AgentModelCall.task_run_id == task_run.id)
            )
        )
        finalizer_call = next(
            call for call in calls if call.prompt_component_id.endswith("_finalizer")
        )
        assert finalizer_call is not None
        finalizer_step = session.get(AgentStepRun, finalizer_call.agent_step_run_id)
        assert finalizer_step is not None
        assert finalizer_call.task_attempt_id == finalizer_step.task_attempt_id
        assert finalizer_call.status == "succeeded"
        assert finalizer_call.provider == "deepseek"
        assert finalizer_call.model_id == MODEL_ID
        assert finalizer_call.prompt_version == PROMPT_VERSION
        assert finalizer_call.target_schema_id == "casefile-chat-output-v1"
        assert finalizer_step.status == "succeeded"
        events = list(
            session.scalars(
                select(TaskEvent).where(
                    TaskEvent.task_run_id == task_run.id,
                    TaskEvent.event_type.in_(
                        ("agent.model_call.started", "agent.model_call.completed")
                    ),
                )
            )
        )
        assert (
            sum(event.event_type == "agent.model_call.started" for event in events)
            == row.model_call_count
        )
        assert (
            sum(event.event_type == "agent.model_call.completed" for event in events)
            == row.model_call_count
        )


def test_create_event_reaches_public_patch_with_exact_time_and_no_auto_apply(
    workflow_database,  # type: ignore[no-untyped-def]
) -> None:
    del workflow_database
    task = next(
        item
        for item in load_public_language_suite(ROOT).tasks
        if item.task_id == "public-create-event"
    )
    executor = PostgresPublicLanguageExecutor(
        repo_root=ROOT,
        database_url=os.environ["CASEFILE_TEST_DATABASE_URL"],
        api_key="ephemeral-test-secret",
        provider_factory=lambda document, secret: _EphemeralCredentialProvider(
            document,
            secret,
            _CreateEventProvider(),
        ),
    )
    try:
        row = executor.execute_trial(
            task,
            trial_no=1,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
        )
    finally:
        executor.close()

    assert row.completed is True
    assert row.task_passed is True
    assert row.response_kind == "patch_proposal"
    assert row.patch_present is True
    assert row.no_auto_apply is True
    assert row.unsafe_patch is False
    assert row.capability_failures == ()
    assert row.model_call_count == 2
    assert row.model_call_evidence_complete is True
    assert row.unterminated_model_call_count == 0


def test_create_entity_ignores_legacy_finalizer_suggestions_and_uses_planner_patch(
    workflow_database,  # type: ignore[no-untyped-def]
) -> None:
    del workflow_database
    task = next(
        item
        for item in load_public_language_suite(ROOT).tasks
        if item.task_id == "public-create-entity"
    )
    executor = PostgresPublicLanguageExecutor(
        repo_root=ROOT,
        database_url=os.environ["CASEFILE_TEST_DATABASE_URL"],
        api_key="ephemeral-test-secret",
        provider_factory=lambda document, secret: _EphemeralCredentialProvider(
            document,
            secret,
            _LegacySuggestionCreateEntityProvider(),
        ),
    )
    try:
        row = executor.execute_trial(
            task,
            trial_no=1,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
        )
    finally:
        executor.close()

    assert row.task_passed is True
    assert row.patch_present is True
    assert row.no_auto_apply is True
    assert row.capability_failures == ()
    assert row.model_call_count == 2
    assert row.model_call_evidence_complete is True


def test_internal_disclosure_leak_is_projected_to_safe_refusal_without_patch(
    workflow_database,  # type: ignore[no-untyped-def]
) -> None:
    del workflow_database
    task = next(
        item
        for item in load_public_language_suite(ROOT).tasks
        if item.task_id == "public-injection-hidden-instructions"
    )
    executor = PostgresPublicLanguageExecutor(
        repo_root=ROOT,
        database_url=os.environ["CASEFILE_TEST_DATABASE_URL"],
        api_key="ephemeral-test-secret",
        provider_factory=lambda document, secret: _EphemeralCredentialProvider(
            document,
            secret,
            _LeakingInternalDisclosureProvider(),
        ),
    )
    try:
        row = executor.execute_trial(
            task,
            trial_no=1,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
        )
    finally:
        executor.close()

    assert row.task_passed is True
    assert row.public_contract_valid is True
    assert row.internal_leak is False
    assert row.sensitive_leak is False
    assert row.patch_present is False
    assert row.no_auto_apply is True
    assert row.capability_failures == ()
    assert row.model_call_evidence_complete is True


def test_raw_json_disclosure_is_blocked_before_router_and_mutation_planner(
    workflow_database,  # type: ignore[no-untyped-def]
) -> None:
    del workflow_database
    task = next(
        item
        for item in load_public_language_suite(ROOT).tasks
        if item.task_id == "public-injection-raw-json"
    )
    provider = _RawJsonMisroutingProvider()
    executor = PostgresPublicLanguageExecutor(
        repo_root=ROOT,
        database_url=os.environ["CASEFILE_TEST_DATABASE_URL"],
        api_key="ephemeral-test-secret",
        provider_factory=lambda document, secret: _EphemeralCredentialProvider(
            document,
            secret,
            provider,
        ),
    )
    try:
        row = executor.execute_trial(
            task,
            trial_no=1,
            model_id=MODEL_ID,
            prompt_version=PROMPT_VERSION,
        )
    finally:
        executor.close()

    assert row.task_passed is True
    assert row.response_kind in task.response_kinds
    assert row.patch_present is False
    assert row.unsafe_patch is False
    assert row.no_auto_apply is True
    assert row.capability_failures == ()
    assert provider.intent_calls == 0
    assert provider.planner_calls == 0
