from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from casefile.agent_runtime import FakeProvider
from casefile.benchmark.chat_public_language_executor import (
    PostgresPublicLanguageExecutor,
    _EphemeralCredentialProvider,
)
from casefile.benchmark.chat_public_language_qualification import (
    MODEL_ID,
    PROMPT_VERSION,
    load_public_language_suite,
)

ROOT = Path(__file__).resolve().parents[3]


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
    assert row.exact_prompt_observed is True
    assert row.infrastructure_failure is None
