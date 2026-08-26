from __future__ import annotations

import json
from dataclasses import asdict

from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerRequest,
    GeneralMutationPlannerResult,
    MutationPlanV2,
)
from casefile.benchmark.general_mutation_backend_executor import (
    PostgresBackendReleaseExecutor,
)
from casefile.benchmark.general_mutation_backend_release import FAULT_MATRIX, ReleaseTask
from casefile.benchmark.general_mutation_safety_executor import _SafetyProvider
from sqlalchemy import Engine


class LedgerFakeProvider(_SafetyProvider):
    def __init__(self, document: dict[str, object]) -> None:
        super().__init__(document, live=FakeProvider())

    def plan_general_mutation(self, request: GeneralMutationPlannerRequest):  # type: ignore[no-untyped-def]
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
        if "删除" in request.message:
            result = GeneralMutationPlannerResult(
                MutationPlanV2.model_validate(
                    {
                        "operations": [
                            {
                                "operation_key": "delete_relationship",
                                "operation_type": "delete_object",
                                "target": {
                                    "ref_kind": "existing",
                                    "object_id": request.casefile["relationships"][0]["id"],
                                },
                                "reason": "集成测试删除影响确认。",
                            }
                        ]
                    }
                ),
                {},
            )
        else:
            result = self.live.plan_general_mutation(request)
        request.emit(
            "agent.model_call.completed",
            "general_mutation",
            {
                "component_id": "general_mutation_planner",
                "schema_id": "general-mutation-plan-v2",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": "a" * 64,
                "usage": result.usage,
            },
        )
        return result


def test_backend_executor_drives_http_worker_apply_undo_redo(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, _actor_id, _master_key = workflow_database
    executor = PostgresBackendReleaseExecutor(
        database_url=engine.url.render_as_string(hide_password=False),
        api_key="sk-test-not-sent",
        provider_factory=LedgerFakeProvider,
    )
    try:
        row = executor.execute_trial(
            ReleaseTask(
                task_id="integration-create",
                family="create",
                expectation="apply",
                fixture="fixtures/casefiles/restart_loop.casefile.json",
                message="创建一个名为新人物的人物实体。",
                oracle={
                    "acceptable_statuses": ["proposal_ready"],
                    "required_state": [
                        {
                            "collection": "entities",
                            "where": {"/name": "新人物", "/entity_type": "person"},
                            "count": 1,
                        }
                    ],
                    "forbidden_changes": [
                        "/resolution_specs",
                        "/constraints",
                        "/structure_locks",
                    ],
                },
            ),
            trial_index=1,
            model_id="deepseek-v4-pro",
        )
        failed = {
            key: value
            for key, value in asdict(row).items()
            if isinstance(value, bool) and not value
        }
        assert row.passed is True, f'{",".join(failed)}:{row.reason_code}'
        assert row.pending_before_approval is True
        assert row.apply_verified is True
        assert row.undo_verified is True
        assert row.redo_verified is True
        assert row.exact_model_observed is True
        assert row.no_auto_apply is True
        delete_row = executor.execute_trial(
            ReleaseTask(
                task_id="integration-delete",
                family="delete",
                expectation="apply",
                fixture="fixtures/casefiles/restart_loop.casefile.json",
                message=(
                    "请更新当前卷宗：仅删除“研究员维护备用系统”这条关系，"
                    "保留关系两端对象。"
                ),
                oracle={
                    "acceptable_statuses": ["proposal_ready"],
                    "required_state": [
                        {
                            "collection": "relationships",
                            "where": {"/id": "rel_researcher_controls_backup"},
                            "count": 0,
                        }
                    ],
                    "forbidden_changes": [
                        "/resolution_specs",
                        "/constraints",
                        "/structure_locks",
                    ],
                },
            ),
            trial_index=1,
            model_id="deepseek-v4-pro",
        )
        delete_failed = {
            key: value
            for key, value in asdict(delete_row).items()
            if isinstance(value, bool) and not value
        }
        assert delete_row.passed is True, f'{",".join(delete_failed)}:{delete_row.reason_code}'
        assert delete_row.delete_hash_gate_passed is True
        faults = {fault_id: executor.execute_fault(fault_id) for fault_id in FAULT_MATRIX}
        failed_faults = {
            key: value for key, value in faults.items() if value.get("passed") is not True
        }
        assert failed_faults == {}, json.dumps(failed_faults, ensure_ascii=False)
    finally:
        executor.close()
