"""Opt-in isolated FakeProvider API/Worker for desktop feedback browser QA."""

from __future__ import annotations

import os
import threading
import time

import uvicorn
from alembic import command
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _alembic_config,
    _prepare_task,
)
from casefile.agent_runtime.chat_preview import AnswerPreview
from casefile.agent_runtime.credentials import generate_master_key
from casefile.agent_runtime.goal.provider import GoalFinalizerRequest
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.api.app import create_app
from casefile.application.workflow_service import WorkflowService
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from test_goal_session_steering import _decision, _finish, _understanding


class BrowserFeedbackProvider(RichFixtureProvider, FakeProvider):
    def _preview(
        self, request: CaseFileChatRequest, result: CaseFileChatResult
    ) -> CaseFileChatResult:
        answer = (
            "我正在对照卷宗中已经记录的线索。人物的行动顺序需要与事件发生时间分别核对，不能把传闻直接当作事实。"
            * 15
        )
        result = result.__class__(
            candidate=result.candidate.model_copy(update={"answer": answer}),
            usage=result.usage,
            tools=result.tools,
        )
        if request.feedback:
            preview = AnswerPreview(request.feedback, sensitive_values=(request.api_key or "",))
            raw = result.candidate.model_dump_json()
            for offset in range(0, len(raw), 100):
                preview.feed(raw[offset : offset + 100])
                time.sleep(0.55)
            preview.finish()
        return result

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        return self._preview(request, super().chat(request))

    def finalize_goal(self, request: GoalFinalizerRequest) -> CaseFileChatResult:
        return self._preview(request.chat, super().finalize_goal(request))


def main() -> None:
    url = os.environ["CASEFILE_TEST_DATABASE_URL"]
    if make_url(url).database != "casefile_feedback_20260904_test":
        raise RuntimeError("Browser QA requires the explicitly isolated feedback test database")
    os.environ["DATABASE_URL"] = url
    os.environ["CASEFILE_MASTER_KEY"] = generate_master_key()
    os.environ["CASEFILE_CHAT_GOAL_ROLLOUT"] = "active"
    os.environ["CASEFILE_CHAT_GOAL_SESSION_ROLLOUT"] = "active"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with engine.begin() as connection:
        actor = connection.scalar(text("SELECT min(id) FROM users"))
        if actor is None:
            actor = connection.scalar(
                text("INSERT INTO users (display_name) VALUES ('Feedback QA') RETURNING id")
            )
        project = connection.scalar(text("SELECT min(id) FROM projects"))
    if actor != 1:
        raise RuntimeError("Use a freshly initialized isolated database for browser QA")
    generation = None
    if project is None:
        project, generation = _prepare_task(engine, actor)
    with factory() as session:
        WorkflowService(session).save_provider_setting(
            actor,
            provider="openai",
            api_key="sk-feedback-test-only",
            model_id="gpt-5.6-sol",
            model_is_custom=False,
        )
        WorkflowService(session).save_provider_setting(
            actor,
            provider="deepseek",
            api_key="sk-feedback-test-only",
            model_id="deepseek-v4-pro",
            model_is_custom=False,
        )
    worker = Worker(
        factory,
        config=WorkerConfig(worker_id="feedback-browser-qa"),
        provider_factory=lambda _task: BrowserFeedbackProvider(
            goal_understanding=_understanding(),
            goal_decisions=(_decision("analyze", "obl_1"), _decision("audit", "obl_2"), _finish()),
        ),
    )
    if generation is not None:
        worker.run_once()
        _adopt_candidate(engine, actor, project, generation)
    print(f"Feedback QA project={project}; actor={actor}; API=127.0.0.1:8011", flush=True)

    def loop() -> None:
        while True:
            if not worker.run_once():
                time.sleep(0.2)

    threading.Thread(target=loop, daemon=True).start()
    uvicorn.run(create_app(url), host="127.0.0.1", port=8011)


if __name__ == "__main__":
    main()
