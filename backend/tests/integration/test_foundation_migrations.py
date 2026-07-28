"""Disposable PostgreSQL verification for the 37-table personal foundation."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine, make_url

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_TABLES = {
    "audit_events",
    "brief_versions",
    "briefs",
    "canon_versions",
    "casefile_constraints",
    "casefile_contract_refs",
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
    "relationships",
    "resolution_slots",
    "resolution_specs",
    "structure_locks",
    "task_attempts",
    "task_events",
    "task_runs",
    "testimonies",
    "user_provider_settings",
    "users",
}


@dataclass(frozen=True)
class Lineage:
    owner_id: int
    project_id: int
    casefile_id: int
    draft_id: int


def _test_database_url() -> str:
    database_url = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.fail(
            "Refusing destructive migrations: CASEFILE_TEST_DATABASE_URL database name "
            "must end in _test"
        )
    return database_url


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    try:
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            command.downgrade(config, "base")
            assert set(sa.inspect(engine).get_table_names()) <= {"alembic_version"}
            command.upgrade(config, "head")
            assert set(sa.inspect(engine).get_table_names()) == BUSINESS_TABLES | {
                "alembic_version"
            }
            command.downgrade(config, "base")
            assert set(sa.inspect(engine).get_table_names()) <= {"alembic_version"}
            command.upgrade(config, "head")
            command.check(config)
        yield engine
    finally:
        engine.dispose()
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            command.downgrade(config, "base")


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        try:
            yield database_connection
        finally:
            if transaction.is_active:
                transaction.rollback()


@contextmanager
def _expect_database_error(connection: Connection) -> Iterator[None]:
    with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
        yield


def _insert_user(connection: Connection, label: str) -> int:
    return int(
        connection.execute(
            sa.text("INSERT INTO users (display_name) VALUES (:label) RETURNING id"),
            {"label": label},
        ).scalar_one()
    )


def _seed_lineage(connection: Connection, label: str) -> Lineage:
    owner_id = _insert_user(connection, f"Owner {label}")
    project_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO projects (owner_user_id, title)
                VALUES (:owner_id, :title) RETURNING id
                """
            ),
            {"owner_id": owner_id, "title": f"Project {label}"},
        ).scalar_one()
    )
    casefile_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO casefiles (project_id, object_id, title, schema_version)
                VALUES (:project_id, 'case_test_' || :project_id, :title, '1.0') RETURNING id
                """
            ),
            {"project_id": project_id, "title": f"CaseFile {label}"},
        ).scalar_one()
    )
    draft_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO drafts (project_id, casefile_id, version_id, schema_version)
                VALUES (
                    :project_id, :casefile_id, 'draft_test_' || :project_id, '1.0'
                ) RETURNING id
                """
            ),
            {"project_id": project_id, "casefile_id": casefile_id},
        ).scalar_one()
    )
    return Lineage(owner_id, project_id, casefile_id, draft_id)


def _insert_object(
    connection: Connection,
    lineage: Lineage,
    object_id: str,
    object_type: str,
) -> int:
    return int(
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_objects (
                    project_id, casefile_id, draft_id, object_id, object_type,
                    contract_ordinal, created_by_id, contract_updated_at,
                    confirmation_status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_id, :object_type,
                    (
                        SELECT COALESCE(MAX(contract_ordinal), 0) + 1
                          FROM casefile_objects
                         WHERE draft_id = :draft_id AND object_type = :ordinal_type
                    ),
                    'user_test', CURRENT_TIMESTAMP::text, 'user_confirmed'
                ) RETURNING id
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "object_id": object_id,
                "object_type": object_type,
                "ordinal_type": object_type,
            },
        ).scalar_one()
    )


def _core_values(lineage: Lineage, object_registry_id: int) -> dict[str, int]:
    return {
        "project_id": lineage.project_id,
        "casefile_id": lineage.casefile_id,
        "draft_id": lineage.draft_id,
        "object_registry_id": object_registry_id,
    }


def _insert_snapshot(
    connection: Connection,
    lineage: Lineage,
    *,
    revision: int,
    content_hash: str,
    content: str = '{"casefile": {}}',
    schema_version: str = "1.0",
    creator_id: int | None = None,
) -> int:
    return int(
        connection.execute(
            sa.text(
                """
                INSERT INTO draft_snapshots (
                    project_id, casefile_id, draft_id, snapshot_revision,
                    schema_version, snapshot_jsonb, content_hash, created_by_user_id
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :revision,
                    :schema_version, CAST(:content AS jsonb), :content_hash, :creator_id
                ) RETURNING id
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "revision": revision,
                "schema_version": schema_version,
                "content": content,
                "content_hash": content_hash,
                "creator_id": creator_id or lineage.owner_id,
            },
        ).scalar_one()
    )


def _insert_canon(
    connection: Connection,
    lineage: Lineage,
    snapshot_id: int,
    *,
    version_no: int = 1,
    parent_id: int | None = None,
    content_hash: str = "a" * 64,
    content: str = '{"casefile": {}}',
    schema_version: str = "1.0",
    confirmer_id: int | None = None,
) -> int:
    return int(
        connection.execute(
            sa.text(
                """
                INSERT INTO canon_versions (
                    project_id, casefile_id, parent_canon_version_id,
                    source_snapshot_id, version_no, schema_version,
                    content_jsonb, content_hash, confirmed_by_user_id
                ) VALUES (
                    :project_id, :casefile_id, :parent_id,
                    :snapshot_id, :version_no, :schema_version,
                    CAST(:content AS jsonb), :content_hash, :confirmer_id
                ) RETURNING id
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "parent_id": parent_id,
                "snapshot_id": snapshot_id,
                "version_no": version_no,
                "schema_version": schema_version,
                "content": content,
                "content_hash": content_hash,
                "confirmer_id": confirmer_id or lineage.owner_id,
            },
        ).scalar_one()
    )


def test_database_has_37_identity_tables_without_team_columns(
    connection: Connection,
) -> None:
    identity_rows = connection.execute(
        sa.text(
            """
            SELECT table_name, data_type, is_identity, identity_generation
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'id'
              AND table_name <> 'alembic_version'
            ORDER BY table_name
            """
        )
    ).all()
    assert len(identity_rows) == 37
    assert all(row[1:] == ("bigint", "YES", "BY DEFAULT") for row in identity_rows)

    columns = connection.execute(
        sa.text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
    ).all()
    column_pairs = set(columns)
    flat_names = {name for row in columns for name in row}
    assert ("casefile_objects", "payload_jsonb") not in column_pairs
    assert not any("workspace" in name or "membership" in name for name in flat_names)


def test_registry_revision_confidence_status_and_stable_id_checks(
    connection: Connection,
) -> None:
    lineage = _seed_lineage(connection, "registry-checks")
    object_id = _insert_object(connection, lineage, "event_unique", "event")
    assert object_id > 0
    base_parameters = {
        "project_id": lineage.project_id,
        "casefile_id": lineage.casefile_id,
        "draft_id": lineage.draft_id,
    }
    with _expect_database_error(connection):
        _insert_object(connection, lineage, "event_unique", "event")
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_objects (
                    project_id, casefile_id, draft_id, object_id, object_type,
                    revision, confirmation_status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, 'event_bad_revision',
                    'event', 0, 'user_confirmed'
                )
                """
            ),
            base_parameters,
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_objects (
                    project_id, casefile_id, draft_id, object_id, object_type,
                    confidence, confirmation_status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, 'event_bad_confidence',
                    'event', 1.1, 'user_confirmed'
                )
                """
            ),
            base_parameters,
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_objects (
                    project_id, casefile_id, draft_id, object_id, object_type,
                    confirmation_status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, 'event_bad_status',
                    'event', 'team_approved'
                )
                """
            ),
            base_parameters,
        )


def test_registered_types_subtypes_and_single_value_lineage_are_enforced(
    connection: Connection,
) -> None:
    first = _seed_lineage(connection, "types-a")
    second = _seed_lineage(connection, "types-b")

    person_object = _insert_object(connection, first, "entity_person", "entity")
    person_entity = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO entities (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_kind, name
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'person', 'Witness'
                ) RETURNING id
                """
            ),
            _core_values(first, person_object),
        ).scalar_one()
    )
    person_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO people (project_id, casefile_id, draft_id, entity_id)
                VALUES (:project_id, :casefile_id, :draft_id, :entity_id) RETURNING id
                """
            ),
            {**_core_values(first, person_object), "entity_id": person_entity},
        ).scalar_one()
    )

    wrong_object = _insert_object(connection, first, "event_wrong_entity", "event")
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO entities (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_kind, name
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'person', 'Wrong'
                )
                """
            ),
            _core_values(first, wrong_object),
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO locations (project_id, casefile_id, draft_id, entity_id)
                VALUES (:project_id, :casefile_id, :draft_id, :entity_id)
                """
            ),
            {**_core_values(first, person_object), "entity_id": person_entity},
        )

    info_object = _insert_object(connection, first, "info_evidence", "information_unit")
    info_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO information_units (
                    project_id, casefile_id, draft_id, object_registry_id,
                    information_kind, title, body_text
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'evidence', 'Receipt', 'A dated receipt'
                ) RETURNING id
                """
            ),
            _core_values(first, info_object),
        ).scalar_one()
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO testimonies (
                    project_id, casefile_id, draft_id, information_unit_id,
                    speaker_person_id, quote_text
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :information_unit_id,
                    :speaker_person_id, 'Wrong subtype'
                )
                """
            ),
            {
                **_core_values(first, info_object),
                "information_unit_id": info_id,
                "speaker_person_id": person_id,
            },
        )

    location_object = _insert_object(connection, first, "entity_location", "entity")
    location_entity = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO entities (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_kind, name
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'location', 'Station'
                ) RETURNING id
                """
            ),
            _core_values(first, location_object),
        ).scalar_one()
    )
    location_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO locations (project_id, casefile_id, draft_id, entity_id)
                VALUES (:project_id, :casefile_id, :draft_id, :entity_id) RETURNING id
                """
            ),
            {**_core_values(first, location_object), "entity_id": location_entity},
        ).scalar_one()
    )
    second_event_object = _insert_object(connection, second, "event_cross_location", "event")
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO events (
                    project_id, casefile_id, draft_id, object_registry_id,
                    title, narrative_order, location_id
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'Cross', 1, :location_id
                )
                """
            ),
            {**_core_values(second, second_event_object), "location_id": location_id},
        )


def test_known_reference_endpoints_ordinal_and_uniqueness(connection: Connection) -> None:
    lineage = _seed_lineage(connection, "refs")
    other_lineage = _seed_lineage(connection, "refs-other")
    event_object = _insert_object(connection, lineage, "event_ref", "event")
    entity_object = _insert_object(connection, lineage, "entity_actor", "entity")
    claim_object = _insert_object(connection, lineage, "claim_ref", "claim")

    connection.execute(
        sa.text(
            """
            INSERT INTO casefile_refs (
                project_id, casefile_id, draft_id, from_object_id, to_object_id,
                field_path, ref_kind, ordinal
            ) VALUES (
                :project_id, :casefile_id, :draft_id, :from_id, :to_id,
                '/actors', 'event_actor', 1
            )
            """
        ),
        {
            "project_id": lineage.project_id,
            "casefile_id": lineage.casefile_id,
            "draft_id": lineage.draft_id,
            "from_id": event_object,
            "to_id": entity_object,
        },
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_refs (
                    project_id, casefile_id, draft_id, from_object_id, to_object_id,
                    field_path, ref_kind, ordinal
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :from_id, :to_id,
                    '/actors', 'event_actor', 2
                )
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "from_id": claim_object,
                "to_id": entity_object,
            },
        )
    foreign_entity = _insert_object(connection, other_lineage, "entity_foreign", "entity")
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_refs (
                    project_id, casefile_id, draft_id, from_object_id, to_object_id,
                    field_path, ref_kind, ordinal
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :from_id, :to_id,
                    '/actors', 'event_actor', 2
                )
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "from_id": event_object,
                "to_id": foreign_entity,
            },
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_refs (
                    project_id, casefile_id, draft_id, from_object_id, to_object_id,
                    field_path, ref_kind, ordinal
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :from_id, :to_id,
                    '/actors', 'event_actor', 1
                )
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "from_id": event_object,
                "to_id": claim_object,
            },
        )


def test_phase_resolution_knowledge_and_reasoning_uniqueness(connection: Connection) -> None:
    lineage = _seed_lineage(connection, "content-rules")
    phase_object = _insert_object(connection, lineage, "phase_opening", "narrative_phase")
    phase_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO narrative_phases (
                    project_id, casefile_id, draft_id, object_registry_id,
                    name, phase_order
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'Opening', 1
                ) RETURNING id
                """
            ),
            _core_values(lineage, phase_object),
        ).scalar_one()
    )
    duplicate_phase_object = _insert_object(
        connection, lineage, "phase_duplicate", "narrative_phase"
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO narrative_phases (
                    project_id, casefile_id, draft_id, object_registry_id,
                    name, phase_order
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'Duplicate', 1
                )
                """
            ),
            _core_values(lineage, duplicate_phase_object),
        )

    spec_object = _insert_object(connection, lineage, "resolution_main", "resolution_spec")
    spec_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO resolution_specs (
                    project_id, casefile_id, draft_id, object_registry_id,
                    question_type, target_question
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'culprit', 'Who did it?'
                ) RETURNING id
                """
            ),
            _core_values(lineage, spec_object),
        ).scalar_one()
    )
    duplicate_spec_object = _insert_object(
        connection, lineage, "resolution_duplicate", "resolution_spec"
    )
    second_spec_id = connection.execute(
        sa.text(
            """
            INSERT INTO resolution_specs (
                project_id, casefile_id, draft_id, object_registry_id,
                question_type, target_question
            ) VALUES (
                :project_id, :casefile_id, :draft_id, :object_registry_id,
                'motive', 'Why?'
            ) RETURNING id
            """
        ),
        _core_values(lineage, duplicate_spec_object),
    ).scalar_one()
    assert second_spec_id != spec_id
    connection.execute(
        sa.text(
            """
            INSERT INTO resolution_slots (
                project_id, casefile_id, draft_id, resolution_spec_id,
                slot_key, label, is_required, ordinal
            ) VALUES (
                :project_id, :casefile_id, :draft_id, :spec_id,
                'culprit_id', 'Culprit', true, 1
            )
            """
        ),
        {**_core_values(lineage, spec_object), "spec_id": spec_id},
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO resolution_slots (
                    project_id, casefile_id, draft_id, resolution_spec_id,
                    slot_key, label, ordinal
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :spec_id,
                    'culprit_id', 'Duplicate', 2
                )
                """
            ),
            {**_core_values(lineage, spec_object), "spec_id": spec_id},
        )

    entity_object = _insert_object(connection, lineage, "entity_knower", "entity")
    entity_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO entities (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_kind, name
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'person', 'Knower'
                ) RETURNING id
                """
            ),
            _core_values(lineage, entity_object),
        ).scalar_one()
    )
    state_object = _insert_object(connection, lineage, "knowledge_knower_open", "knowledge_state")
    state_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_states (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_id, narrative_phase_id, status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    :entity_id, :phase_id, 'partial'
                ) RETURNING id
                """
            ),
            {
                **_core_values(lineage, state_object),
                "entity_id": entity_id,
                "phase_id": phase_id,
            },
        ).scalar_one()
    )
    duplicate_state_object = _insert_object(
        connection, lineage, "knowledge_knower_duplicate", "knowledge_state"
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_states (
                    project_id, casefile_id, draft_id, object_registry_id,
                    entity_id, narrative_phase_id, status
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    :entity_id, :phase_id, 'known'
                )
                """
            ),
            {
                **_core_values(lineage, duplicate_state_object),
                "entity_id": entity_id,
                "phase_id": phase_id,
            },
        )
    knowledge_info_object = _insert_object(
        connection, lineage, "info_knowledge", "information_unit"
    )
    knowledge_info_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO information_units (
                    project_id, casefile_id, draft_id, object_registry_id,
                    information_kind, title, body_text
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_registry_id,
                    'observation', 'Observed', 'Something was observed'
                ) RETURNING id
                """
            ),
            _core_values(lineage, knowledge_info_object),
        ).scalar_one()
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_state_entries (
                    project_id, casefile_id, draft_id, knowledge_state_id,
                    information_unit_id, cognition_status, disclosure_status, ordinal
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :state_id,
                    :information_id, 'guessed', 'revealed', 1
                )
                """
            ),
            {
                **_core_values(lineage, knowledge_info_object),
                "state_id": state_id,
                "information_id": knowledge_info_id,
            },
        )

    first_path_object = _insert_object(connection, lineage, "reasoning_path_one", "reasoning_path")
    second_path_object = _insert_object(connection, lineage, "reasoning_path_two", "reasoning_path")
    path_ids = []
    for ordinal, object_id in enumerate((first_path_object, second_path_object), start=1):
        path_ids.append(
            int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO reasoning_paths (
                            project_id, casefile_id, draft_id, object_registry_id,
                            name, reasoning_type
                        ) VALUES (
                            :project_id, :casefile_id, :draft_id, :object_registry_id,
                            :name, 'deductive'
                        ) RETURNING id
                        """
                    ),
                    {**_core_values(lineage, object_id), "name": f"Path {ordinal}"},
                ).scalar_one()
            )
        )
    node_ids = []
    for path_id, node_key in zip(path_ids, ("node_one", "node_two"), strict=True):
        node_ids.append(
            int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO reasoning_nodes (
                            project_id, casefile_id, draft_id, reasoning_path_id,
                            node_key, ordinal, node_type, statement
                        ) VALUES (
                            :project_id, :casefile_id, :draft_id, :path_id,
                            :node_key, 1, 'claim', 'Statement'
                        ) RETURNING id
                        """
                    ),
                    {
                        **_core_values(lineage, first_path_object),
                        "path_id": path_id,
                        "node_key": node_key,
                    },
                ).scalar_one()
            )
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO reasoning_edges (
                    project_id, casefile_id, draft_id, reasoning_path_id,
                    from_node_id, to_node_id, argument_kind
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :path_id,
                    :from_node_id, :to_node_id, 'supports'
                )
                """
            ),
            {
                **_core_values(lineage, first_path_object),
                "path_id": path_ids[0],
                "from_node_id": node_ids[0],
                "to_node_id": node_ids[1],
            },
        )


def test_concurrent_draft_operations_use_optimistic_revision(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as seed_connection:
        lineage = _seed_lineage(seed_connection, "operation-race")
        object_id = _insert_object(seed_connection, lineage, "event_operation_race", "event")

    start = Barrier(2)

    def attempt() -> bool:
        try:
            with migrated_engine.begin() as operation_connection:
                start.wait(timeout=10)
                operation_connection.execute(
                    sa.text(
                        """
                        INSERT INTO draft_operations (
                            project_id, casefile_id, draft_id, casefile_object_id,
                            sequence_no, operation_group_no, operation_type, field_path,
                            base_revision, result_revision, actor_kind, actor_user_id
                        ) VALUES (
                            :project_id, :casefile_id, :draft_id, :object_id,
                            1, 1, 'replace', '/title', 1, 2, 'user', :owner_id
                        )
                        """
                    ),
                    {
                        "project_id": lineage.project_id,
                        "casefile_id": lineage.casefile_id,
                        "draft_id": lineage.draft_id,
                        "object_id": object_id,
                        "owner_id": lineage.owner_id,
                    },
                )
            return True
        except sa.exc.DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=20)
            for future in (executor.submit(attempt), executor.submit(attempt))
        ]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


def test_snapshot_canon_gates_pointers_audit_and_immutability(connection: Connection) -> None:
    lineage = _seed_lineage(connection, "canon")
    outsider_id = _insert_user(connection, "Outsider")
    with _expect_database_error(connection):
        _insert_snapshot(connection, lineage, revision=2, content_hash="a" * 64)
    with _expect_database_error(connection):
        _insert_snapshot(connection, lineage, revision=1, content_hash="not-a-sha256")
    with _expect_database_error(connection):
        _insert_snapshot(
            connection,
            lineage,
            revision=1,
            content_hash="a" * 64,
            creator_id=outsider_id,
        )
    snapshot_id = _insert_snapshot(
        connection,
        lineage,
        revision=1,
        content_hash="a" * 64,
    )
    with _expect_database_error(connection):
        _insert_canon(connection, lineage, snapshot_id, content_hash="b" * 64)
    with _expect_database_error(connection):
        _insert_canon(
            connection,
            lineage,
            snapshot_id,
            version_no=2,
            content_hash="a" * 64,
        )
    with _expect_database_error(connection):
        _insert_canon(
            connection,
            lineage,
            snapshot_id,
            content_hash="a" * 64,
            schema_version="2.4",
        )
    canon_id = _insert_canon(connection, lineage, snapshot_id)

    pointers = connection.execute(
        sa.text(
            """
            SELECT cf.current_canon_version_id, d.base_canon_version_id
            FROM casefiles AS cf
            JOIN drafts AS d ON d.casefile_id = cf.id AND d.project_id = cf.project_id
            WHERE cf.id = :casefile_id
            """
        ),
        {"casefile_id": lineage.casefile_id},
    ).one()
    assert pointers == (canon_id, canon_id)
    assert (
        connection.execute(
            sa.text(
                """
            SELECT COUNT(*) FROM audit_events
            WHERE casefile_id = :casefile_id AND action = 'canon.created'
            """
            ),
            {"casefile_id": lineage.casefile_id},
        ).scalar_one()
        == 1
    )
    audit_id = int(
        connection.execute(
            sa.text(
                """
                SELECT id FROM audit_events
                WHERE casefile_id = :casefile_id AND action = 'canon.created'
                """
            ),
            {"casefile_id": lineage.casefile_id},
        ).scalar_one()
    )

    operation_object = _insert_object(connection, lineage, "event_immutable", "event")
    operation_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO draft_operations (
                    project_id, casefile_id, draft_id, casefile_object_id,
                    sequence_no, operation_group_no, operation_type, field_path,
                    base_revision, result_revision, actor_kind, actor_user_id
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :object_id,
                    1, 1, 'add', '', 1, 2, 'user', :owner_id
                ) RETURNING id
                """
            ),
            {
                "project_id": lineage.project_id,
                "casefile_id": lineage.casefile_id,
                "draft_id": lineage.draft_id,
                "object_id": operation_object,
                "owner_id": lineage.owner_id,
            },
        ).scalar_one()
    )
    immutable_rows = (
        ("draft_operations", operation_id),
        ("draft_snapshots", snapshot_id),
        ("canon_versions", canon_id),
        ("audit_events", audit_id),
    )
    for table_name, row_id in immutable_rows:
        with _expect_database_error(connection):
            connection.execute(
                sa.text(f"UPDATE {table_name} SET id = id WHERE id = :row_id"),
                {"row_id": row_id},
            )
        with _expect_database_error(connection):
            connection.execute(
                sa.text(f"DELETE FROM {table_name} WHERE id = :row_id"),
                {"row_id": row_id},
            )


def test_concurrent_first_canon_confirmation_commits_once(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as seed_connection:
        lineage = _seed_lineage(seed_connection, "canon-race")
        snapshot_id = _insert_snapshot(
            seed_connection,
            lineage,
            revision=1,
            content_hash="a" * 64,
        )
    start = Barrier(2)

    def attempt() -> bool:
        try:
            with migrated_engine.begin() as canon_connection:
                start.wait(timeout=10)
                _insert_canon(canon_connection, lineage, snapshot_id)
            return True
        except sa.exc.DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=20)
            for future in (executor.submit(attempt), executor.submit(attempt))
        ]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
