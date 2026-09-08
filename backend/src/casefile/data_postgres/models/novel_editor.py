"""Novel manuscripts are independent of compiler artifacts and CaseFile drafts."""

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

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin


class NovelManuscriptRecord(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_manuscripts"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("project_id", "draft_id", "source_key"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("projects.id"))
    draft_id: Mapped[int] = mapped_column(BigInteger)
    casefile_id: Mapped[int] = mapped_column(BigInteger)
    source_key: Mapped[str] = mapped_column(String(160))
    source_label: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(BigInteger)


class NovelVersion(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("manuscript_id", "revision"),
        UniqueConstraint("project_id", "manuscript_id", "revision"),
        ForeignKeyConstraint(
            ["project_id", "manuscript_id"],
            ["novel_manuscripts.project_id", "novel_manuscripts.id"],
        ),
    )
    project_id: Mapped[int] = mapped_column(BigInteger)
    manuscript_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class NovelChapterRecord(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_chapters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "version_id"], ["novel_versions.project_id", "novel_versions.id"]
        ),
        UniqueConstraint("version_id", "chapter_key"),
        UniqueConstraint("version_id", "ordinal"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger)
    version_id: Mapped[int] = mapped_column(BigInteger)
    chapter_key: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)


class NovelExchange(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_exchanges"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("manuscript_id", "request_key"),
        UniqueConstraint("task_id"),
        ForeignKeyConstraint(
            ["project_id", "manuscript_id", "revision"],
            [
                "novel_versions.project_id",
                "novel_versions.manuscript_id",
                "novel_versions.revision",
            ],
        ),
        ForeignKeyConstraint(["project_id", "task_id"], ["task_runs.project_id", "task_runs.id"]),
        CheckConstraint("mode IN ('discuss','rewrite','polish')", name="mode_allowed"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger)
    manuscript_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    request_key: Mapped[str] = mapped_column(String(100))
    task_id: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(20))
    chapter_key: Mapped[str] = mapped_column(String(100))
    instruction: Mapped[str] = mapped_column(Text)


class NovelEdit(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_edits"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        ForeignKeyConstraint(
            ["project_id", "exchange_id"], ["novel_exchanges.project_id", "novel_exchanges.id"]
        ),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="span_valid"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger)
    exchange_id: Mapped[int] = mapped_column(BigInteger)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class NovelEditDecision(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "novel_edit_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "edit_id"], ["novel_edits.project_id", "novel_edits.id"]
        ),
        ForeignKeyConstraint(
            ["project_id", "version_id"], ["novel_versions.project_id", "novel_versions.id"]
        ),
        UniqueConstraint("edit_id"),
        CheckConstraint("action IN ('accept','reject')", name="action_allowed"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger)
    edit_id: Mapped[int] = mapped_column(BigInteger)
    version_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(20))
