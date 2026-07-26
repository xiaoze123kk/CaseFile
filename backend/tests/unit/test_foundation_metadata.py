"""Static contracts for the 28-table personal-product database metadata."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from casefile.data_postgres import models
from casefile.data_postgres.base import Base
from sqlalchemy.dialects import postgresql

EXPECTED_TABLES = {
    "audit_events",
    "canon_versions",
    "casefile_constraints",
    "casefile_objects",
    "casefile_refs",
    "casefiles",
    "claims",
    "draft_operations",
    "draft_snapshots",
    "drafts",
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
    "projects",
    "reasoning_edges",
    "reasoning_nodes",
    "reasoning_paths",
    "resolution_slots",
    "resolution_specs",
    "testimonies",
    "users",
}

DEDICATED_CURRENT_TABLES = {
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
    "resolution_slots",
    "resolution_specs",
    "testimonies",
}

JSONB_ALLOWLIST = {
    ("audit_events", "details_jsonb"),
    ("canon_versions", "content_jsonb"),
    ("casefile_constraints", "rule_jsonb"),
    ("casefile_objects", "source_jsonb"),
    ("casefile_refs", "metadata_jsonb"),
    ("draft_operations", "new_value_jsonb"),
    ("draft_operations", "old_value_jsonb"),
    ("draft_snapshots", "snapshot_jsonb"),
    ("entities", "attributes_jsonb"),
    ("entities", "traits_jsonb"),
    ("events", "end_time_jsonb"),
    ("events", "start_time_jsonb"),
    ("hypotheses", "exclusion_rule_jsonb"),
    ("locations", "geo_jsonb"),
    ("locations", "movement_rules_jsonb"),
    ("narrative_phases", "release_rule_jsonb"),
    ("projects", "profile_jsonb"),
    ("reasoning_edges", "attributes_jsonb"),
    ("reasoning_nodes", "attributes_jsonb"),
    ("resolution_slots", "value_jsonb"),
    ("resolution_specs", "conclusion_pattern_jsonb"),
}

EXPECTED_UNIQUES = {
    "uq_casefiles_project_id",
    "uq_drafts_project_id_casefile_id",
    "uq_casefile_objects_casefile_id_object_id",
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
    "uq_resolution_specs_draft_id",
    "uq_resolution_slots_spec_key",
    "uq_draft_snapshots_draft_id_snapshot_revision",
    "uq_canon_versions_source_snapshot_id",
}

EXPECTED_FOREIGN_KEYS = {
    "fk_casefile_objects_project_casefile_draft_drafts",
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
}


def _constraint_names(constraint_type: type[sa.Constraint]) -> set[str]:
    return {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name is not None
    }


def test_metadata_contains_exactly_the_28_personal_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert set(models.__all__) == {table.class_.__name__ for table in Base.registry.mappers}
    assert len(models.__all__) == 28

    all_column_names = {
        column.name for table in Base.metadata.tables.values() for column in table.columns
    }
    forbidden_fragments = ("workspace", "membership", "team_role", "public_id")
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

    for table_name in EXPECTED_TABLES - {"users", "projects"}:
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

    local_field_doc = repository_root / "docs" / "基座数据库表字段说明.md"
    if local_field_doc.exists():
        content = local_field_doc.read_text(encoding="utf-8")
        for table_name in EXPECTED_TABLES:
            assert f"`{table_name}`" in content
