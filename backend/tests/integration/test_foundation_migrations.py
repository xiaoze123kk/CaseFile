"""Disposable PostgreSQL verification for the 42-table personal foundation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
import rfc8785
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine, make_url

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260728084832"
BUSINESS_TABLES = {
    "agent_messages",
    "agent_patch_operations",
    "agent_patch_sets",
    "agent_threads",
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
    "source_records",
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


@dataclass(frozen=True)
class MigrationCompatibilityIds:
    project_id: int
    brief_id: int
    brief_version_id: int
    task_run_id: int
    task_attempt_id: int
    snapshot_id: int
    canon_id: int
    legacy_casefile: dict[str, object]
    legacy_brief: dict[str, object]


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
            command.upgrade(config, PREVIOUS_REVISION)
            compatibility_ids = _seed_legacy_migration_documents(engine)
            command.upgrade(config, "head")
            first_forward = _assert_forward_migration_documents(
                engine,
                compatibility_ids,
            )
            modern_attempt_id, modern_document = _seed_modern_migration_candidate(
                engine,
                compatibility_ids,
            )
            command.downgrade(config, PREVIOUS_REVISION)
            _assert_reverse_migration_documents(engine, compatibility_ids)
            _assert_modern_candidate_downgrade(engine, modern_attempt_id)
            command.upgrade(config, "head")
            second_forward = _assert_forward_migration_documents(
                engine,
                compatibility_ids,
            )
            assert second_forward == first_forward
            _assert_task_attempt_document(
                engine,
                modern_attempt_id,
                modern_document,
            )
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


def _legacy_casefile_document() -> dict[str, object]:
    phase_ref = {"object_type": "phase", "object_id": "phase_investigation"}
    return {
        "schema_version": "1.0",
        "casefile_id": "case_migration_compat",
        "project_profile": {
            "content_type": "interactive_reasoning",
            "target_audience": "adult",
            "primary_use_case": "mystery",
            "genres": ["science_fiction"],
            "target_duration_minutes": 90,
            "target_participant_count": 4,
            "difficulty_template": "hard",
            "collaboration_mode": "solo",
        },
        "resolution_specs": [
            {
                "id": "res_root_cause",
                "target_question": "Who triggered the restart?",
                "fairness_requirements": ["The log must be obtainable."],
            }
        ],
        "entities": [
            {
                "id": "ent_researcher",
                "knowledge_states": [
                    {
                        "phase_ref": phase_ref,
                        "knows_refs": [],
                        "believes_refs": [],
                        "false_belief_refs": [],
                    }
                ],
            }
        ],
        "relationships": [{"id": "rel_maintains", "phase_refs": [phase_ref]}],
        "events": [{"id": "evt_restart", "narrative_phase_refs": [phase_ref]}],
        "information_units": [
            {
                "id": "info_restart_log",
                "availability": {
                    "phase_refs": [phase_ref],
                    "perspective_refs": [],
                    "acquisition_conditions": [],
                    "alternative_path_refs": [],
                },
            }
        ],
        "phases": [{"id": "phase_investigation", "title": "Investigation"}],
        "constraints": [
            {
                "id": "con_phase_scope",
                "scope_refs": [
                    phase_ref,
                    {"object_type": "casefile", "object_id": "case_migration_compat"},
                ],
            }
        ],
        "structure_locks": [
            {
                "id": "lock_phase",
                "object_ref": phase_ref,
                "field_paths": ["/completion_conditions"],
            }
        ],
        "extensions": {"fixture.compat": {"preserve": True}},
    }


def _legacy_brief_document() -> dict[str, object]:
    return {
        "source_text": "The seventh restart came from the backup controller.",
        "one_line_concept": "A laboratory restart mystery.",
        "core_mystery": "What caused the seventh restart?",
        "player_goal": "Identify the controller responsible.",
        "gameplay_loop": "Inspect logs and name the cause.",
        "constraints": ["No supernatural explanation."],
    }


def _canonical_hash(document: dict[str, object]) -> str:
    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def _seed_legacy_migration_documents(engine: Engine) -> MigrationCompatibilityIds:
    legacy_casefile = _legacy_casefile_document()
    legacy_brief = _legacy_brief_document()
    casefile_text = json.dumps(legacy_casefile, ensure_ascii=False)
    brief_text = json.dumps(legacy_brief, ensure_ascii=False)
    with engine.begin() as connection:
        lineage = _seed_lineage(connection, "migration-compat")
        provider_setting_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO user_provider_settings (
                        user_id, provider, model_id, secret_ciphertext,
                        secret_nonce, secret_last_four, default_budget_jsonb
                    ) VALUES (
                        :user_id, 'fake', 'fixture-model', :ciphertext,
                        :nonce, 'test', '{}'::jsonb
                    ) RETURNING id
                    """
                ),
                {
                    "user_id": lineage.owner_id,
                    "ciphertext": b"x" * 17,
                    "nonce": b"n" * 12,
                },
            ).scalar_one()
        )
        brief_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO briefs (
                        project_id, public_id, draft_revision, draft_jsonb
                    ) VALUES (
                        :project_id, 'brief_migration_compat', 1, CAST(:brief AS jsonb)
                    ) RETURNING id
                    """
                ),
                {"project_id": lineage.project_id, "brief": brief_text},
            ).scalar_one()
        )
        brief_version_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO brief_versions (
                        project_id, brief_id, version_no, content_jsonb,
                        content_hash, confirmed_by_user_id
                    ) VALUES (
                        :project_id, :brief_id, 1, CAST(:brief AS jsonb),
                        :content_hash, :owner_id
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": lineage.project_id,
                    "brief_id": brief_id,
                    "brief": brief_text,
                    "content_hash": _canonical_hash(legacy_brief),
                    "owner_id": lineage.owner_id,
                },
            ).scalar_one()
        )
        connection.execute(
            sa.text(
                """
                UPDATE briefs
                   SET current_version_id = :version_id
                 WHERE id = :brief_id
                """
            ),
            {"brief_id": brief_id, "version_id": brief_version_id},
        )

        snapshot_id = _insert_snapshot(
            connection,
            lineage,
            revision=1,
            content_hash=_canonical_hash(legacy_casefile),
            content=casefile_text,
        )
        canon_id = _insert_canon(
            connection,
            lineage,
            snapshot_id,
            content_hash=_canonical_hash(legacy_casefile),
            content=casefile_text,
        )
        task_run_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO task_runs (
                        project_id, casefile_id, draft_id, brief_version_id,
                        actor_user_id, provider_setting_id, task_type, status, stage,
                        input_draft_revision, provider, model_id,
                        provider_config_version, schema_version, agent_version,
                        prompt_version, toolset_version, budget_jsonb,
                        result_snapshot_id
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :brief_version_id,
                        :owner_id, :provider_setting_id, 'brief_to_draft',
                        'succeeded', 'completed', 1, 'fake', 'fixture-model',
                        1, '1.0', 'agent-test', 'prompt-test', 'toolset-test',
                        '{}'::jsonb, :snapshot_id
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": lineage.project_id,
                    "casefile_id": lineage.casefile_id,
                    "draft_id": lineage.draft_id,
                    "brief_version_id": brief_version_id,
                    "owner_id": lineage.owner_id,
                    "provider_setting_id": provider_setting_id,
                    "snapshot_id": snapshot_id,
                },
            ).scalar_one()
        )
        task_attempt_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO task_attempts (
                        project_id, task_run_id, attempt_no, status, candidate_jsonb
                    ) VALUES (
                        :project_id, :task_run_id, 1, 'succeeded',
                        CAST(:candidate AS jsonb)
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": lineage.project_id,
                    "task_run_id": task_run_id,
                    "candidate": casefile_text,
                },
            ).scalar_one()
        )
    return MigrationCompatibilityIds(
        project_id=lineage.project_id,
        brief_id=brief_id,
        brief_version_id=brief_version_id,
        task_run_id=task_run_id,
        task_attempt_id=task_attempt_id,
        snapshot_id=snapshot_id,
        canon_id=canon_id,
        legacy_casefile=legacy_casefile,
        legacy_brief=legacy_brief,
    )


def _assert_forward_migration_documents(
    engine: Engine,
    ids: MigrationCompatibilityIds,
) -> tuple[dict[str, object], str, dict[str, object]]:
    with engine.connect() as connection:
        snapshot_document, snapshot_hash = connection.execute(
            sa.text(
                """
                SELECT snapshot_jsonb, content_hash
                  FROM draft_snapshots
                 WHERE id = :snapshot_id
                """
            ),
            {"snapshot_id": ids.snapshot_id},
        ).one()
        canon_document, canon_hash = connection.execute(
            sa.text(
                """
                SELECT content_jsonb, content_hash
                  FROM canon_versions
                 WHERE id = :canon_id
                """
            ),
            {"canon_id": ids.canon_id},
        ).one()
        attempt_document = connection.execute(
            sa.text("SELECT candidate_jsonb FROM task_attempts WHERE id = :attempt_id"),
            {"attempt_id": ids.task_attempt_id},
        ).scalar_one()
        brief_document, brief_hash = connection.execute(
            sa.text(
                """
                SELECT content_jsonb, content_hash
                  FROM brief_versions
                 WHERE id = :brief_version_id
                """
            ),
            {"brief_version_id": ids.brief_version_id},
        ).one()
        task_input_hash, task_input = connection.execute(
            sa.text(
                """
                SELECT input_hash, input_jsonb
                  FROM task_runs
                 WHERE id = :task_run_id
                """
            ),
            {"task_run_id": ids.task_run_id},
        ).one()
        source_text = connection.execute(
            sa.text(
                """
                SELECT content_text
                  FROM source_records
                 WHERE project_id = :project_id
                """
            ),
            {"project_id": ids.project_id},
        ).scalar_one()

    assert "project_profile" not in snapshot_document
    assert "phases" not in snapshot_document
    resolution = snapshot_document["resolution_specs"][0]
    assert resolution["reasoning_question"] == "Who triggered the restart?"
    assert "target_question" not in resolution
    assert "fairness_requirements" not in resolution
    state = snapshot_document["entities"][0]["knowledge_states"][0]
    assert state["as_of_event_ref"] is None
    assert "phase_ref" not in state
    assert "phase_refs" not in snapshot_document["relationships"][0]
    assert "narrative_phase_refs" not in snapshot_document["events"][0]
    assert "phase_refs" not in snapshot_document["information_units"][0]["availability"]
    assert snapshot_document["constraints"][0]["scope_refs"] == [
        {"object_type": "casefile", "object_id": "case_migration_compat"}
    ]
    assert snapshot_document["structure_locks"] == []
    assert (
        snapshot_document["extensions"]["casefile.migration_20260728171649"]["kind"]
        == "legacy"
    )
    assert snapshot_hash == _canonical_hash(snapshot_document)
    assert canon_document == snapshot_document
    assert canon_hash == snapshot_hash
    assert attempt_document == snapshot_document
    assert brief_hash == _canonical_hash(brief_document)
    assert task_input_hash == brief_hash
    assert task_input["brief"] == brief_document
    assert source_text == ids.legacy_brief["source_text"]
    return snapshot_document, snapshot_hash, brief_document


def _assert_reverse_migration_documents(
    engine: Engine,
    ids: MigrationCompatibilityIds,
) -> None:
    assert "source_records" not in sa.inspect(engine).get_table_names()
    with engine.connect() as connection:
        snapshot_document, snapshot_hash = connection.execute(
            sa.text(
                """
                SELECT snapshot_jsonb, content_hash
                  FROM draft_snapshots
                 WHERE id = :snapshot_id
                """
            ),
            {"snapshot_id": ids.snapshot_id},
        ).one()
        canon_document, canon_hash = connection.execute(
            sa.text(
                """
                SELECT content_jsonb, content_hash
                  FROM canon_versions
                 WHERE id = :canon_id
                """
            ),
            {"canon_id": ids.canon_id},
        ).one()
        attempt_document = connection.execute(
            sa.text("SELECT candidate_jsonb FROM task_attempts WHERE id = :attempt_id"),
            {"attempt_id": ids.task_attempt_id},
        ).scalar_one()
        brief_document = connection.execute(
            sa.text("SELECT content_jsonb FROM brief_versions WHERE id = :brief_version_id"),
            {"brief_version_id": ids.brief_version_id},
        ).scalar_one()

    assert snapshot_document == ids.legacy_casefile
    assert snapshot_hash == _canonical_hash(ids.legacy_casefile)
    assert canon_document == snapshot_document
    assert canon_hash == snapshot_hash
    assert attempt_document == ids.legacy_casefile
    assert brief_document["source_text"] == ids.legacy_brief["source_text"]
    assert brief_document["one_line_concept"] == ids.legacy_brief["one_line_concept"]
    assert brief_document["core_mystery"] == ids.legacy_brief["core_mystery"]
    assert brief_document["player_goal"] == ids.legacy_brief["core_mystery"]


def _modern_casefile_document() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "casefile_id": "case_modern_migration_compat",
        "resolution_specs": [
            {
                "id": "res_modern",
                "reasoning_question": "What invariant explains the restart?",
            }
        ],
        "entities": [
            {
                "id": "ent_modern",
                "knowledge_states": [
                    {
                        "as_of_event_ref": {
                            "object_type": "event",
                            "object_id": "evt_modern",
                        },
                        "knows_refs": [],
                        "believes_refs": [],
                        "false_belief_refs": [],
                    }
                ],
            }
        ],
        "relationships": [],
        "events": [{"id": "evt_modern"}],
        "information_units": [],
        "constraints": [],
        "structure_locks": [],
        "extensions": {"fixture.modern": {"preserve": True}},
    }


def _seed_modern_migration_candidate(
    engine: Engine,
    ids: MigrationCompatibilityIds,
) -> tuple[int, dict[str, object]]:
    document = _modern_casefile_document()
    with engine.begin() as connection:
        attempt_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO task_attempts (
                        project_id, task_run_id, attempt_no, status, candidate_jsonb
                    ) VALUES (
                        :project_id, :task_run_id, 2, 'succeeded',
                        CAST(:candidate AS jsonb)
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": ids.project_id,
                    "task_run_id": ids.task_run_id,
                    "candidate": json.dumps(document),
                },
            ).scalar_one()
        )
    return attempt_id, document


def _assert_modern_candidate_downgrade(engine: Engine, attempt_id: int) -> None:
    with engine.connect() as connection:
        document = connection.execute(
            sa.text("SELECT candidate_jsonb FROM task_attempts WHERE id = :attempt_id"),
            {"attempt_id": attempt_id},
        ).scalar_one()
    assert "project_profile" in document
    assert document["phases"][0]["id"] == "phase_migration_compat"
    assert document["resolution_specs"][0]["target_question"] == (
        "What invariant explains the restart?"
    )
    assert document["resolution_specs"][0]["fairness_requirements"] == []
    assert document["entities"][0]["knowledge_states"][0]["phase_ref"] == {
        "object_type": "phase",
        "object_id": "phase_migration_compat",
    }
    assert document["extensions"]["casefile.migration_20260728171649"]["kind"] == "modern"


def _assert_task_attempt_document(
    engine: Engine,
    attempt_id: int,
    expected: dict[str, object],
) -> None:
    with engine.connect() as connection:
        document = connection.execute(
            sa.text("SELECT candidate_jsonb FROM task_attempts WHERE id = :attempt_id"),
            {"attempt_id": attempt_id},
        ).scalar_one()
    assert document == expected


def test_database_has_42_identity_tables_without_team_columns(
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
    assert len(identity_rows) == 42
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


def test_source_records_and_task_candidates_are_immutable_and_project_scoped(
    connection: Connection,
) -> None:
    first = _seed_lineage(connection, "source-first")
    second = _seed_lineage(connection, "source-second")

    def insert_original(lineage: Lineage, content: str) -> int:
        return int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO source_records (
                        project_id, source_kind, content_text, content_hash,
                        created_by_user_id
                    ) VALUES (
                        :project_id, 'human_original', :content, :content_hash,
                        :owner_id
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": lineage.project_id,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "owner_id": lineage.owner_id,
                },
            ).scalar_one()
        )

    first_source_id = insert_original(first, "First immutable source")
    second_source_id = insert_original(second, "Second immutable source")

    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO source_records (
                    project_id, source_kind, content_text, content_hash,
                    parent_source_record_id, created_by_user_id
                ) VALUES (
                    :project_id, 'human_original', 'Invalid parented original',
                    :content_hash, :parent_id, :owner_id
                )
                """
            ),
            {
                "project_id": first.project_id,
                "content_hash": hashlib.sha256(b"Invalid parented original").hexdigest(),
                "parent_id": first_source_id,
                "owner_id": first.owner_id,
            },
        )

    revision_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO source_records (
                    project_id, source_kind, content_text, content_hash,
                    parent_source_record_id, created_by_user_id
                ) VALUES (
                    :project_id, 'human_revision', :content, :content_hash,
                    :parent_id, :owner_id
                ) RETURNING id
                """
            ),
            {
                "project_id": first.project_id,
                "content": "First human revision",
                "content_hash": hashlib.sha256(b"First human revision").hexdigest(),
                "parent_id": first_source_id,
                "owner_id": first.owner_id,
            },
        ).scalar_one()
    )
    assert revision_id > first_source_id

    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO source_records (
                    project_id, source_kind, content_text, content_hash,
                    parent_source_record_id, created_by_user_id
                ) VALUES (
                    :project_id, 'human_revision', 'Cross-project parent',
                    :content_hash, :parent_id, :owner_id
                )
                """
            ),
            {
                "project_id": first.project_id,
                "content_hash": hashlib.sha256(b"Cross-project parent").hexdigest(),
                "parent_id": second_source_id,
                "owner_id": first.owner_id,
            },
        )

    provider_setting_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO user_provider_settings (
                    user_id, provider, model_id, secret_ciphertext,
                    secret_nonce, secret_last_four
                ) VALUES (
                    :owner_id, 'fake', 'fixture-model', :ciphertext,
                    :nonce, 'test'
                ) RETURNING id
                """
            ),
            {
                "owner_id": first.owner_id,
                "ciphertext": b"x" * 17,
                "nonce": b"n" * 12,
            },
        ).scalar_one()
    )

    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                UPDATE user_provider_settings
                   SET credential_status = 'deleted',
                       credential_deleted_at = CURRENT_TIMESTAMP
                 WHERE id = :setting_id
                """
            ),
            {"setting_id": provider_setting_id},
        )

    connection.execute(
        sa.text(
            """
            UPDATE user_provider_settings
               SET credential_status = 'deleted',
                   credential_deleted_at = CURRENT_TIMESTAMP,
                   secret_ciphertext = NULL,
                   secret_nonce = NULL,
                   key_version = NULL,
                   secret_last_four = NULL
             WHERE id = :setting_id
            """
        ),
        {"setting_id": provider_setting_id},
    )
    deleted_material = connection.execute(
        sa.text(
            """
            SELECT secret_ciphertext, secret_nonce, key_version, secret_last_four
              FROM user_provider_settings
             WHERE id = :setting_id
            """
        ),
        {"setting_id": provider_setting_id},
    ).one()
    assert deleted_material == (None, None, None, None)

    connection.execute(
        sa.text(
            """
            UPDATE user_provider_settings
               SET credential_status = 'unverified',
                   credential_deleted_at = NULL,
                   secret_ciphertext = :ciphertext,
                   secret_nonce = :nonce,
                   key_version = 1,
                   secret_last_four = 'test'
             WHERE id = :setting_id
            """
        ),
        {
            "setting_id": provider_setting_id,
            "ciphertext": b"x" * 17,
            "nonce": b"n" * 12,
        },
    )

    task_parameters = {
        "project_id": first.project_id,
        "casefile_id": first.casefile_id,
        "draft_id": first.draft_id,
        "source_id": first_source_id,
        "owner_id": first.owner_id,
        "provider_setting_id": provider_setting_id,
        "input_hash": hashlib.sha256(b"First immutable source").hexdigest(),
    }
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO task_runs (
                    project_id, casefile_id, draft_id, input_source_record_id,
                    input_hash, input_jsonb, actor_user_id, provider_setting_id,
                    task_type, input_draft_revision, provider, model_id,
                    provider_config_version, schema_version, agent_version,
                    prompt_version, toolset_version, budget_jsonb
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :wrong_source_id,
                    :input_hash, '{}'::jsonb, :owner_id, :provider_setting_id,
                    'brief_polish', 1, 'fake', 'fixture-model',
                    1, '1.0', 'agent-test', 'prompt-test', 'toolset-test',
                    '{}'::jsonb
                )
                """
            ),
            {**task_parameters, "wrong_source_id": second_source_id},
        )

    task_run_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO task_runs (
                    project_id, casefile_id, draft_id, input_source_record_id,
                    input_hash, input_jsonb, actor_user_id, provider_setting_id,
                    task_type, input_draft_revision, provider, model_id,
                    provider_config_version, schema_version, agent_version,
                    prompt_version, toolset_version, budget_jsonb
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :source_id,
                    :input_hash, jsonb_build_object('source_record_id', :source_id),
                    :owner_id, :provider_setting_id, 'brief_polish', 1,
                    'fake', 'fixture-model', 1, '1.0', 'agent-test',
                    'prompt-test', 'toolset-test', '{}'::jsonb
                ) RETURNING id
                """
            ),
            task_parameters,
        ).scalar_one()
    )

    proposal_text = "Agent polish proposal"
    proposal_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO source_records (
                    project_id, source_kind, content_text, content_hash,
                    parent_source_record_id, generated_by_task_run_id,
                    created_by_user_id
                ) VALUES (
                    :project_id, 'agent_polish_proposal', :content, :content_hash,
                    :parent_id, :task_run_id, :owner_id
                ) RETURNING id
                """
            ),
            {
                "project_id": first.project_id,
                "content": proposal_text,
                "content_hash": hashlib.sha256(proposal_text.encode()).hexdigest(),
                "parent_id": first_source_id,
                "task_run_id": task_run_id,
                "owner_id": first.owner_id,
            },
        ).scalar_one()
    )
    assert proposal_id > revision_id

    candidate_attempt_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO task_attempts (
                    project_id, task_run_id, attempt_no, status, candidate_jsonb
                ) VALUES (
                    :project_id, :task_run_id, 1, 'succeeded',
                    '{"candidate": true}'::jsonb
                ) RETURNING id
                """
            ),
            {
                "project_id": first.project_id,
                "task_run_id": task_run_id,
            },
        ).scalar_one()
    )
    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                UPDATE task_attempts
                   SET candidate_jsonb = '{"candidate": false}'::jsonb
                 WHERE id = :attempt_id
                """
            ),
            {"attempt_id": candidate_attempt_id},
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text("DELETE FROM task_attempts WHERE id = :attempt_id"),
            {"attempt_id": candidate_attempt_id},
        )

    with _expect_database_error(connection):
        connection.execute(
            sa.text(
                """
                INSERT INTO source_records (
                    project_id, source_kind, content_text, content_hash,
                    parent_source_record_id, generated_by_task_run_id,
                    created_by_user_id
                ) VALUES (
                    :project_id, 'agent_polish_proposal', 'Cross-project task',
                    :content_hash, :parent_id, :task_run_id, :owner_id
                )
                """
            ),
            {
                "project_id": second.project_id,
                "content_hash": hashlib.sha256(b"Cross-project task").hexdigest(),
                "parent_id": second_source_id,
                "task_run_id": task_run_id,
                "owner_id": second.owner_id,
            },
        )

    with _expect_database_error(connection):
        connection.execute(
            sa.text("UPDATE source_records SET content_text = content_text WHERE id = :id"),
            {"id": first_source_id},
        )
    with _expect_database_error(connection):
        connection.execute(
            sa.text("DELETE FROM source_records WHERE id = :id"),
            {"id": first_source_id},
        )
