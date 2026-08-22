"""Mutable CaseFile, Draft, object registry, reference, and operation models."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


class CaseFile(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One structured source of truth for one personal project."""

    __tablename__ = "casefiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "id", "current_draft_id"],
            [
                "drafts.project_id",
                "drafts.casefile_id",
                "drafts.id",
            ],
            name="fk_casefiles_project_casefile_current_draft_drafts",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "current_canon_version_id"],
            [
                "canon_versions.project_id",
                "canon_versions.casefile_id",
                "canon_versions.id",
            ],
            name="fk_casefiles_project_casefile_current_canon_canon_versions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("project_id", name="uq_casefiles_project_id"),
        UniqueConstraint("project_id", "id", name="uq_casefiles_project_id_id"),
        UniqueConstraint("object_id", name="uq_casefiles_object_id"),
        CheckConstraint(
            "object_id ~ '^case_[a-z0-9][a-z0-9_]{0,55}$'",
            name="object_id_format",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(schema_version)) > 0", name="schema_version_not_blank"),
        CheckConstraint("status IN ('draft', 'canon', 'archived')", name="status_allowed"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name="archive_state_consistent",
        ),
        Index(
            "ix_casefiles_project_id_status_updated_at",
            "project_id",
            "status",
            "updated_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
    )
    current_canon_version_id: Mapped[int | None] = mapped_column(BigInteger)
    current_draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One mutable working copy in a CaseFile with one server-selected current Draft."""

    __tablename__ = "drafts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_drafts_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "base_canon_version_id"],
            [
                "canon_versions.project_id",
                "canon_versions.casefile_id",
                "canon_versions.id",
            ],
            name="fk_drafts_project_casefile_base_canon_canon_versions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "id",
            name="uq_drafts_project_id_casefile_id_id",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "version_id ~ '^draft_[a-z0-9][a-z0-9_]{0,54}$'",
            name="version_id_format",
        ),
        CheckConstraint("length(btrim(schema_version)) > 0", name="schema_version_not_blank"),
        CheckConstraint("status IN ('active', 'locked')", name="status_allowed"),
        CheckConstraint(
            "document_status IN ('draft', 'canon', 'archived')",
            name="document_status_allowed",
        ),
        Index(
            "ix_drafts_casefile_id_updated_at",
            "casefile_id",
            "updated_at",
        ),
        CheckConstraint(
            "jsonb_typeof(content_notices_jsonb) = 'array'",
            name="content_notices_is_array",
        ),
        CheckConstraint("jsonb_typeof(extensions_jsonb) = 'object'", name="extensions_is_object"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_canon_version_id: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    document_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
    )
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    parent_version_id: Mapped[str | None] = mapped_column(String(64))
    brief_version_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("brief_versions.id", ondelete="RESTRICT", use_alter=True),
    )
    schema_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
    )
    content_notices_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    extensions_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class CaseFileObject(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A stable Draft object identity whose content lives in a dedicated table."""

    __tablename__ = "casefile_objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_objects_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_casefile_objects_lineage_id",
        ),
        UniqueConstraint(
            "draft_id",
            "object_id",
            name="uq_casefile_objects_draft_id_object_id",
        ),
        UniqueConstraint(
            "draft_id",
            "object_type",
            "contract_ordinal",
            name="uq_casefile_objects_draft_type_ordinal",
        ),
        CheckConstraint(
            "object_id ~ '^[a-z][a-z0-9_]{1,127}$'",
            name="object_id_format",
        ),
        CheckConstraint(
            "object_type IN ('narrative_phase', 'phase', 'entity', 'relationship', "
            "'location', 'event', 'information_unit', 'claim', 'hypothesis', "
            "'reasoning_path', 'resolution_spec', 'constraint', 'structure_lock', "
            "'knowledge_state')",
            name="object_type_allowed",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("contract_ordinal >= 1", name="contract_ordinal_positive"),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="confidence_range",
        ),
        CheckConstraint(
            "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved')",
            name="confirmation_status_allowed",
        ),
        CheckConstraint("jsonb_typeof(source_jsonb) = 'object'", name="source_is_object"),
        CheckConstraint("jsonb_typeof(tags_jsonb) = 'array'", name="tags_is_array"),
        CheckConstraint(
            "created_by_type IN ('user', 'agent', 'system')",
            name="created_by_type_allowed",
        ),
        Index(
            "ix_casefile_objects_casefile_id_object_type_deleted_at",
            "casefile_id",
            "object_type",
            "deleted_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    contract_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    description: Mapped[str | None] = mapped_column(Text)
    tags_jsonb: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_by_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'user'"),
    )
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    source_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confirmation_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseFileRef(BigIntIdentityPrimaryKeyMixin, Base):
    """A directional, rebuildable reference between objects in one Draft."""

    __tablename__ = "casefile_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_refs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "from_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_refs_from_object",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "to_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_refs_to_object",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ref_kind",
            "ordinal",
            name="uq_casefile_refs_source_ordinal",
        ),
        UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ref_kind",
            "to_object_id",
            name="uq_casefile_refs_target",
        ),
        CheckConstraint("field_path ~ '^/'", name="field_path_json_pointer"),
        CheckConstraint(
            "ref_kind ~ '^[a-z][a-z0-9_]*$'",
            name="ref_kind_format",
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint("jsonb_typeof(metadata_jsonb) = 'object'", name="metadata_is_object"),
        Index(
            "ix_casefile_refs_draft_id_from_object_id",
            "draft_id",
            "from_object_id",
        ),
        Index(
            "ix_casefile_refs_draft_id_to_object_id",
            "draft_id",
            "to_object_id",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_object_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_object_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ref_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class CaseFileContractRef(BigIntIdentityPrimaryKeyMixin, Base):
    """An ordered v1 ObjectRef edge, including external source fragments."""

    __tablename__ = "casefile_contract_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_contract_refs_draft",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "from_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_contract_refs_from_object",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ordinal",
            name="uq_casefile_contract_refs_source_ordinal",
        ),
        CheckConstraint("field_path ~ '^/'", name="field_path_json_pointer"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "object_type IN ('casefile', 'resolution_spec', 'entity', 'relationship', "
            "'location', 'event', 'information_unit', 'claim', 'hypothesis', "
            "'reasoning_path', 'phase', 'constraint', 'structure_lock', "
            "'source_fragment')",
            name="object_type_allowed",
        ),
        CheckConstraint("length(btrim(object_id)) >= 5", name="object_id_not_blank"),
        CheckConstraint("jsonb_typeof(metadata_jsonb) = 'object'", name="metadata_is_object"),
        Index(
            "ix_casefile_contract_refs_draft_source_path",
            "draft_id",
            "from_object_id",
            "field_path",
        ),
        Index(
            "ix_casefile_contract_refs_draft_target",
            "draft_id",
            "object_type",
            "object_id",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_object_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class DraftOperation(BigIntIdentityPrimaryKeyMixin, Base):
    """An immutable ordered edit operation applied to a Draft revision."""

    __tablename__ = "draft_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_draft_operations_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "casefile_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_draft_operations_object",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "draft_id",
            "sequence_no",
            name="uq_draft_operations_draft_id_sequence_no",
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("operation_group_no >= 1", name="group_positive"),
        CheckConstraint(
            "operation_type IN "
            "('add', 'remove', 'replace', 'agent_generate_from_brief', "
            "'agent_adopt_brief_candidate', 'agent_patch_apply', 'agent_patch_undo', "
            "'logical_mutation_apply', 'logical_mutation_undo', "
            "'logical_mutation_redo', 'logical_mutation_normalize')",
            name="type_allowed",
        ),
        CheckConstraint(
            "field_path = '' OR field_path ~ '^/'",
            name="field_path_json_pointer",
        ),
        CheckConstraint("base_revision >= 1", name="base_revision_positive"),
        CheckConstraint("result_revision = base_revision + 1", name="revision_step"),
        CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL) OR "
            "(actor_kind IN ('agent', 'system', 'import') AND actor_user_id IS NULL "
            "AND actor_ref IS NOT NULL AND length(btrim(actor_ref)) > 0)",
            name="actor_shape",
        ),
        Index(
            "ix_draft_operations_draft_id_created_at",
            "draft_id",
            "created_at",
        ),
        Index(
            "ix_draft_operations_draft_id_operation_group_no_sequence_no",
            "draft_id",
            "operation_group_no",
            "sequence_no",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_object_id: Mapped[int | None] = mapped_column(BigInteger)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_group_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    old_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    new_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    actor_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
