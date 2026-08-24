"""Static contracts for the 64-table personal-product database metadata."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from casefile.data_postgres import models
from casefile.data_postgres.base import Base
from sqlalchemy.dialects import postgresql

EXPECTED_TABLES = {
    "agent_model_calls",
    "agent_messages",
    "agent_patch_operations",
    "agent_patch_sets",
    "agent_threads",
    "agent_step_runs",
    "agent_thread_context_states",
    "audit_events",
    "brief_intake_candidates",
    "brief_intake_questions",
    "brief_intakes",
    "brief_versions",
    "briefs",
    "canon_versions",
    "casefile_constraints",
    "casefile_contract_refs",
    "casefile_objects",
    "casefile_refs",
    "casefiles",
    "claims",
    "compile_artifacts",
    "compile_runs",
    "compiler_profile_versions",
    "compiler_profiles",
    "draft_operations",
    "draft_snapshots",
    "drafts",
    "entities",
    "events",
    "evidence_items",
    "exposure_plan_entries",
    "exposure_plan_entry_refs",
    "exposure_plan_revisions",
    "exposure_plans",
    "hypotheses",
    "idea_candidates",
    "imported_documents",
    "information_units",
    "knowledge_state_entries",
    "knowledge_states",
    "locations",
    "narrative_phases",
    "parse_items",
    "people",
    "projects",
    "reasoning_edges",
    "reasoning_nodes",
    "reasoning_paths",
    "relationships",
    "resolution_slots",
    "resolution_specs",
    "source_records",
    "structure_locks",
    "task_attempts",
    "task_events",
    "task_runs",
    "testimonies",
    "user_provider_settings",
    "users",
    "verification_finding_patch_operations",
    "verification_finding_refs",
    "verification_finding_reviews",
    "verification_findings",
    "verification_runs",
}

DEDICATED_CURRENT_TABLES = {
    "agent_patch_operations",
    "agent_patch_sets",
    "agent_threads",
    "casefile_constraints",
    "claims",
    "entities",
    "events",
    "evidence_items",
    "hypotheses",
    "information_units",
    "knowledge_state_entries",
    "knowledge_states",
    "locations",
    "narrative_phases",
    "people",
    "reasoning_edges",
    "reasoning_nodes",
    "reasoning_paths",
    "relationships",
    "resolution_slots",
    "resolution_specs",
    "structure_locks",
    "testimonies",
    "verification_findings",
    "verification_runs",
}

JSONB_ALLOWLIST = {
    ("agent_model_calls", "issues_jsonb"),
    ("agent_model_calls", "usage_jsonb"),
    ("agent_patch_operations", "new_value_jsonb"),
    ("agent_patch_operations", "old_value_jsonb"),
    ("agent_patch_operations", "repair_obligation_keys"),
    ("agent_step_runs", "diagnostic_jsonb"),
    ("agent_step_runs", "output_jsonb"),
    ("agent_step_runs", "upstream_hashes_jsonb"),
    ("agent_step_runs", "usage_jsonb"),
    ("agent_thread_context_states", "state_jsonb"),
    ("audit_events", "details_jsonb"),
    ("brief_intake_candidates", "content_jsonb"),
    ("brief_intake_questions", "suggestions_jsonb"),
    ("brief_versions", "content_jsonb"),
    ("briefs", "draft_jsonb"),
    ("canon_versions", "content_jsonb"),
    ("compile_artifacts", "content_jsonb"),
    ("compiler_profile_versions", "payload_jsonb"),
    ("casefile_constraints", "rule_jsonb"),
    ("casefile_contract_refs", "metadata_jsonb"),
    ("casefile_objects", "source_jsonb"),
    ("casefile_objects", "tags_jsonb"),
    ("casefile_refs", "metadata_jsonb"),
    ("draft_operations", "new_value_jsonb"),
    ("draft_operations", "old_value_jsonb"),
    ("draft_snapshots", "snapshot_jsonb"),
    ("drafts", "content_notices_jsonb"),
    ("drafts", "extensions_jsonb"),
    ("entities", "aliases_jsonb"),
    ("entities", "attributes_jsonb"),
    ("entities", "capabilities_jsonb"),
    ("entities", "goals_jsonb"),
    ("entities", "secrets_jsonb"),
    ("entities", "traits_jsonb"),
    ("events", "end_time_jsonb"),
    ("events", "start_time_jsonb"),
    ("events", "time_jsonb"),
    ("verification_findings", "payload_jsonb"),
    ("hypotheses", "exclusion_rule_jsonb"),
    ("idea_candidates", "content_jsonb"),
    ("imported_documents", "blocks_jsonb"),
    ("information_units", "acquisition_conditions_jsonb"),
    ("locations", "access_rules_jsonb"),
    ("locations", "geo_jsonb"),
    ("locations", "movement_rules_jsonb"),
    ("locations", "visibility_rules_jsonb"),
    ("narrative_phases", "allowed_action_types_jsonb"),
    ("narrative_phases", "completion_conditions_jsonb"),
    ("narrative_phases", "entry_conditions_jsonb"),
    ("narrative_phases", "release_rule_jsonb"),
    ("parse_items", "content_jsonb"),
    ("parse_items", "source_block_refs"),
    ("projects", "profile_jsonb"),
    ("reasoning_edges", "attributes_jsonb"),
    ("reasoning_nodes", "attributes_jsonb"),
    ("resolution_slots", "value_jsonb"),
    ("resolution_specs", "accepted_answer_texts_jsonb"),
    ("resolution_specs", "conclusion_pattern_jsonb"),
    ("resolution_specs", "conclusion_unresolved_gaps_jsonb"),
    ("resolution_specs", "fairness_requirements_jsonb"),
    ("structure_locks", "field_paths_jsonb"),
    ("task_attempts", "candidate_jsonb"),
    ("task_attempts", "error_details_jsonb"),
    ("task_attempts", "usage_jsonb"),
    ("task_attempts", "validation_errors_jsonb"),
    ("task_events", "payload_jsonb"),
    ("task_runs", "budget_jsonb"),
    ("task_runs", "error_details_jsonb"),
    ("task_runs", "input_jsonb"),
    ("task_runs", "result_jsonb"),
    ("task_runs", "usage_jsonb"),
    ("user_provider_settings", "default_budget_jsonb"),
}

EXPECTED_UNIQUES = {
    "uq_agent_messages_project_id_id",
    "uq_agent_messages_thread_sequence_no",
    "uq_agent_patch_operations_patch_set_operation_id",
    "uq_agent_patch_operations_patch_set_ordinal",
    "uq_agent_patch_sets_project_id_id",
    "uq_agent_patch_sets_task_run_id",
    "uq_agent_threads_project_id_id",
    "uq_brief_intake_candidates_generated_task",
    "uq_brief_intake_candidates_lineage_id",
    "uq_brief_intake_questions_lineage_id",
    "uq_brief_intake_questions_task_ordinal",
    "uq_brief_intake_questions_task_question_key",
    "uq_brief_intakes_project_id",
    "uq_brief_intakes_project_id_id",
    "uq_casefiles_project_id",
    "uq_casefile_objects_draft_id_object_id",
    "uq_casefile_refs_source_ordinal",
    "uq_casefile_refs_target",
    "uq_entities_object_registry_id",
    "uq_people_entity_id",
    "uq_locations_entity_id",
    "uq_information_units_object_registry_id",
    "uq_evidence_items_information_unit_id",
    "uq_testimonies_information_unit_id",
    "uq_narrative_phases_draft_order",
    "uq_knowledge_states_entity_phase",
    "uq_knowledge_entries_state_information",
    "uq_reasoning_nodes_path_key",
    "uq_reasoning_edges_argument",
    "uq_resolution_slots_spec_key",
    "uq_source_records_project_id_id",
    "uq_draft_snapshots_draft_id_snapshot_revision",
    "uq_exposure_plan_entries_revision_entry_key",
    "uq_exposure_plan_entries_revision_sequence_no",
    "uq_exposure_plan_entry_refs_entry_object_registry",
    "uq_exposure_plan_revisions_plan_id_revision_no",
    "uq_exposure_plans_draft_id",
    "uq_canon_versions_source_snapshot_id",
    "uq_briefs_project_id",
    "uq_task_events_run_sequence_no",
    "uq_task_runs_project_id_id",
    "uq_user_provider_settings_user_provider",
}

EXPECTED_FOREIGN_KEYS = {
    "fk_agent_messages_project_thread_agent_threads",
    "fk_agent_patch_operations_project_patch_set_agent_patch_sets",
    "fk_agent_patch_operations_target_object",
    "fk_agent_patch_sets_project_casefile_draft_drafts",
    "fk_agent_patch_sets_project_source_message_agent_messages",
    "fk_agent_patch_sets_project_task_run_task_runs",
    "fk_agent_patch_sets_project_thread_agent_threads",
    "fk_agent_threads_project_casefile_draft_drafts",
    "fk_brief_intake_candidates_generated_task_task_runs",
    "fk_brief_intake_candidates_project_intake_brief_intakes",
    "fk_brief_intake_questions_generated_task_task_runs",
    "fk_brief_intake_questions_project_intake_brief_intakes",
    "fk_brief_intakes_current_candidate_brief_intake_candidates",
    "fk_brief_intakes_current_questions_task_task_runs",
    "fk_brief_intakes_project_current_source_source_records",
    "fk_casefiles_project_casefile_current_draft_drafts",
    "fk_casefile_objects_project_casefile_draft_drafts",
    "fk_exposure_plan_entries_lineage_revision_revisions",
    "fk_exposure_plan_entry_refs_lineage_entry_entries",
    "fk_exposure_plan_entry_refs_lineage_object_casefile_objects",
    "fk_exposure_plan_revisions_lineage_plan_exposure_plans",
    "fk_exposure_plans_lineage_current_revision_revisions",
    "fk_exposure_plans_project_casefile_draft_drafts",
    "fk_casefile_refs_from_object",
    "fk_casefile_refs_to_object",
    "fk_entities_object",
    "fk_people_entity",
    "fk_locations_entity",
    "fk_events_location",
    "fk_events_narrative_phase",
    "fk_information_units_visible_phase",
    "fk_evidence_items_source_event",
    "fk_testimonies_speaker_person",
    "fk_knowledge_states_entity",
    "fk_knowledge_states_phase",
    "fk_knowledge_state_entries_information",
    "fk_reasoning_nodes_source_object",
    "fk_reasoning_edges_from_node",
    "fk_reasoning_edges_to_node",
    "fk_casefile_constraints_target_object",
    "fk_source_records_project_generated_task_task_runs",
    "fk_source_records_project_parent_source_records",
    "fk_task_events_project_task_run_task_runs",
    "fk_task_runs_project_agent_thread_agent_threads",
    "fk_task_runs_project_brief_intake_brief_intakes",
    "fk_task_runs_project_brief_version_brief_versions",
    "fk_task_runs_project_casefile_draft_drafts",
    "fk_task_runs_project_input_source_source_records",
    "fk_task_runs_project_input_message_agent_messages",
    "fk_task_runs_project_output_message_agent_messages",
}


def _constraint_names(constraint_type: type[sa.Constraint]) -> set[str]:
    return {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name is not None
    }


def test_metadata_contains_exactly_the_64_personal_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert set(models.__all__) == {table.class_.__name__ for table in Base.registry.mappers}
    assert len(models.__all__) == 64

    all_column_names = {
        column.name for table in Base.metadata.tables.values() for column in table.columns
    }
    forbidden_fragments = ("workspace", "membership", "team_role")
    assert not any(
        fragment in name
        for fragment in forbidden_fragments
        for name in EXPECTED_TABLES | all_column_names
    )


def test_every_table_uses_bigint_identity_and_every_relation_key_is_bigint() -> None:
    for table in Base.metadata.tables.values():
        assert list(table.primary_key.columns.keys()) == ["id"]
        primary_key = table.c.id
        assert isinstance(primary_key.type, sa.BigInteger)
        assert primary_key.identity is not None
        assert primary_key.identity.always is False

        for column in table.columns:
            assert not isinstance(column.type, sa.Uuid)
        for foreign_key in table.foreign_keys:
            assert isinstance(foreign_key.parent.type, sa.BigInteger)

    for table_name in EXPECTED_TABLES - {"users", "projects", "user_provider_settings"}:
        assert "project_id" in Base.metadata.tables[table_name].c
    for table_name in DEDICATED_CURRENT_TABLES:
        assert {"project_id", "casefile_id", "draft_id"} <= set(
            Base.metadata.tables[table_name].c.keys()
        )


def test_registry_is_lightweight_and_jsonb_is_limited_to_the_allowlist() -> None:
    registry_columns = set(Base.metadata.tables["casefile_objects"].c.keys())
    assert "payload_jsonb" not in registry_columns
    assert {"object_id", "object_type", "source_jsonb", "revision"} <= registry_columns

    actual_jsonb = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, postgresql.JSONB)
    }
    assert actual_jsonb == JSONB_ALLOWLIST

    assert all(
        index.dialect_options["postgresql"].get("using") != "gin"
        for table in Base.metadata.tables.values()
        for index in table.indexes
    )


def test_core_unique_and_foreign_key_constraints_are_present() -> None:
    assert EXPECTED_UNIQUES <= _constraint_names(sa.UniqueConstraint)
    assert EXPECTED_FOREIGN_KEYS <= _constraint_names(sa.ForeignKeyConstraint)

    ref_table = Base.metadata.tables["casefile_refs"]
    assert {"ordinal", "metadata_jsonb"} <= set(ref_table.c.keys())
    assert all(
        not isinstance(column.type, sa.Enum)
        for table in Base.metadata.tables.values()
        for column in table.c
    )
    operation_checks = {
        str(constraint.sqltext)
        for constraint in Base.metadata.tables["draft_operations"].constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert any("agent_adopt_brief_candidate" in expression for expression in operation_checks)


def test_tracked_responsibility_docs_list_the_same_tables() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    tracked_docs = (
        repository_root / "AGENT.md",
        repository_root / "backend" / "migrations" / "README.md",
    )
    for document in tracked_docs:
        content = document.read_text(encoding="utf-8")
        for table_name in EXPECTED_TABLES:
            assert f"`{table_name}`" in content
