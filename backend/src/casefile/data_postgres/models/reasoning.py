"""Normalized hypothesis, reasoning, resolution, and constraint models."""

from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


def _draft_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id"],
        ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _object_fk(column: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id", column],
        [
            "casefile_objects.project_id",
            "casefile_objects.casefile_id",
            "casefile_objects.draft_id",
            "casefile_objects.id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


class Hypothesis(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A candidate explanation connected to Claims and Information Units by references."""

    __tablename__ = "hypotheses"
    __table_args__ = (
        _draft_fk("fk_hypotheses_draft"),
        _object_fk("object_registry_id", "fk_hypotheses_object"),
        UniqueConstraint("object_registry_id", name="uq_hypotheses_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_hypotheses_lineage_id"
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "status IN ('draft', 'active', 'supported', 'refuted', 'discarded', "
            "'eliminated', 'accepted', 'rejected', 'undetermined')",
            name="status_allowed",
        ),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 1", name="score_range"),
        CheckConstraint(
            "jsonb_typeof(exclusion_rule_jsonb) = 'object'", name="exclusion_rule_is_object"
        ),
        Index("ix_hypotheses_draft_id_status", "draft_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    exclusion_rule_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ReasoningPath(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A named reasoning path containing ordered nodes and directed edges."""

    __tablename__ = "reasoning_paths"
    __table_args__ = (
        _draft_fk("fk_reasoning_paths_draft"),
        _object_fk("object_registry_id", "fk_reasoning_paths_object"),
        UniqueConstraint("object_registry_id", name="uq_reasoning_paths_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_reasoning_paths_lineage_id"
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "reasoning_type IN ('deductive', 'inductive', 'abductive', 'mixed', "
            "'exclusion', 'causal', 'proof', 'combination', 'relationship', 'temporal', "
            "'decision', 'rule_derivation', 'counterfactual')",
            name="reasoning_type_allowed",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'confirmed', 'rejected')", name="status_allowed"
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
        Index("ix_reasoning_paths_draft_id_status", "draft_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reasoning_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    human_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    summary: Mapped[str | None] = mapped_column(Text)
    required_for_resolution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class ReasoningNode(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """An ordered node in one Reasoning Path."""

    __tablename__ = "reasoning_nodes"
    __table_args__ = (
        _draft_fk("fk_reasoning_nodes_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id"],
            [
                "reasoning_paths.project_id",
                "reasoning_paths.casefile_id",
                "reasoning_paths.draft_id",
                "reasoning_paths.id",
            ],
            name="fk_reasoning_nodes_path",
            ondelete="RESTRICT",
        ),
        _object_fk("source_object_id", "fk_reasoning_nodes_source_object"),
        UniqueConstraint("reasoning_path_id", "node_key", name="uq_reasoning_nodes_path_key"),
        UniqueConstraint("reasoning_path_id", "ordinal", name="uq_reasoning_nodes_path_ordinal"),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "reasoning_path_id",
            "id",
            name="uq_reasoning_nodes_path_lineage_id",
        ),
        CheckConstraint("node_key ~ '^[a-z][a-z0-9_]{1,127}$'", name="node_key_format"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint("node_type ~ '^[a-z][a-z0-9_]*$'", name="node_type_format"),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_not_blank"),
        CheckConstraint("jsonb_typeof(attributes_jsonb) = 'object'", name="attributes_is_object"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reasoning_path_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_object_id: Mapped[int | None] = mapped_column(BigInteger)
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ReasoningEdge(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A directed argument edge whose two nodes belong to the same Reasoning Path."""

    __tablename__ = "reasoning_edges"
    __table_args__ = (
        _draft_fk("fk_reasoning_edges_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id"],
            [
                "reasoning_paths.project_id",
                "reasoning_paths.casefile_id",
                "reasoning_paths.draft_id",
                "reasoning_paths.id",
            ],
            name="fk_reasoning_edges_path",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_id",
                "casefile_id",
                "draft_id",
                "reasoning_path_id",
                "from_node_id",
            ],
            [
                "reasoning_nodes.project_id",
                "reasoning_nodes.casefile_id",
                "reasoning_nodes.draft_id",
                "reasoning_nodes.reasoning_path_id",
                "reasoning_nodes.id",
            ],
            name="fk_reasoning_edges_from_node",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "reasoning_path_id", "to_node_id"],
            [
                "reasoning_nodes.project_id",
                "reasoning_nodes.casefile_id",
                "reasoning_nodes.draft_id",
                "reasoning_nodes.reasoning_path_id",
                "reasoning_nodes.id",
            ],
            name="fk_reasoning_edges_to_node",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "reasoning_path_id",
            "from_node_id",
            "to_node_id",
            "argument_kind",
            name="uq_reasoning_edges_argument",
        ),
        CheckConstraint("from_node_id <> to_node_id", name="nodes_differ"),
        CheckConstraint("argument_kind ~ '^[a-z][a-z0-9_]*$'", name="argument_kind_format"),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
        CheckConstraint("jsonb_typeof(attributes_jsonb) = 'object'", name="attributes_is_object"),
        Index("ix_reasoning_edges_path_from", "reasoning_path_id", "from_node_id"),
        Index("ix_reasoning_edges_path_to", "reasoning_path_id", "to_node_id"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reasoning_path_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    argument_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    human_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    attributes_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ResolutionSpec(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One v1 dynamic resolution contract for a Draft."""

    __tablename__ = "resolution_specs"
    __table_args__ = (
        _draft_fk("fk_resolution_specs_draft"),
        _object_fk("object_registry_id", "fk_resolution_specs_object"),
        UniqueConstraint("object_registry_id", name="uq_resolution_specs_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_resolution_specs_lineage_id"
        ),
        CheckConstraint("question_type ~ '^[a-z][a-z0-9_]*$'", name="question_type_format"),
        CheckConstraint("length(btrim(target_question)) > 0", name="target_question_not_blank"),
        CheckConstraint("title IS NULL OR length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "conclusion_mode IS NULL OR conclusion_mode IN ('unique', 'finite_multiple', "
            "'optimal', 'probabilistic', 'open_interpretation', 'multiple_endings', "
            "'undetermined')",
            name="conclusion_mode_allowed",
        ),
        CheckConstraint(
            "jsonb_typeof(conclusion_pattern_jsonb) = 'object'",
            name="conclusion_pattern_is_object",
        ),
        CheckConstraint("status IN ('draft', 'resolved', 'locked')", name="status_allowed"),
        CheckConstraint(
            "jsonb_typeof(accepted_answer_texts_jsonb) = 'object'",
            name="accepted_answer_texts_is_object",
        ),
        CheckConstraint(
            "jsonb_typeof(fairness_requirements_jsonb) = 'array'",
            name="fairness_requirements_is_array",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    question_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_question: Mapped[str] = mapped_column(Text, nullable=False)
    conclusion_mode: Mapped[str | None] = mapped_column(String(32))
    accepted_answer_texts_jsonb: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    fairness_requirements_jsonb: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    conclusion_pattern_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))


class ResolutionSlot(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One ordered, schema-defined dynamic value in a Resolution Spec."""

    __tablename__ = "resolution_slots"
    __table_args__ = (
        _draft_fk("fk_resolution_slots_draft"),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "resolution_spec_id"],
            [
                "resolution_specs.project_id",
                "resolution_specs.casefile_id",
                "resolution_specs.draft_id",
                "resolution_specs.id",
            ],
            name="fk_resolution_slots_spec",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("resolution_spec_id", "slot_key", name="uq_resolution_slots_spec_key"),
        UniqueConstraint("resolution_spec_id", "ordinal", name="uq_resolution_slots_spec_ordinal"),
        CheckConstraint("slot_key ~ '^[a-z][a-z0-9_]{1,127}$'", name="slot_key_format"),
        CheckConstraint("length(btrim(label)) > 0", name="label_not_blank"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "value_type IS NULL OR value_type IN ('entity_or_claim_ref', "
            "'text_or_claim_ref', 'object_ref', 'text', 'number', 'boolean')",
            name="value_type_allowed",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolution_spec_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_type: Mapped[str | None] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    value_jsonb: Mapped[Any | None] = mapped_column(JSONB)


class CaseFileConstraint(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A hard or soft machine-readable constraint over an optional target object."""

    __tablename__ = "casefile_constraints"
    __table_args__ = (
        _draft_fk("fk_casefile_constraints_draft"),
        _object_fk("object_registry_id", "fk_casefile_constraints_object"),
        _object_fk("target_object_id", "fk_casefile_constraints_target_object"),
        UniqueConstraint("object_registry_id", name="uq_casefile_constraints_object_registry_id"),
        CheckConstraint("constraint_kind ~ '^[a-z][a-z0-9_]*$'", name="constraint_kind_format"),
        CheckConstraint("constraint_level IN ('hard', 'soft')", name="constraint_level_allowed"),
        CheckConstraint("jsonb_typeof(rule_jsonb) = 'object'", name="rule_is_object"),
        CheckConstraint("title IS NULL OR length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "statement IS NULL OR length(btrim(statement)) > 0",
            name="statement_not_blank",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="status_allowed"),
        CheckConstraint(
            "conflict_status IN ('none', 'potential', 'confirmed', 'resolved')",
            name="conflict_status_allowed",
        ),
        Index("ix_casefile_constraints_draft_id_status", "draft_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_object_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(200))
    statement: Mapped[str | None] = mapped_column(Text)
    rule_expression: Mapped[str | None] = mapped_column(Text)
    constraint_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    constraint_level: Mapped[str] = mapped_column(String(12), nullable=False)
    rule_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    conflict_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'none'")
    )


class StructureLock(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A v1 field-level structure lock over one CaseFile object."""

    __tablename__ = "structure_locks"
    __table_args__ = (
        _draft_fk("fk_structure_locks_draft"),
        _object_fk("object_registry_id", "fk_structure_locks_object"),
        UniqueConstraint("object_registry_id", name="uq_structure_locks_object_registry_id"),
        UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_structure_locks_lineage_id"
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("lock_type IN ('hard', 'soft', 'open')", name="lock_type_allowed"),
        CheckConstraint("jsonb_typeof(field_paths_jsonb) = 'array'", name="field_paths_is_array"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        Index("ix_structure_locks_draft_id_type", "draft_id", "lock_type"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_registry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lock_type: Mapped[str] = mapped_column(String(12), nullable=False)
    field_paths_jsonb: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
