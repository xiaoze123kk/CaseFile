"""Versioned linear Exposure Plan application service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError, not_found
from casefile.data_postgres.exposure_repository import (
    ExposureEntryWrite,
    ExposureObligationWrite,
    ExposurePlanRepository,
)
from casefile.data_postgres.models import AuditEvent, ExposurePlan
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository


def _plan_view(
    repository: ExposurePlanRepository,
    owned: OwnedDraft,
    plan: ExposurePlan,
) -> dict[str, Any]:
    return {
        "plan_id": plan.id,
        "draft_id": owned.draft.id,
        "revision": plan.revision,
        "updated_at": plan.updated_at.isoformat(),
        "entries": [
            {
                "entry_key": item.entry.entry_key,
                "sequence_no": item.entry.sequence_no,
                "title": item.entry.title,
                "note": item.entry.note,
                "refs": [
                    {
                        "object_type": registry.object_type,
                        "object_id": registry.object_id,
                    }
                    for registry in item.objects
                ],
                "planning_obligations": [
                    {
                        "kind": obligation.obligation.obligation_kind,
                        "obligation_key": obligation.obligation.obligation_key,
                        "level": obligation.obligation.level,
                        **(
                            {"min_distinct": obligation.obligation.min_distinct}
                            if obligation.obligation.obligation_kind
                            == "participant_coverage"
                            else {}
                        ),
                        (
                            "eligible_refs"
                            if obligation.obligation.obligation_kind
                            == "participant_coverage"
                            else "required_refs"
                        ): [
                            {
                                "object_type": registry.object_type,
                                "object_id": registry.object_id,
                            }
                            for registry in obligation.objects
                        ],
                    }
                    for obligation in item.obligations
                ],
            }
            for item in repository.read_current_entries(plan)
        ],
    }


class ExposurePlanService:
    """Read and revise one linear plan without mutating Draft or Canon state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.plans = ExposurePlanRepository(session)

    def get(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            plan = self.plans.get_for_draft(owned)
            if plan is None:
                plan = self.plans.create_blank(owned, actor_user_id)
            return _plan_view(self.plans, owned, plan)

    def put(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_draft_id: int,
        expected_revision: int,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            if owned.draft.id != expected_draft_id:
                raise ApplicationError(
                    "current_draft_changed",
                    "当前工作稿已在其他位置切换，请刷新后重试。",
                    status_code=409,
                    details={"current_draft_id": owned.draft.id},
                )
            plan = self.plans.get_for_draft(owned, lock=True)
            if plan is None:
                plan = self.plans.create_blank(owned, actor_user_id)
            if plan.revision != expected_revision:
                raise ApplicationError(
                    "exposure_plan_revision_conflict",
                    "披露计划已被更新，请刷新后重新排序。",
                    status_code=409,
                    details={
                        "current_revision": plan.revision,
                        "received_revision": expected_revision,
                    },
                )

            requested_ids = {
                reference["object_id"]
                for entry in entries
                for reference in (
                    entry["refs"]
                    + [
                        ref
                        for obligation in entry["planning_obligations"]
                        for ref in (
                            obligation.get("eligible_refs")
                            or obligation.get("required_refs")
                            or []
                        )
                    ]
                )
            }
            registries = self.plans.registries_by_object_id(owned, requested_ids)
            if set(registries) != requested_ids:
                missing = sorted(requested_ids - set(registries))
                raise ApplicationError(
                    "exposure_plan_invalid_reference",
                    "披露计划引用了当前工作稿中不存在的对象。",
                    status_code=422,
                    details={"object_ids": missing},
                )

            writes: list[ExposureEntryWrite] = []
            for entry in entries:
                title = str(entry["title"]).strip()
                if not title:
                    raise ApplicationError(
                        "exposure_plan_title_blank",
                        "披露节点标题不能为空。",
                        status_code=422,
                    )
                registry_ids: list[int] = []
                for reference in entry["refs"]:
                    registry = registries[reference["object_id"]]
                    if registry.object_type != reference["object_type"]:
                        raise ApplicationError(
                            "exposure_plan_reference_type_mismatch",
                            "披露计划引用类型与当前工作稿对象不一致。",
                            status_code=422,
                            details={"object_id": registry.object_id},
                        )
                    registry_ids.append(registry.id)
                obligation_writes: list[ExposureObligationWrite] = []
                for obligation in entry["planning_obligations"]:
                    refs = (
                        obligation.get("eligible_refs")
                        or obligation.get("required_refs")
                        or []
                    )
                    obligation_registry_ids: list[int] = []
                    for reference in refs:
                        registry = registries[reference["object_id"]]
                        if registry.object_type != reference["object_type"]:
                            raise ApplicationError(
                                "exposure_plan_reference_type_mismatch",
                                "披露计划语义义务的引用类型与当前工作稿对象不一致。",
                                status_code=422,
                                details={"object_id": registry.object_id},
                            )
                        if (
                            obligation["kind"] == "hypothesis_coverage"
                            and registry.object_type != "hypothesis"
                        ):
                            raise ApplicationError(
                                "exposure_plan_hypothesis_reference_invalid",
                                "假设覆盖义务只能引用 Hypothesis。",
                                status_code=422,
                                details={"object_id": registry.object_id},
                            )
                        obligation_registry_ids.append(registry.id)
                    obligation_writes.append(
                        ExposureObligationWrite(
                            obligation_key=obligation["obligation_key"],
                            obligation_kind=obligation["kind"],
                            level=obligation["level"],
                            min_distinct=obligation.get("min_distinct"),
                            object_registry_ids=tuple(obligation_registry_ids),
                        )
                    )
                writes.append(
                    ExposureEntryWrite(
                        entry_key=entry["entry_key"],
                        title=title,
                        note=entry["note"],
                        object_registry_ids=tuple(registry_ids),
                        obligations=tuple(obligation_writes),
                    )
                )

            previous_revision = plan.revision
            self.plans.append_revision(owned, plan, actor_user_id, writes)
            self.session.add(
                AuditEvent(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    actor_kind="user",
                    actor_user_id=actor_user_id,
                    actor_ref=None,
                    action="exposure_plan.revised",
                    target_type="exposure_plan",
                    target_id=plan.id,
                    trace_id=None,
                    details_jsonb={
                        "draft_id": owned.draft.id,
                        "previous_revision": previous_revision,
                        "revision": plan.revision,
                        "entry_count": len(writes),
                    },
                )
            )
            self.session.flush()
            self.session.refresh(plan)
            return _plan_view(self.plans, owned, plan)


__all__ = ["ExposurePlanService"]
