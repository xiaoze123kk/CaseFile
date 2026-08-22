"""Stable transactional façade for Workflow application use cases.

Owns dependency construction and the public ``WorkflowService(session)`` API.
Does not own validation, projections, event serialization, or individual use-
case implementations; those live in the delegated workflow modules.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from casefile.application.task_events import append_task_event
from casefile.application.workflow.agent import AgentWorkflowMixin
from casefile.application.workflow.content import ContentWorkflowMixin
from casefile.application.workflow_common import (
    DEFAULT_BUDGET,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
)
from casefile.application.workflow_views import (
    event_view,
    source_view,
    task_failure_view,
    task_view,
)
from casefile.data_postgres.repositories import ProjectRepository


class WorkflowService(ContentWorkflowMixin, AgentWorkflowMixin):
    """Transactional facade for the user-visible Agent generation workflow."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)


__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "SUPPORTED_PROVIDERS",
    "WorkflowService",
    "append_task_event",
    "event_view",
    "source_view",
    "task_failure_view",
    "task_view",
]
