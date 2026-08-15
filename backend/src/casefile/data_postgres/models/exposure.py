"""Versioned linear Exposure Plan models independent from Draft content."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


class ExposurePlan(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The single mutable revision pointer for one Draft's disclosure plan."""

    __tablename__ = "exposure_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_exposure_plans_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_id",
                "casefile_id",
                "draft_id",
                "id",
                "current_revision_id",
            ],
            [
                "exposure_plan_revisions.project_id",
                "exposure_plan_revisions.casefile_id",
                "exposure_plan_revisions.draft_id",
                "exposure_plan_revisions.plan_id",
                "exposure_plan_revisions.id",
            ],
            name="fk_exposure_plans_lineage_current_revision_revisions",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("draft_id", name="uq_exposure_plans_draft_id"),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plans_lineage_id",
        ),
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint(
            "(revision = 0 AND current_revision_id IS NULL) OR "
            "(revision >= 1 AND current_revision_id IS NOT NULL)",
            name="revision_pointer_consistent",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    current_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )


class ExposurePlanRevision(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable complete revision of a linear Exposure Plan."""

    __tablename__ = "exposure_plan_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "plan_id"],
            [
                "exposure_plans.project_id",
                "exposure_plans.casefile_id",
                "exposure_plans.draft_id",
                "exposure_plans.id",
            ],
            name="fk_exposure_plan_revisions_lineage_plan_exposure_plans",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "plan_id",
            "revision_no",
            name="uq_exposure_plan_revisions_plan_id_revision_no",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plan_revisions_lineage_id",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "plan_id",
            "id",
            name="uq_exposure_plan_revisions_plan_lineage_id",
        ),
        CheckConstraint("revision_no >= 1", name="revision_no_positive"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ExposurePlanEntry(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable position in an Exposure Plan revision."""

    __tablename__ = "exposure_plan_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "plan_revision_id"],
            [
                "exposure_plan_revisions.project_id",
                "exposure_plan_revisions.casefile_id",
                "exposure_plan_revisions.draft_id",
                "exposure_plan_revisions.id",
            ],
            name="fk_exposure_plan_entries_lineage_revision_revisions",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "plan_revision_id",
            "sequence_no",
            name="uq_exposure_plan_entries_revision_sequence_no",
        ),
        UniqueConstraint(
            "plan_revision_id",
            "entry_key",
            name="uq_exposure_plan_entries_revision_entry_key",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plan_entries_lineage_id",
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_no_positive"),
        CheckConstraint(
            "entry_key ~ '^exposure_[a-z0-9][a-z0-9_]{0,150}$'",
            name="entry_key_format",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_key: Mapped[str] = mapped_column(String(160), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class ExposurePlanEntryRef(BigIntIdentityPrimaryKeyMixin, Base):
    """One ordered stable CaseFile object reference from an Exposure Plan entry."""

    __tablename__ = "exposure_plan_entry_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entry_id"],
            [
                "exposure_plan_entries.project_id",
                "exposure_plan_entries.casefile_id",
                "exposure_plan_entries.draft_id",
                "exposure_plan_entries.id",
            ],
            name="fk_exposure_plan_entry_refs_lineage_entry_entries",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "object_registry_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_exposure_plan_entry_refs_lineage_object_casefile_objects",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "entry_id",
            "ordinal",
            name="uq_exposure_plan_entry_refs_entry_id_ordinal",
        ),
        UniqueConstraint(
            "entry_id",
            "object_registry_id",
            name="uq_exposure_plan_entry_refs_entry_object_registry",
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


__all__ = [
    "ExposurePlan",
    "ExposurePlanEntry",
    "ExposurePlanEntryRef",
    "ExposurePlanRevision",
]
