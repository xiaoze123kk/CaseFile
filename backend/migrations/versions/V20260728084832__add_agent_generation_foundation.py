"""add_agent_generation_foundation

Revision ID: 20260728084832
Revises: 20260726131019
Create Date: 2026-07-28 08:48:34.456963
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728084832"
down_revision: str | None = "20260726131019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_casefile_foundation()
    _extend_content_tables()
    _extend_reasoning_tables()
    _create_relationship_and_lock_tables()
    _create_provider_and_brief_tables()
    _create_task_tables()
    _create_v1_integrity_triggers()


def downgrade() -> None:
    _drop_v1_integrity_triggers()
    _drop_task_tables()
    _drop_provider_and_brief_tables()
    _drop_relationship_and_lock_tables()
    _restore_reasoning_tables()
    _restore_content_tables()
    _restore_casefile_foundation()


def _extend_casefile_foundation() -> None:
    op.add_column("casefiles", sa.Column("object_id", sa.String(64), nullable=True))
    op.execute("UPDATE casefiles SET object_id = 'case_' || id::text")
    op.alter_column("casefiles", "object_id", nullable=False)
    op.create_unique_constraint("uq_casefiles_object_id", "casefiles", ["object_id"])
    op.create_check_constraint(
        op.f("ck_casefiles_object_id_format"),
        "casefiles",
        "object_id ~ '^case_[a-z0-9][a-z0-9_]{0,55}$'",
    )

    op.add_column("drafts", sa.Column("version_id", sa.String(64), nullable=True))
    op.add_column(
        "drafts", sa.Column("version_no", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    op.add_column("drafts", sa.Column("parent_version_id", sa.String(64), nullable=True))
    op.add_column("drafts", sa.Column("brief_version_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "drafts",
        sa.Column(
            "content_notices_jsonb", _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "drafts",
        sa.Column(
            "extensions_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.execute("UPDATE drafts SET version_id = 'draft_' || id::text")
    op.alter_column("drafts", "version_id", nullable=False)
    op.create_check_constraint(op.f("ck_drafts_version_no_positive"), "drafts", "version_no >= 1")
    op.create_check_constraint(
        op.f("ck_drafts_version_id_format"),
        "drafts",
        "version_id ~ '^draft_[a-z0-9][a-z0-9_]{0,54}$'",
    )
    op.create_check_constraint(
        op.f("ck_drafts_content_notices_is_array"),
        "drafts",
        "jsonb_typeof(content_notices_jsonb) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_drafts_extensions_is_object"),
        "drafts",
        "jsonb_typeof(extensions_jsonb) = 'object'",
    )

    op.add_column("casefile_objects", sa.Column("contract_ordinal", sa.Integer(), nullable=True))
    op.add_column("casefile_objects", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "casefile_objects",
        sa.Column("tags_jsonb", _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "casefile_objects",
        sa.Column(
            "created_by_type", sa.String(16), server_default=sa.text("'user'"), nullable=False
        ),
    )
    op.add_column("casefile_objects", sa.Column("created_by_id", sa.String(64), nullable=True))
    op.add_column(
        "casefile_objects", sa.Column("contract_updated_at", sa.String(40), nullable=True)
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY draft_id, object_type ORDER BY id) AS ordinal
              FROM casefile_objects
        )
        UPDATE casefile_objects AS target
           SET contract_ordinal = ranked.ordinal
          FROM ranked
         WHERE ranked.id = target.id
        """
    )
    op.execute(
        """
        UPDATE casefile_objects AS object
           SET created_by_id = 'user_' || project.owner_user_id::text,
               contract_updated_at = to_char(
                   object.updated_at AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               )
          FROM projects AS project
         WHERE project.id = object.project_id
        """
    )
    op.alter_column("casefile_objects", "contract_ordinal", nullable=False)
    op.alter_column("casefile_objects", "created_by_id", nullable=False)
    op.alter_column("casefile_objects", "contract_updated_at", nullable=False)
    op.create_unique_constraint(
        "uq_casefile_objects_draft_type_ordinal",
        "casefile_objects",
        ["draft_id", "object_type", "contract_ordinal"],
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_contract_ordinal_positive"),
        "casefile_objects",
        "contract_ordinal >= 1",
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_tags_is_array"),
        "casefile_objects",
        "jsonb_typeof(tags_jsonb) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_created_by_type_allowed"),
        "casefile_objects",
        "created_by_type IN ('user', 'agent', 'system')",
    )
    op.drop_constraint(
        op.f("ck_casefile_objects_object_type_allowed"), "casefile_objects", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_object_type_allowed"),
        "casefile_objects",
        "object_type IN ('narrative_phase', 'phase', 'entity', 'relationship', 'location', "
        "'event', 'information_unit', 'claim', 'hypothesis', 'reasoning_path', "
        "'resolution_spec', 'constraint', 'structure_lock', 'knowledge_state')",
    )
    op.drop_constraint(op.f("ck_draft_operations_type_allowed"), "draft_operations", type_="check")
    op.alter_column(
        "draft_operations",
        "operation_type",
        existing_type=sa.String(16),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN ('add', 'remove', 'replace', 'agent_generate_from_brief')",
    )


def _extend_content_tables() -> None:
    for name in (
        "entry_conditions_jsonb",
        "allowed_action_types_jsonb",
        "completion_conditions_jsonb",
    ):
        op.add_column(
            "narrative_phases",
            sa.Column(name, _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        )
        op.create_check_constraint(
            op.f(f"ck_narrative_phases_{name.removesuffix('_jsonb')}_is_array"),
            "narrative_phases",
            f"jsonb_typeof({name}) = 'array'",
        )

    for name in ("aliases_jsonb", "goals_jsonb", "secrets_jsonb", "capabilities_jsonb"):
        op.add_column(
            "entities",
            sa.Column(name, _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        )
    op.drop_constraint(op.f("ck_entities_entity_kind_allowed"), "entities", type_="check")
    op.create_check_constraint(
        op.f("ck_entities_entity_kind_allowed"),
        "entities",
        "entity_kind IN ('person', 'organization', 'object', 'system', 'faction', "
        "'rule_actor', 'location', 'concept', 'other')",
    )

    op.alter_column("locations", "entity_id", nullable=True)
    op.add_column("locations", sa.Column("object_registry_id", sa.BigInteger(), nullable=True))
    op.add_column("locations", sa.Column("name", sa.String(200), nullable=True))
    op.add_column(
        "locations",
        sa.Column(
            "access_rules_jsonb", _jsonb(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "locations",
        sa.Column(
            "visibility_rules_jsonb",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_locations_object",
        "locations",
        "casefile_objects",
        ["project_id", "casefile_id", "draft_id", "object_registry_id"],
        ["project_id", "casefile_id", "draft_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_locations_object_registry_id", "locations", ["object_registry_id"]
    )
    op.create_check_constraint(
        op.f("ck_locations_name_not_blank"),
        "locations",
        "name IS NULL OR length(btrim(name)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_locations_access_rules_is_array"),
        "locations",
        "jsonb_typeof(access_rules_jsonb) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_locations_visibility_rules_is_array"),
        "locations",
        "jsonb_typeof(visibility_rules_jsonb) = 'array'",
    )

    op.add_column("events", sa.Column("time_jsonb", _jsonb(), nullable=True))
    op.create_check_constraint(
        op.f("ck_events_time_is_object"),
        "events",
        "time_jsonb IS NULL OR jsonb_typeof(time_jsonb) = 'object'",
    )
    op.drop_constraint(op.f("ck_events_truth_status_allowed"), "events", type_="check")
    op.create_check_constraint(
        op.f("ck_events_truth_status_allowed"),
        "events",
        "truth_status IN ('true', 'false', 'uncertain', 'canon_true', 'reported', "
        "'disputed', 'false_belief', 'unknown')",
    )

    op.add_column("information_units", sa.Column("reliability", sa.String(16), nullable=True))
    op.add_column("information_units", sa.Column("truth_status", sa.String(20), nullable=True))
    op.add_column("information_units", sa.Column("classification", sa.String(20), nullable=True))
    op.add_column(
        "information_units",
        sa.Column(
            "acquisition_conditions_jsonb",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        op.f("ck_information_units_information_kind_allowed"),
        "information_units",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_information_units_information_kind_allowed"),
        "information_units",
        "information_kind IN ('evidence', 'testimony', 'observation', 'dialogue', "
        "'document', 'system_log', 'rule', 'environment', 'feedback', 'clue', 'other')",
    )
    op.create_check_constraint(
        op.f("ck_information_units_reliability_allowed"),
        "information_units",
        "reliability IS NULL OR reliability IN ('high', 'medium', 'low', 'unknown')",
    )
    op.create_check_constraint(
        op.f("ck_information_units_truth_status_allowed"),
        "information_units",
        "truth_status IS NULL OR truth_status IN "
        "('canon_true', 'reported', 'disputed', 'false_belief', 'unknown')",
    )
    op.create_check_constraint(
        op.f("ck_information_units_classification_allowed"),
        "information_units",
        "classification IS NULL OR classification IN "
        "('key', 'supporting', 'background', 'distractor', 'misleading', 'incomplete')",
    )
    op.create_check_constraint(
        op.f("ck_information_units_acquisition_conditions_is_array"),
        "information_units",
        "jsonb_typeof(acquisition_conditions_jsonb) = 'array'",
    )

    op.add_column("claims", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("claims", sa.Column("claim_type", sa.String(20), nullable=True))
    op.add_column("claims", sa.Column("materiality", sa.String(20), nullable=True))
    op.drop_constraint(op.f("ck_claims_status_allowed"), "claims", type_="check")
    op.create_check_constraint(
        op.f("ck_claims_status_allowed"),
        "claims",
        "status IN ('unsupported', 'partially_supported', 'supported', 'refuted', "
        "'disputed', 'unresolved')",
    )
    op.create_check_constraint(
        op.f("ck_claims_title_not_blank"),
        "claims",
        "title IS NULL OR length(btrim(title)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_claims_claim_type_allowed"),
        "claims",
        "claim_type IS NULL OR claim_type IN ('fact', 'causal', 'identity', 'relationship', "
        "'temporal', 'rule', 'evaluative', 'other')",
    )
    op.create_check_constraint(
        op.f("ck_claims_materiality_allowed"),
        "claims",
        "materiality IS NULL OR materiality IN ('critical', 'major', 'minor', 'background')",
    )


def _extend_reasoning_tables() -> None:
    op.drop_constraint(op.f("ck_hypotheses_status_allowed"), "hypotheses", type_="check")
    op.create_check_constraint(
        op.f("ck_hypotheses_status_allowed"),
        "hypotheses",
        "status IN ('draft', 'active', 'supported', 'refuted', 'discarded', 'eliminated', "
        "'accepted', 'rejected', 'undetermined')",
    )
    op.add_column(
        "reasoning_paths",
        sa.Column(
            "required_for_resolution", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.drop_constraint(
        op.f("ck_reasoning_paths_reasoning_type_allowed"), "reasoning_paths", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_reasoning_paths_reasoning_type_allowed"),
        "reasoning_paths",
        "reasoning_type IN ('deductive', 'inductive', 'abductive', 'mixed', 'exclusion', "
        "'causal', 'proof', 'combination', 'relationship', 'temporal', 'decision', "
        "'rule_derivation', 'counterfactual')",
    )

    op.drop_constraint("uq_resolution_specs_draft_id", "resolution_specs", type_="unique")
    op.add_column("resolution_specs", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("resolution_specs", sa.Column("conclusion_mode", sa.String(32), nullable=True))
    op.add_column(
        "resolution_specs",
        sa.Column(
            "accepted_answer_texts_jsonb",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "resolution_specs",
        sa.Column(
            "fairness_requirements_jsonb",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_title_not_blank"),
        "resolution_specs",
        "title IS NULL OR length(btrim(title)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_mode_allowed"),
        "resolution_specs",
        "conclusion_mode IS NULL OR conclusion_mode IN ('unique', 'finite_multiple', 'optimal', "
        "'probabilistic', 'open_interpretation', 'multiple_endings', 'undetermined')",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_accepted_answer_texts_is_object"),
        "resolution_specs",
        "jsonb_typeof(accepted_answer_texts_jsonb) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_fairness_requirements_is_array"),
        "resolution_specs",
        "jsonb_typeof(fairness_requirements_jsonb) = 'array'",
    )
    op.add_column("resolution_slots", sa.Column("value_type", sa.String(32), nullable=True))
    op.create_check_constraint(
        op.f("ck_resolution_slots_value_type_allowed"),
        "resolution_slots",
        "value_type IS NULL OR value_type IN ('entity_or_claim_ref', 'text_or_claim_ref', "
        "'object_ref', 'text', 'number', 'boolean')",
    )

    op.add_column("casefile_constraints", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("casefile_constraints", sa.Column("statement", sa.Text(), nullable=True))
    op.add_column("casefile_constraints", sa.Column("rule_expression", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_casefile_constraints_title_not_blank"),
        "casefile_constraints",
        "title IS NULL OR length(btrim(title)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_casefile_constraints_statement_not_blank"),
        "casefile_constraints",
        "statement IS NULL OR length(btrim(statement)) > 0",
    )


def _create_relationship_and_lock_tables() -> None:
    op.create_table(
        "relationships",
        _id(),
        *_lineage_columns(),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("truth_status", sa.String(20), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_relationships_title_not_blank")
        ),
        sa.CheckConstraint(
            "relationship_type ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_relationships_relationship_type_format"),
        ),
        sa.CheckConstraint(
            "direction IN ('directed', 'undirected', 'bidirectional')",
            name=op.f("ck_relationships_direction_allowed"),
        ),
        sa.CheckConstraint(
            "truth_status IN ('canon_true', 'reported', 'disputed', 'false_belief', 'unknown')",
            name=op.f("ck_relationships_truth_status_allowed"),
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private', 'restricted', 'hidden')",
            name=op.f("ck_relationships_visibility_allowed"),
        ),
        _draft_fk("fk_relationships_draft"),
        _object_fk("object_registry_id", "fk_relationships_object"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
        sa.UniqueConstraint("object_registry_id", name="uq_relationships_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_relationships_lineage_id"
        ),
    )
    op.create_index(
        "ix_relationships_draft_id_type", "relationships", ["draft_id", "relationship_type"]
    )

    op.create_table(
        "structure_locks",
        _id(),
        *_lineage_columns(),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("lock_type", sa.String(12), nullable=False),
        sa.Column("field_paths_jsonb", _jsonb(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_structure_locks_title_not_blank")
        ),
        sa.CheckConstraint(
            "lock_type IN ('hard', 'soft', 'open')",
            name=op.f("ck_structure_locks_lock_type_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_paths_jsonb) = 'array'",
            name=op.f("ck_structure_locks_field_paths_is_array"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_structure_locks_reason_not_blank")
        ),
        _draft_fk("fk_structure_locks_draft"),
        _object_fk("object_registry_id", "fk_structure_locks_object"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_locks")),
        sa.UniqueConstraint("object_registry_id", name="uq_structure_locks_object_registry_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_structure_locks_lineage_id"
        ),
    )
    op.create_index(
        "ix_structure_locks_draft_id_type", "structure_locks", ["draft_id", "lock_type"]
    )

    op.create_table(
        "casefile_contract_refs",
        _id(),
        *_lineage_columns(),
        sa.Column("from_object_id", sa.BigInteger(), nullable=False),
        sa.Column("field_path", sa.String(512), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "field_path ~ '^/'", name=op.f("ck_casefile_contract_refs_field_path_json_pointer")
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_casefile_contract_refs_ordinal_positive")),
        sa.CheckConstraint(
            "object_type IN ('casefile', 'resolution_spec', 'entity', 'relationship', "
            "'location', 'event', 'information_unit', 'claim', 'hypothesis', "
            "'reasoning_path', 'phase', 'constraint', 'structure_lock', 'source_fragment')",
            name=op.f("ck_casefile_contract_refs_object_type_allowed"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_id)) >= 5",
            name=op.f("ck_casefile_contract_refs_object_id_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_jsonb) = 'object'",
            name=op.f("ck_casefile_contract_refs_metadata_is_object"),
        ),
        _draft_fk("fk_casefile_contract_refs_draft"),
        _object_fk("from_object_id", "fk_casefile_contract_refs_from_object", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_casefile_contract_refs")),
        sa.UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ordinal",
            name="uq_casefile_contract_refs_source_ordinal",
        ),
    )
    op.create_index(
        "ix_casefile_contract_refs_draft_source_path",
        "casefile_contract_refs",
        ["draft_id", "from_object_id", "field_path"],
    )
    op.create_index(
        "ix_casefile_contract_refs_draft_target",
        "casefile_contract_refs",
        ["draft_id", "object_type", "object_id"],
    )


def _create_provider_and_brief_tables() -> None:
    op.create_table(
        "user_provider_settings",
        _id(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_id", sa.String(160), nullable=False),
        sa.Column("model_is_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("config_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("secret_last_four", sa.String(4), nullable=False),
        sa.Column(
            "credential_status",
            sa.String(20),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
        sa.Column(
            "default_budget_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_error_code", sa.String(80), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "provider ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_user_provider_settings_provider_format")
        ),
        sa.CheckConstraint(
            "length(btrim(model_id)) > 0", name=op.f("ck_user_provider_settings_model_id_not_blank")
        ),
        sa.CheckConstraint(
            "key_version >= 1", name=op.f("ck_user_provider_settings_key_version_positive")
        ),
        sa.CheckConstraint(
            "config_version >= 1", name=op.f("ck_user_provider_settings_config_version_positive")
        ),
        sa.CheckConstraint(
            "octet_length(secret_nonce) = 12",
            name=op.f("ck_user_provider_settings_secret_nonce_length"),
        ),
        sa.CheckConstraint(
            "octet_length(secret_ciphertext) > 16",
            name=op.f("ck_user_provider_settings_ciphertext_not_empty"),
        ),
        sa.CheckConstraint(
            "length(secret_last_four) = 4", name=op.f("ck_user_provider_settings_last_four_length")
        ),
        sa.CheckConstraint(
            "credential_status IN ('unverified', 'valid', 'invalid')",
            name=op.f("ck_user_provider_settings_credential_status_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(default_budget_jsonb) = 'object'",
            name=op.f("ck_user_provider_settings_budget_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_provider_settings_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_provider_settings")),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider_settings_user_provider"),
        sa.UniqueConstraint("user_id", "id", name="uq_user_provider_settings_user_id_id"),
    )
    op.create_index(
        "ix_user_provider_settings_user_id_updated_at",
        "user_provider_settings",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "briefs",
        _id(),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("draft_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("draft_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("draft_revision >= 1", name=op.f("ck_briefs_draft_revision_positive")),
        sa.CheckConstraint(
            "public_id ~ '^brief_[a-z0-9][a-z0-9_]{0,54}$'",
            name=op.f("ck_briefs_public_id_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(draft_jsonb) = 'object'", name=op.f("ck_briefs_draft_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_briefs_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefs")),
        sa.UniqueConstraint("project_id", name="uq_briefs_project_id"),
        sa.UniqueConstraint("project_id", "id", name="uq_briefs_project_id_id"),
        sa.UniqueConstraint("public_id", name="uq_briefs_public_id"),
    )
    op.create_table(
        "brief_versions",
        _id(),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("brief_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_jsonb", _jsonb(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_brief_versions_version_no_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(content_jsonb) = 'object'",
            name=op.f("ck_brief_versions_content_is_object"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_brief_versions_content_hash_format")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "brief_id"],
            ["briefs.project_id", "briefs.id"],
            name="fk_brief_versions_project_brief_briefs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["users.id"],
            name=op.f("fk_brief_versions_confirmed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_versions")),
        sa.UniqueConstraint("project_id", "id", name="uq_brief_versions_project_id_id"),
        sa.UniqueConstraint("project_id", "brief_id", "id", name="uq_brief_versions_lineage_id"),
        sa.UniqueConstraint("brief_id", "version_no", name="uq_brief_versions_brief_version_no"),
    )
    op.create_index(
        "ix_brief_versions_brief_id_confirmed_at", "brief_versions", ["brief_id", "confirmed_at"]
    )
    op.create_foreign_key(
        "fk_briefs_project_brief_current_version_brief_versions",
        "briefs",
        "brief_versions",
        ["project_id", "id", "current_version_id"],
        ["project_id", "brief_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_drafts_brief_version_id_brief_versions"),
        "drafts",
        "brief_versions",
        ["brief_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        INSERT INTO briefs (project_id, public_id, draft_revision, draft_jsonb)
        SELECT project.id, 'brief_' || project.id::text, 1, '{}'::jsonb
          FROM projects AS project
        ON CONFLICT (project_id) DO NOTHING
        """
    )


def _create_task_tables() -> None:
    op.create_table(
        "task_runs",
        _id(),
        *_lineage_columns(),
        sa.Column("brief_version_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_setting_id", sa.BigInteger(), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", sa.String(64), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("input_draft_revision", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_id", sa.String(160), nullable=False),
        sa.Column("provider_config_version", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("toolset_version", sa.String(64), nullable=False),
        sa.Column("budget_jsonb", _jsonb(), nullable=False),
        sa.Column("usage_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("leased_by", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "error_details_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "task_type ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_task_runs_task_type_format")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_task_runs_status_allowed"),
        ),
        sa.CheckConstraint(
            "input_draft_revision >= 1", name=op.f("ck_task_runs_input_revision_positive")
        ),
        sa.CheckConstraint(
            "provider_config_version >= 1", name=op.f("ck_task_runs_provider_version_positive")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_task_runs_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(budget_jsonb) = 'object'", name=op.f("ck_task_runs_budget_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_jsonb) = 'object'", name=op.f("ck_task_runs_usage_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(error_details_jsonb) = 'object'",
            name=op.f("ck_task_runs_error_details_is_object"),
        ),
        _draft_fk("fk_task_runs_project_casefile_draft_drafts"),
        sa.ForeignKeyConstraint(
            ["project_id", "brief_version_id"],
            ["brief_versions.project_id", "brief_versions.id"],
            name="fk_task_runs_project_brief_version_brief_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "provider_setting_id"],
            ["user_provider_settings.user_id", "user_provider_settings.id"],
            name="fk_task_runs_actor_provider_setting_user_provider_settings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_task_runs_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_snapshot_id"],
            ["draft_snapshots.id"],
            name=op.f("fk_task_runs_result_snapshot_id_draft_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_runs")),
        sa.UniqueConstraint("project_id", "id", name="uq_task_runs_project_id_id"),
    )
    op.create_index("ix_task_runs_status_created_at", "task_runs", ["status", "created_at"])
    op.create_index("ix_task_runs_project_id_updated_at", "task_runs", ["project_id", "updated_at"])
    op.create_index("ix_task_runs_lease_expires_at", "task_runs", ["lease_expires_at"])

    op.create_table(
        "task_attempts",
        _id(),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("candidate_jsonb", _jsonb(), nullable=True),
        sa.Column(
            "validation_errors_jsonb",
            _jsonb(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("usage_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "error_details_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_no >= 1", name=op.f("ck_task_attempts_attempt_no_positive")),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_task_attempts_status_allowed"),
        ),
        sa.CheckConstraint(
            "candidate_jsonb IS NULL OR jsonb_typeof(candidate_jsonb) = 'object'",
            name=op.f("ck_task_attempts_candidate_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_errors_jsonb) = 'array'",
            name=op.f("ck_task_attempts_validation_errors_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_jsonb) = 'object'", name=op.f("ck_task_attempts_usage_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(error_details_jsonb) = 'object'",
            name=op.f("ck_task_attempts_error_details_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_task_attempts_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_attempts")),
        sa.UniqueConstraint("task_run_id", "attempt_no", name="uq_task_attempts_run_attempt_no"),
    )
    op.create_index(
        "ix_task_attempts_task_run_id_started_at", "task_attempts", ["task_run_id", "started_at"]
    )

    op.create_table(
        "task_events",
        _id(),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("payload_jsonb", _jsonb(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence_no >= 1", name=op.f("ck_task_events_sequence_no_positive")),
        sa.CheckConstraint(
            "event_type ~ '^[a-z][a-z0-9_.]*$'", name=op.f("ck_task_events_event_type_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_jsonb) = 'object'", name=op.f("ck_task_events_payload_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_task_events_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
        sa.UniqueConstraint("task_run_id", "sequence_no", name="uq_task_events_run_sequence_no"),
    )
    op.create_index(
        "ix_task_events_task_run_id_sequence_no", "task_events", ["task_run_id", "sequence_no"]
    )


def _create_v1_integrity_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_prevent_discriminator_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE discriminator_name text;
        BEGIN
            discriminator_name := CASE TG_TABLE_NAME
                WHEN 'entities' THEN 'entity_kind'
                WHEN 'information_units' THEN 'information_kind'
                ELSE NULL
            END;
            IF discriminator_name IS NOT NULL
               AND (to_jsonb(NEW) ->> discriminator_name)
                   IS DISTINCT FROM (to_jsonb(OLD) ->> discriminator_name) THEN
                RAISE EXCEPTION '% is immutable', discriminator_name;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in (
        "briefs",
        "relationships",
        "structure_locks",
        "task_runs",
        "user_provider_settings",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
            """
        )
    for table in ("brief_versions", "task_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION casefile_reject_history_mutation()
            """
        )
    for table in ("relationships", "structure_locks"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_registered_type
            BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, object_registry_id
            ON {table}
            FOR EACH ROW EXECUTE FUNCTION casefile_validate_content_object_type()
            """
        )
    op.execute("DROP TRIGGER trg_locations_entity_kind ON locations")
    op.execute(
        """
        CREATE TRIGGER trg_locations_entity_kind
        BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, entity_id, object_registry_id
        ON locations
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_entity_extension()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_content_object_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_type text; actual_type text;
        BEGIN
            expected_type := CASE TG_TABLE_NAME
                WHEN 'narrative_phases' THEN 'phase'
                WHEN 'entities' THEN 'entity'
                WHEN 'relationships' THEN 'relationship'
                WHEN 'events' THEN 'event'
                WHEN 'information_units' THEN 'information_unit'
                WHEN 'claims' THEN 'claim'
                WHEN 'hypotheses' THEN 'hypothesis'
                WHEN 'reasoning_paths' THEN 'reasoning_path'
                WHEN 'resolution_specs' THEN 'resolution_spec'
                WHEN 'casefile_constraints' THEN 'constraint'
                WHEN 'structure_locks' THEN 'structure_lock'
                WHEN 'knowledge_states' THEN 'knowledge_state'
                ELSE NULL
            END;
            SELECT object_type INTO actual_type
              FROM casefile_objects
             WHERE id = NEW.object_registry_id
               AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id
               AND draft_id = NEW.draft_id;
            IF TG_TABLE_NAME = 'narrative_phases'
               AND actual_type IN ('phase', 'narrative_phase') THEN
                RETURN NEW;
            END IF;
            IF actual_type IS NULL OR actual_type <> expected_type THEN
                RAISE EXCEPTION '% requires registered object_type %, got %',
                    TG_TABLE_NAME, expected_type, COALESCE(actual_type, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_entity_extension()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actual_kind text; actual_type text;
        BEGIN
            IF TG_TABLE_NAME = 'locations' AND NEW.entity_id IS NULL THEN
                SELECT object_type INTO actual_type
                  FROM casefile_objects
                 WHERE id = NEW.object_registry_id
                   AND project_id = NEW.project_id
                   AND casefile_id = NEW.casefile_id
                   AND draft_id = NEW.draft_id;
                IF actual_type IS DISTINCT FROM 'location' THEN
                    RAISE EXCEPTION 'formal location requires registered object_type location';
                END IF;
                RETURN NEW;
            END IF;
            SELECT entity_kind INTO actual_kind
              FROM entities
             WHERE id = NEW.entity_id
               AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id
               AND draft_id = NEW.draft_id;
            IF actual_kind IS NULL OR actual_kind <> (CASE TG_TABLE_NAME
                WHEN 'people' THEN 'person' WHEN 'locations' THEN 'location' END) THEN
                RAISE EXCEPTION '% has an invalid entity extension', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def _drop_v1_integrity_triggers() -> None:
    op.execute("DROP TRIGGER trg_locations_entity_kind ON locations")
    for table in ("relationships", "structure_locks"):
        op.execute(f"DROP TRIGGER trg_{table}_registered_type ON {table}")
    for table in ("brief_versions", "task_events"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    for table in (
        "briefs",
        "relationships",
        "structure_locks",
        "task_runs",
        "user_provider_settings",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_updated_at ON {table}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_content_object_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_type text; actual_type text;
        BEGIN
            expected_type := CASE TG_TABLE_NAME
                WHEN 'narrative_phases' THEN 'narrative_phase'
                WHEN 'entities' THEN 'entity'
                WHEN 'events' THEN 'event'
                WHEN 'information_units' THEN 'information_unit'
                WHEN 'claims' THEN 'claim'
                WHEN 'hypotheses' THEN 'hypothesis'
                WHEN 'reasoning_paths' THEN 'reasoning_path'
                WHEN 'resolution_specs' THEN 'resolution_spec'
                WHEN 'casefile_constraints' THEN 'constraint'
                WHEN 'knowledge_states' THEN 'knowledge_state'
                ELSE NULL
            END;
            SELECT object_type INTO actual_type FROM casefile_objects
             WHERE id = NEW.object_registry_id AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id AND draft_id = NEW.draft_id;
            IF actual_type IS NULL OR actual_type <> expected_type THEN
                RAISE EXCEPTION '% requires registered object_type %, got %',
                    TG_TABLE_NAME, expected_type, COALESCE(actual_type, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_validate_entity_extension()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_kind text; actual_kind text;
        BEGIN
            expected_kind := CASE TG_TABLE_NAME
                WHEN 'people' THEN 'person' WHEN 'locations' THEN 'location' ELSE NULL END;
            SELECT entity_kind INTO actual_kind FROM entities
             WHERE id = NEW.entity_id AND project_id = NEW.project_id
               AND casefile_id = NEW.casefile_id AND draft_id = NEW.draft_id;
            IF actual_kind IS NULL OR actual_kind <> expected_kind THEN
                RAISE EXCEPTION '% requires entity_kind %, got %',
                    TG_TABLE_NAME, expected_kind, COALESCE(actual_kind, '<missing>');
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_locations_entity_kind
        BEFORE INSERT OR UPDATE OF project_id, casefile_id, draft_id, entity_id
        ON locations FOR EACH ROW EXECUTE FUNCTION casefile_validate_entity_extension()
        """
    )


def _drop_task_tables() -> None:
    op.drop_index("ix_task_events_task_run_id_sequence_no", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_attempts_task_run_id_started_at", table_name="task_attempts")
    op.drop_table("task_attempts")
    op.drop_index("ix_task_runs_lease_expires_at", table_name="task_runs")
    op.drop_index("ix_task_runs_project_id_updated_at", table_name="task_runs")
    op.drop_index("ix_task_runs_status_created_at", table_name="task_runs")
    op.drop_table("task_runs")


def _drop_provider_and_brief_tables() -> None:
    op.drop_constraint(
        op.f("fk_drafts_brief_version_id_brief_versions"), "drafts", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_briefs_project_brief_current_version_brief_versions", "briefs", type_="foreignkey"
    )
    op.drop_index("ix_brief_versions_brief_id_confirmed_at", table_name="brief_versions")
    op.drop_table("brief_versions")
    op.drop_table("briefs")
    op.drop_index(
        "ix_user_provider_settings_user_id_updated_at", table_name="user_provider_settings"
    )
    op.drop_table("user_provider_settings")


def _drop_relationship_and_lock_tables() -> None:
    op.drop_index("ix_casefile_contract_refs_draft_target", table_name="casefile_contract_refs")
    op.drop_index(
        "ix_casefile_contract_refs_draft_source_path", table_name="casefile_contract_refs"
    )
    op.drop_table("casefile_contract_refs")
    op.drop_index("ix_structure_locks_draft_id_type", table_name="structure_locks")
    op.drop_table("structure_locks")
    op.drop_index("ix_relationships_draft_id_type", table_name="relationships")
    op.drop_table("relationships")


def _restore_reasoning_tables() -> None:
    # The previous schema can represent only one ResolutionSpec per Draft.
    # Preserve the oldest row deterministically and remove dependent slots for
    # newer rows before restoring that legacy unique constraint.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY draft_id ORDER BY id) AS ordinal
              FROM resolution_specs
        )
        DELETE FROM resolution_slots
         WHERE resolution_spec_id IN (
             SELECT id FROM ranked WHERE ordinal > 1
         )
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY draft_id ORDER BY id) AS ordinal
              FROM resolution_specs
        )
        DELETE FROM resolution_specs
         WHERE id IN (
             SELECT id FROM ranked WHERE ordinal > 1
         )
        """
    )
    for name in ("statement_not_blank", "title_not_blank"):
        op.drop_constraint(
            op.f(f"ck_casefile_constraints_{name}"), "casefile_constraints", type_="check"
        )
    for name in ("rule_expression", "statement", "title"):
        op.drop_column("casefile_constraints", name)
    op.drop_constraint(
        op.f("ck_resolution_slots_value_type_allowed"), "resolution_slots", type_="check"
    )
    op.drop_column("resolution_slots", "value_type")
    for name in (
        "fairness_requirements_is_array",
        "accepted_answer_texts_is_object",
        "conclusion_mode_allowed",
        "title_not_blank",
    ):
        op.drop_constraint(op.f(f"ck_resolution_specs_{name}"), "resolution_specs", type_="check")
    for name in (
        "fairness_requirements_jsonb",
        "accepted_answer_texts_jsonb",
        "conclusion_mode",
        "title",
    ):
        op.drop_column("resolution_specs", name)
    op.create_unique_constraint("uq_resolution_specs_draft_id", "resolution_specs", ["draft_id"])
    op.drop_constraint(
        op.f("ck_reasoning_paths_reasoning_type_allowed"), "reasoning_paths", type_="check"
    )
    op.execute(
        """
        UPDATE reasoning_paths
           SET reasoning_type = 'mixed'
         WHERE reasoning_type NOT IN ('deductive', 'inductive', 'abductive', 'mixed')
        """
    )
    op.create_check_constraint(
        op.f("ck_reasoning_paths_reasoning_type_allowed"),
        "reasoning_paths",
        "reasoning_type IN ('deductive', 'inductive', 'abductive', 'mixed')",
    )
    op.drop_column("reasoning_paths", "required_for_resolution")
    op.drop_constraint(op.f("ck_hypotheses_status_allowed"), "hypotheses", type_="check")
    op.execute(
        """
        UPDATE hypotheses
           SET status = CASE
               WHEN status = 'accepted' THEN 'supported'
               WHEN status IN ('rejected', 'eliminated') THEN 'refuted'
               WHEN status = 'undetermined' THEN 'active'
               ELSE status
           END
         WHERE status NOT IN ('draft', 'active', 'supported', 'refuted', 'discarded')
        """
    )
    op.create_check_constraint(
        op.f("ck_hypotheses_status_allowed"),
        "hypotheses",
        "status IN ('draft', 'active', 'supported', 'refuted', 'discarded')",
    )


def _restore_content_tables() -> None:
    for name in ("materiality_allowed", "claim_type_allowed", "title_not_blank"):
        op.drop_constraint(op.f(f"ck_claims_{name}"), "claims", type_="check")
    op.drop_constraint(op.f("ck_claims_status_allowed"), "claims", type_="check")
    op.execute(
        """
        UPDATE claims
           SET status = 'unresolved'
         WHERE status NOT IN ('unresolved', 'supported', 'refuted', 'disputed')
        """
    )
    op.create_check_constraint(
        op.f("ck_claims_status_allowed"),
        "claims",
        "status IN ('unresolved', 'supported', 'refuted', 'disputed')",
    )
    for name in ("materiality", "claim_type", "title"):
        op.drop_column("claims", name)

    for name in (
        "acquisition_conditions_is_array",
        "classification_allowed",
        "truth_status_allowed",
        "reliability_allowed",
    ):
        op.drop_constraint(op.f(f"ck_information_units_{name}"), "information_units", type_="check")
    op.drop_constraint(
        op.f("ck_information_units_information_kind_allowed"), "information_units", type_="check"
    )
    op.execute(
        "ALTER TABLE information_units DISABLE TRIGGER trg_information_units_kind_immutable"
    )
    op.execute(
        """
        UPDATE information_units
           SET information_kind = 'other'
         WHERE information_kind NOT IN (
             'evidence', 'testimony', 'document', 'observation', 'clue', 'other'
         )
        """
    )
    op.execute(
        "ALTER TABLE information_units ENABLE TRIGGER trg_information_units_kind_immutable"
    )
    op.create_check_constraint(
        op.f("ck_information_units_information_kind_allowed"),
        "information_units",
        "information_kind IN ('evidence', 'testimony', 'document', 'observation', 'clue', 'other')",
    )
    for name in ("acquisition_conditions_jsonb", "classification", "truth_status", "reliability"):
        op.drop_column("information_units", name)

    op.drop_constraint(op.f("ck_events_time_is_object"), "events", type_="check")
    op.drop_constraint(op.f("ck_events_truth_status_allowed"), "events", type_="check")
    op.execute(
        """
        UPDATE events
           SET truth_status = CASE
               WHEN truth_status = 'canon_true' THEN 'true'
               WHEN truth_status = 'false_belief' THEN 'false'
               WHEN truth_status IN ('reported', 'unknown') THEN 'uncertain'
               ELSE truth_status
           END
         WHERE truth_status NOT IN ('true', 'false', 'uncertain', 'disputed')
        """
    )
    op.create_check_constraint(
        op.f("ck_events_truth_status_allowed"),
        "events",
        "truth_status IN ('true', 'false', 'uncertain', 'disputed')",
    )
    op.drop_column("events", "time_jsonb")

    op.execute("ALTER TABLE entities DISABLE TRIGGER trg_entities_registered_type")
    op.execute(
        """
        WITH inserted AS (
            INSERT INTO entities (
                project_id,
                casefile_id,
                draft_id,
                object_registry_id,
                entity_kind,
                name,
                description,
                traits_jsonb,
                attributes_jsonb
            )
            SELECT location.project_id,
                   location.casefile_id,
                   location.draft_id,
                   location.object_registry_id,
                   'location',
                   location.name,
                   object.description,
                   '[]'::jsonb,
                   '{}'::jsonb
              FROM locations AS location
              JOIN casefile_objects AS object
                ON object.id = location.object_registry_id
             WHERE location.entity_id IS NULL
            RETURNING id, object_registry_id
        )
        UPDATE locations AS location
           SET entity_id = inserted.id
          FROM inserted
         WHERE location.object_registry_id = inserted.object_registry_id
        """
    )
    op.execute("ALTER TABLE entities ENABLE TRIGGER trg_entities_registered_type")
    for name in ("visibility_rules_is_array", "access_rules_is_array", "name_not_blank"):
        op.drop_constraint(op.f(f"ck_locations_{name}"), "locations", type_="check")
    op.drop_constraint("uq_locations_object_registry_id", "locations", type_="unique")
    op.drop_constraint("fk_locations_object", "locations", type_="foreignkey")
    for name in ("visibility_rules_jsonb", "access_rules_jsonb", "name", "object_registry_id"):
        op.drop_column("locations", name)
    op.alter_column("locations", "entity_id", nullable=False)

    op.drop_constraint(op.f("ck_entities_entity_kind_allowed"), "entities", type_="check")
    op.execute("ALTER TABLE entities DISABLE TRIGGER trg_entities_kind_immutable")
    op.execute(
        """
        UPDATE entities
           SET entity_kind = CASE
               WHEN entity_kind = 'system' THEN 'object'
               WHEN entity_kind IN ('faction', 'rule_actor') THEN 'organization'
               ELSE 'other'
           END
         WHERE entity_kind NOT IN (
             'person', 'location', 'organization', 'object', 'concept', 'other'
         )
        """
    )
    op.execute("ALTER TABLE entities ENABLE TRIGGER trg_entities_kind_immutable")
    op.create_check_constraint(
        op.f("ck_entities_entity_kind_allowed"),
        "entities",
        "entity_kind IN ('person', 'location', 'organization', 'object', 'concept', 'other')",
    )
    for name in ("capabilities_jsonb", "secrets_jsonb", "goals_jsonb", "aliases_jsonb"):
        op.drop_column("entities", name)
    for name in (
        "completion_conditions_jsonb",
        "allowed_action_types_jsonb",
        "entry_conditions_jsonb",
    ):
        op.drop_constraint(
            op.f(f"ck_narrative_phases_{name.removesuffix('_jsonb')}_is_array"),
            "narrative_phases",
            type_="check",
        )
        op.drop_column("narrative_phases", name)


def _restore_casefile_foundation() -> None:
    op.execute("ALTER TABLE draft_operations DISABLE TRIGGER trg_draft_operations_immutable")
    op.execute(
        "DELETE FROM draft_operations WHERE operation_type = 'agent_generate_from_brief'"
    )
    op.execute(
        """
        DELETE FROM draft_operations
         WHERE casefile_object_id IN (
             SELECT object.id
               FROM casefile_objects AS object
              WHERE object.object_type IN ('relationship', 'structure_lock')
                 OR (
                     object.object_type = 'resolution_spec'
                     AND NOT EXISTS (
                         SELECT 1
                           FROM resolution_specs AS resolution
                          WHERE resolution.object_registry_id = object.id
                     )
                 )
         )
        """
    )
    op.execute("ALTER TABLE draft_operations ENABLE TRIGGER trg_draft_operations_immutable")
    op.drop_constraint(op.f("ck_draft_operations_type_allowed"), "draft_operations", type_="check")
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN ('add', 'remove', 'replace')",
    )
    op.alter_column(
        "draft_operations",
        "operation_type",
        existing_type=sa.String(64),
        type_=sa.String(16),
        existing_nullable=False,
    )
    for name in ("created_by_type_allowed", "tags_is_array", "contract_ordinal_positive"):
        op.drop_constraint(op.f(f"ck_casefile_objects_{name}"), "casefile_objects", type_="check")
    op.drop_constraint(
        op.f("ck_casefile_objects_object_type_allowed"), "casefile_objects", type_="check"
    )
    op.drop_constraint(
        "uq_casefile_objects_draft_type_ordinal", "casefile_objects", type_="unique"
    )
    op.execute(
        """
        DELETE FROM casefile_objects AS object
         WHERE object.object_type IN ('relationship', 'structure_lock')
            OR (
                object.object_type = 'resolution_spec'
                AND NOT EXISTS (
                    SELECT 1
                      FROM resolution_specs AS resolution
                     WHERE resolution.object_registry_id = object.id
                )
            )
        """
    )
    op.execute(
        "ALTER TABLE casefile_objects DISABLE TRIGGER trg_casefile_objects_identity_immutable"
    )
    op.execute(
        """
        UPDATE casefile_objects
           SET object_type = CASE
               WHEN object_type = 'phase' THEN 'narrative_phase'
               WHEN object_type = 'location' THEN 'entity'
               ELSE object_type
           END
         WHERE object_type IN ('phase', 'location')
        """
    )
    op.execute(
        "ALTER TABLE casefile_objects ENABLE TRIGGER trg_casefile_objects_identity_immutable"
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_object_type_allowed"),
        "casefile_objects",
        "object_type IN ('narrative_phase', 'entity', 'event', 'information_unit', 'claim', "
        "'hypothesis', 'reasoning_path', 'resolution_spec', 'constraint', 'knowledge_state')",
    )
    for name in (
        "contract_updated_at",
        "created_by_id",
        "created_by_type",
        "tags_jsonb",
        "description",
        "contract_ordinal",
    ):
        op.drop_column("casefile_objects", name)
    for name in (
        "extensions_is_object",
        "content_notices_is_array",
        "version_id_format",
        "version_no_positive",
    ):
        op.drop_constraint(op.f(f"ck_drafts_{name}"), "drafts", type_="check")
    for name in (
        "extensions_jsonb",
        "content_notices_jsonb",
        "brief_version_id",
        "parent_version_id",
        "version_no",
        "version_id",
    ):
        op.drop_column("drafts", name)
    op.drop_constraint(op.f("ck_casefiles_object_id_format"), "casefiles", type_="check")
    op.drop_constraint("uq_casefiles_object_id", "casefiles", type_="unique")
    op.drop_column("casefiles", "object_id")


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def _lineage_columns() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
    )


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _draft_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id"],
        ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _object_fk(column: str, name: str, *, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["project_id", "casefile_id", "draft_id", column],
        [
            "casefile_objects.project_id",
            "casefile_objects.casefile_id",
            "casefile_objects.draft_id",
            "casefile_objects.id",
        ],
        name=name,
        ondelete=ondelete,
    )
