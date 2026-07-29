"""source_records_and_brief_tasks

Revision ID: 20260728171649
Revises: 20260728084832
Create Date: 2026-07-28 17:16:50.698722
"""

import copy
import hashlib
from collections.abc import Sequence
from typing import Any

import rfc8785
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728171649"
down_revision: str | None = "20260728084832"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASEFILE_COMPAT_EXTENSION = "casefile.migration_20260728171649"
_COMPAT_PHASE_ID = "phase_migration_compat"


def upgrade() -> None:
    _create_source_records()
    _forward_convert_legacy_briefs()
    _forward_convert_casefile_history()
    _extend_task_runs()
    op.execute(
        """
        CREATE TRIGGER trg_source_records_immutable
        BEFORE UPDATE OR DELETE ON source_records
        FOR EACH ROW EXECUTE FUNCTION casefile_reject_history_mutation()
        """
    )


def downgrade() -> None:
    _reverse_convert_casefile_history()
    _reverse_convert_briefs()
    op.execute("DROP TRIGGER trg_source_records_immutable ON source_records")
    _restore_task_runs()
    op.drop_index("ix_source_records_parent_source_record_id", table_name="source_records")
    op.drop_index("ix_source_records_project_id_created_at", table_name="source_records")
    op.drop_table("source_records")


def _create_source_records() -> None:
    op.create_table(
        "source_records",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("generated_by_task_run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind IN "
            "('human_original', 'agent_polish_proposal', 'human_revision')",
            name=op.f("ck_source_records_source_kind_allowed"),
        ),
        sa.CheckConstraint(
            "btrim(content_text) <> ''",
            name=op.f("ck_source_records_content_not_blank"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_source_records_content_hash_format"),
        ),
        sa.CheckConstraint(
            "("
            "source_kind = 'human_original' "
            "AND parent_source_record_id IS NULL "
            "AND generated_by_task_run_id IS NULL"
            ") OR ("
            "source_kind = 'human_revision' "
            "AND parent_source_record_id IS NOT NULL "
            "AND generated_by_task_run_id IS NULL"
            ") OR ("
            "source_kind = 'agent_polish_proposal' "
            "AND parent_source_record_id IS NOT NULL "
            "AND generated_by_task_run_id IS NOT NULL"
            ")",
            name=op.f("ck_source_records_provenance_matches_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_source_records_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "parent_source_record_id"],
            ["source_records.project_id", "source_records.id"],
            name="fk_source_records_project_parent_source_records",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_source_records_project_generated_task_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_source_records_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_records")),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_source_records_project_id_id",
        ),
    )
    op.create_index(
        "ix_source_records_project_id_created_at",
        "source_records",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_source_records_parent_source_record_id",
        "source_records",
        ["parent_source_record_id"],
    )


def _extend_task_runs() -> None:
    op.alter_column(
        "task_runs",
        "brief_version_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column("task_runs", sa.Column("input_source_record_id", sa.BigInteger()))
    op.add_column("task_runs", sa.Column("input_brief_revision", sa.Integer()))
    op.add_column("task_runs", sa.Column("input_hash", sa.String(length=64)))
    op.add_column(
        "task_runs",
        sa.Column("input_jsonb", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "task_runs",
        sa.Column("result_jsonb", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.execute(
        """
        UPDATE task_runs AS task
           SET input_brief_revision = brief.draft_revision,
               input_hash = version.content_hash,
               input_jsonb = jsonb_build_object(
                   'brief', version.content_jsonb,
                   'brief_public_id', brief.public_id,
                   'brief_version_no', version.version_no
               )
          FROM brief_versions AS version
          JOIN briefs AS brief
            ON brief.id = version.brief_id
           AND brief.project_id = version.project_id
         WHERE task.brief_version_id = version.id
           AND task.project_id = version.project_id
        """
    )
    op.alter_column("task_runs", "input_hash", existing_type=sa.String(length=64), nullable=False)
    op.alter_column(
        "task_runs",
        "input_jsonb",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_task_runs_project_input_source_source_records",
        "task_runs",
        "source_records",
        ["project_id", "input_source_record_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_task_runs_task_type_format"),
        "task_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_to_draft')",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_brief_revision_positive"),
        "task_runs",
        "input_brief_revision IS NULL OR input_brief_revision >= 1",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_hash_format"),
        "task_runs",
        "input_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_is_object"),
        "task_runs",
        "jsonb_typeof(input_jsonb) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_result_is_object"),
        "task_runs",
        "result_jsonb IS NULL OR jsonb_typeof(result_jsonb) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        "("
        "task_type = 'brief_polish' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL "
        "AND input_brief_revision IS NULL"
        ") OR ("
        "task_type = 'brief_anchor_extract' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL"
        ") OR ("
        "task_type = 'brief_to_draft' "
        "AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL"
        ")",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_snapshot_matches_task_type"),
        "task_runs",
        "(task_type = 'brief_to_draft') OR result_snapshot_id IS NULL",
    )
    op.create_index(
        "ix_task_runs_project_type_created_at",
        "task_runs",
        ["project_id", "task_type", "created_at"],
    )


def _restore_task_runs() -> None:
    op.drop_constraint(
        op.f("ck_task_runs_snapshot_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_result_is_object"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_is_object"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_hash_format"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_brief_revision_positive"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_format"),
        "task_runs",
        "task_type ~ '^[a-z][a-z0-9_]*$'",
    )
    op.drop_index("ix_task_runs_project_type_created_at", table_name="task_runs")
    op.drop_constraint(
        "fk_source_records_project_generated_task_task_runs",
        "source_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_task_runs_project_input_source_source_records",
        "task_runs",
        type_="foreignkey",
    )
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        """
        DELETE FROM task_events
         WHERE task_run_id IN (
             SELECT id FROM task_runs WHERE task_type <> 'brief_to_draft'
         )
        """
    )
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.execute(
        """
        DELETE FROM task_attempts
         WHERE task_run_id IN (
             SELECT id FROM task_runs WHERE task_type <> 'brief_to_draft'
         )
        """
    )
    op.execute("DELETE FROM task_runs WHERE task_type <> 'brief_to_draft'")
    op.drop_column("task_runs", "result_jsonb")
    op.drop_column("task_runs", "input_jsonb")
    op.drop_column("task_runs", "input_hash")
    op.drop_column("task_runs", "input_brief_revision")
    op.drop_column("task_runs", "input_source_record_id")
    op.alter_column(
        "task_runs",
        "brief_version_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )


def _forward_convert_casefile_history() -> None:
    _rewrite_casefile_history(_forward_casefile_document)


def _reverse_convert_casefile_history() -> None:
    _rewrite_casefile_history(_reverse_casefile_document)


def _rewrite_casefile_history(
    converter: Any,
) -> None:
    bind = op.get_bind()
    op.execute(
        "ALTER TABLE draft_snapshots DISABLE TRIGGER trg_draft_snapshots_immutable"
    )
    op.execute("ALTER TABLE canon_versions DISABLE TRIGGER trg_canon_versions_immutable")
    try:
        rows = bind.execute(
            sa.text("SELECT id, snapshot_jsonb FROM draft_snapshots ORDER BY id")
        ).mappings()
        for row in rows:
            current = dict(row["snapshot_jsonb"])
            converted = converter(current)
            if converted == current:
                continue
            bind.execute(
                sa.text(
                    """
                    UPDATE draft_snapshots
                       SET snapshot_jsonb = :content,
                           content_hash = :content_hash
                     WHERE id = :id
                    """
                ).bindparams(
                    sa.bindparam("content", type_=postgresql.JSONB()),
                ),
                {
                    "id": row["id"],
                    "content": converted,
                    "content_hash": _json_hash(converted),
                },
            )

        # Canon is defined as an exact copy of its source Snapshot. Copying the
        # converted source also repairs the hash without independently converting
        # two immutable documents that must stay byte-for-byte equivalent.
        bind.execute(
            sa.text(
                """
                UPDATE canon_versions AS canon
                   SET content_jsonb = snapshot.snapshot_jsonb,
                       content_hash = snapshot.content_hash
                  FROM draft_snapshots AS snapshot
                 WHERE snapshot.id = canon.source_snapshot_id
                   AND snapshot.project_id = canon.project_id
                   AND snapshot.casefile_id = canon.casefile_id
                """
            )
        )
    finally:
        op.execute("ALTER TABLE canon_versions ENABLE TRIGGER trg_canon_versions_immutable")
        op.execute(
            "ALTER TABLE draft_snapshots ENABLE TRIGGER trg_draft_snapshots_immutable"
        )

    rows = bind.execute(
        sa.text(
            """
            SELECT id, candidate_jsonb
              FROM task_attempts
             WHERE candidate_jsonb IS NOT NULL
             ORDER BY id
            """
        )
    ).mappings()
    for row in rows:
        current = dict(row["candidate_jsonb"])
        converted = converter(current)
        if converted == current:
            continue
        bind.execute(
            sa.text(
                "UPDATE task_attempts SET candidate_jsonb = :content WHERE id = :id"
            ).bindparams(sa.bindparam("content", type_=postgresql.JSONB())),
            {"id": row["id"], "content": converted},
        )


def _forward_casefile_document(document: dict[str, Any]) -> dict[str, Any]:
    if not _looks_like_casefile(document):
        return document

    converted = copy.deepcopy(document)
    extensions = converted.get("extensions")
    if not isinstance(extensions, dict):
        return document
    existing_compat = extensions.pop(_CASEFILE_COMPAT_EXTENSION, None)
    modern_fields: dict[str, Any] | None = None
    if existing_compat is not None:
        if (
            not isinstance(existing_compat, dict)
            or existing_compat.get("kind") != "modern"
            or not isinstance(existing_compat.get("fields"), dict)
        ):
            raise RuntimeError(
                f"reserved CaseFile extension {_CASEFILE_COMPAT_EXTENSION!r} is already in use"
            )
        modern_fields = dict(existing_compat["fields"])

    legacy_fields: dict[str, Any] = {}
    if "project_profile" in converted:
        legacy_fields["project_profile"] = converted.pop("project_profile")
    if "phases" in converted:
        legacy_fields["phases"] = converted.pop("phases")

    resolutions = _objects(converted, "resolution_specs")
    fairness = _pop_indexed_fields(resolutions, "fairness_requirements")
    if fairness:
        legacy_fields["resolution_fairness_requirements"] = fairness
    for resolution in resolutions:
        if "target_question" not in resolution:
            continue
        target_question = resolution.pop("target_question")
        resolution.setdefault("reasoning_question", target_question)

    legacy_knowledge_phases: list[dict[str, Any]] = []
    modern_as_of = (
        modern_fields.get("entity_knowledge_as_of_event_refs", [])
        if modern_fields is not None
        else []
    )
    for entity_index, entity in enumerate(_objects(converted, "entities")):
        states = entity.get("knowledge_states")
        if not isinstance(states, list):
            continue
        for state_index, state in enumerate(states):
            if not isinstance(state, dict):
                continue
            if "phase_ref" in state:
                legacy_knowledge_phases.append(
                    _nested_entry(
                        entity,
                        entity_index,
                        state_index,
                        state.pop("phase_ref"),
                    )
                )
            found, value = _find_nested_value(
                modern_as_of,
                entity,
                entity_index,
                state_index,
            )
            if found:
                state["as_of_event_ref"] = copy.deepcopy(value)
            else:
                state.setdefault("as_of_event_ref", None)
    if legacy_knowledge_phases:
        legacy_fields["entity_knowledge_phase_refs"] = legacy_knowledge_phases

    _pop_collection_field(
        converted,
        "relationships",
        "phase_refs",
        "relationship_phase_refs",
        legacy_fields,
    )
    _pop_collection_field(
        converted,
        "events",
        "narrative_phase_refs",
        "event_narrative_phase_refs",
        legacy_fields,
    )

    availability_phases: list[dict[str, Any]] = []
    for index, information in enumerate(_objects(converted, "information_units")):
        availability = information.get("availability")
        if isinstance(availability, dict) and "phase_refs" in availability:
            availability_phases.append(
                _indexed_entry(information, index, availability.pop("phase_refs"))
            )
    if availability_phases:
        legacy_fields["information_availability_phase_refs"] = availability_phases

    constraint_phase_scopes: list[dict[str, Any]] = []
    for index, constraint in enumerate(_objects(converted, "constraints")):
        scope_refs = constraint.get("scope_refs")
        if not isinstance(scope_refs, list):
            continue
        filtered = [ref for ref in scope_refs if not _is_phase_ref(ref)]
        if filtered != scope_refs:
            constraint_phase_scopes.append(_indexed_entry(constraint, index, scope_refs))
            constraint["scope_refs"] = filtered
    if constraint_phase_scopes:
        legacy_fields["constraint_phase_scope_refs"] = constraint_phase_scopes

    structure_locks = converted.get("structure_locks")
    if isinstance(structure_locks, list):
        kept_locks: list[Any] = []
        removed_locks: list[dict[str, Any]] = []
        for index, lock in enumerate(structure_locks):
            if isinstance(lock, dict) and _is_phase_ref(lock.get("object_ref")):
                removed_locks.append({"index": index, "value": copy.deepcopy(lock)})
            else:
                kept_locks.append(lock)
        if removed_locks:
            converted["structure_locks"] = kept_locks
            legacy_fields["phase_structure_locks"] = removed_locks

    if modern_fields is None:
        if not legacy_fields:
            return document
        extensions[_CASEFILE_COMPAT_EXTENSION] = {
            "kind": "legacy",
            "fields": legacy_fields,
        }
    return converted


def _reverse_casefile_document(document: dict[str, Any]) -> dict[str, Any]:
    if not _looks_like_casefile(document):
        return document

    converted = copy.deepcopy(document)
    extensions = converted.get("extensions")
    if not isinstance(extensions, dict):
        return document
    existing_compat = extensions.pop(_CASEFILE_COMPAT_EXTENSION, None)
    legacy_fields: dict[str, Any] | None = None
    if existing_compat is not None:
        if not isinstance(existing_compat, dict) or not isinstance(
            existing_compat.get("fields"), dict
        ):
            raise RuntimeError(
                f"invalid CaseFile compatibility extension {_CASEFILE_COMPAT_EXTENSION!r}"
            )
        if existing_compat.get("kind") == "modern":
            return document
        if existing_compat.get("kind") != "legacy":
            raise RuntimeError(
                f"invalid CaseFile compatibility extension {_CASEFILE_COMPAT_EXTENSION!r}"
            )
        legacy_fields = dict(existing_compat["fields"])

    modern_fields: dict[str, Any] = {}
    if legacy_fields is not None and "project_profile" in legacy_fields:
        converted["project_profile"] = copy.deepcopy(legacy_fields["project_profile"])
    else:
        converted["project_profile"] = _legacy_project_profile()

    if legacy_fields is not None and "phases" in legacy_fields:
        converted["phases"] = copy.deepcopy(legacy_fields["phases"])
    else:
        converted["phases"] = []

    resolutions = _objects(converted, "resolution_specs")
    stored_fairness = (
        legacy_fields.get("resolution_fairness_requirements", [])
        if legacy_fields is not None
        else []
    )
    for index, resolution in enumerate(resolutions):
        if "reasoning_question" in resolution:
            resolution["target_question"] = resolution.pop("reasoning_question")
        found, value = _find_indexed_value(stored_fairness, resolution, index)
        resolution["fairness_requirements"] = copy.deepcopy(value) if found else []

    stored_knowledge_phases = (
        legacy_fields.get("entity_knowledge_phase_refs", [])
        if legacy_fields is not None
        else []
    )
    modern_as_of: list[dict[str, Any]] = []
    needs_compat_phase = False
    for entity_index, entity in enumerate(_objects(converted, "entities")):
        states = entity.get("knowledge_states")
        if not isinstance(states, list):
            continue
        for state_index, state in enumerate(states):
            if not isinstance(state, dict):
                continue
            if legacy_fields is None:
                modern_as_of.append(
                    _nested_entry(
                        entity,
                        entity_index,
                        state_index,
                        state.get("as_of_event_ref"),
                    )
                )
            state.pop("as_of_event_ref", None)
            found, value = _find_nested_value(
                stored_knowledge_phases,
                entity,
                entity_index,
                state_index,
            )
            if found:
                state["phase_ref"] = copy.deepcopy(value)
            else:
                state["phase_ref"] = {
                    "object_type": "phase",
                    "object_id": _COMPAT_PHASE_ID,
                }
                needs_compat_phase = True
    if modern_as_of:
        modern_fields["entity_knowledge_as_of_event_refs"] = modern_as_of

    _restore_collection_field(
        converted,
        "relationships",
        "phase_refs",
        "relationship_phase_refs",
        legacy_fields,
    )
    _restore_collection_field(
        converted,
        "events",
        "narrative_phase_refs",
        "event_narrative_phase_refs",
        legacy_fields,
    )

    stored_availability = (
        legacy_fields.get("information_availability_phase_refs", [])
        if legacy_fields is not None
        else []
    )
    for index, information in enumerate(_objects(converted, "information_units")):
        availability = information.get("availability")
        if not isinstance(availability, dict):
            continue
        found, value = _find_indexed_value(stored_availability, information, index)
        availability["phase_refs"] = copy.deepcopy(value) if found else []

    stored_constraint_scopes = (
        legacy_fields.get("constraint_phase_scope_refs", [])
        if legacy_fields is not None
        else []
    )
    for index, constraint in enumerate(_objects(converted, "constraints")):
        found, value = _find_indexed_value(stored_constraint_scopes, constraint, index)
        if found:
            constraint["scope_refs"] = copy.deepcopy(value)

    if legacy_fields is not None:
        _restore_removed_items(
            converted,
            "structure_locks",
            legacy_fields.get("phase_structure_locks", []),
        )

    if needs_compat_phase and not any(
        isinstance(phase, dict) and phase.get("id") == _COMPAT_PHASE_ID
        for phase in converted["phases"]
    ):
        converted["phases"].append(_legacy_compat_phase())

    if legacy_fields is None:
        extensions[_CASEFILE_COMPAT_EXTENSION] = {
            "kind": "modern",
            "fields": modern_fields,
        }
    return converted


def _looks_like_casefile(document: dict[str, Any]) -> bool:
    return (
        isinstance(document.get("casefile_id"), str)
        and isinstance(document.get("resolution_specs"), list)
        and isinstance(document.get("entities"), list)
    )


def _objects(document: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    values = document.get(collection)
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _indexed_entry(item: dict[str, Any], index: int, value: Any) -> dict[str, Any]:
    return {
        "index": index,
        "object_id": item.get("id"),
        "value": copy.deepcopy(value),
    }


def _nested_entry(
    item: dict[str, Any],
    item_index: int,
    nested_index: int,
    value: Any,
) -> dict[str, Any]:
    return {
        "index": item_index,
        "object_id": item.get("id"),
        "nested_index": nested_index,
        "value": copy.deepcopy(value),
    }


def _find_indexed_value(
    entries: Any,
    item: dict[str, Any],
    index: int,
) -> tuple[bool, Any]:
    if not isinstance(entries, list):
        return False, None
    object_id = item.get("id")
    for entry in entries:
        if not isinstance(entry, dict) or "value" not in entry:
            continue
        if object_id is not None and entry.get("object_id") == object_id:
            return True, entry["value"]
        if entry.get("index") == index:
            return True, entry["value"]
    return False, None


def _find_nested_value(
    entries: Any,
    item: dict[str, Any],
    item_index: int,
    nested_index: int,
) -> tuple[bool, Any]:
    if not isinstance(entries, list):
        return False, None
    object_id = item.get("id")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or "value" not in entry
            or entry.get("nested_index") != nested_index
        ):
            continue
        if object_id is not None and entry.get("object_id") == object_id:
            return True, entry["value"]
        if entry.get("index") == item_index:
            return True, entry["value"]
    return False, None


def _pop_indexed_fields(
    items: list[dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if field in item:
            removed.append(_indexed_entry(item, index, item.pop(field)))
    return removed


def _pop_collection_field(
    document: dict[str, Any],
    collection: str,
    field: str,
    compatibility_name: str,
    compatibility: dict[str, Any],
) -> None:
    removed = _pop_indexed_fields(_objects(document, collection), field)
    if removed:
        compatibility[compatibility_name] = removed


def _restore_collection_field(
    document: dict[str, Any],
    collection: str,
    field: str,
    compatibility_name: str,
    compatibility: dict[str, Any] | None,
) -> None:
    entries = compatibility.get(compatibility_name, []) if compatibility is not None else []
    for index, item in enumerate(_objects(document, collection)):
        found, value = _find_indexed_value(entries, item, index)
        item[field] = copy.deepcopy(value) if found else []


def _restore_removed_items(
    document: dict[str, Any],
    collection: str,
    removed: Any,
) -> None:
    values = document.get(collection)
    if not isinstance(values, list) or not isinstance(removed, list):
        return
    for entry in sorted(
        (entry for entry in removed if isinstance(entry, dict) and "value" in entry),
        key=lambda entry: int(entry.get("index", len(values))),
    ):
        index = min(max(int(entry.get("index", len(values))), 0), len(values))
        values.insert(index, copy.deepcopy(entry["value"]))


def _is_phase_ref(value: Any) -> bool:
    return isinstance(value, dict) and value.get("object_type") == "phase"


def _legacy_project_profile() -> dict[str, Any]:
    return {
        "content_type": "interactive_reasoning",
        "target_audience": "general",
        "primary_use_case": "casefile_reasoning",
        "genres": [],
        "target_duration_minutes": 60,
        "target_participant_count": 1,
        "difficulty_template": "custom",
        "collaboration_mode": "solo",
    }


def _legacy_compat_phase() -> dict[str, Any]:
    return {
        "id": _COMPAT_PHASE_ID,
        "title": "Migration compatibility phase",
        "order": 1,
        "entry_conditions": [],
        "visible_information_refs": [],
        "allowed_action_types": [],
        "completion_conditions": [],
        "tags": [],
        "source_refs": [],
        "confidence": 1.0,
        "confirmation_status": "unresolved",
        "created_by": {
            "actor_type": "system",
            "actor_id": "system_migration_compat",
        },
        "updated_at": "1970-01-01T00:00:00Z",
        "revision": 1,
    }


def _forward_convert_legacy_briefs() -> None:
    bind = op.get_bind()
    source_ids: dict[tuple[int, str], int] = {}

    def source_id(project_id: int, content: str) -> int:
        cache_key = (project_id, content)
        cached = source_ids.get(cache_key)
        if cached is not None:
            return cached
        inserted = int(
            bind.execute(
                sa.text(
                    """
                    INSERT INTO source_records (
                        project_id, source_kind, content_text, content_hash,
                        created_by_user_id
                    )
                    SELECT id, 'human_original', :content, :content_hash, owner_user_id
                      FROM projects
                     WHERE id = :project_id
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "content": content,
                    "content_hash": _text_hash(content),
                },
            ).scalar_one()
        )
        source_ids[cache_key] = inserted
        return inserted

    rows = bind.execute(
        sa.text("SELECT id, project_id, draft_jsonb FROM briefs ORDER BY id")
    ).mappings()
    for row in rows:
        old = dict(row["draft_jsonb"])
        if not old or "creative_intent" in old:
            continue
        converted = _forward_brief(old, source_id(int(row["project_id"]), old["source_text"]))
        bind.execute(
            sa.text("UPDATE briefs SET draft_jsonb = :content WHERE id = :id").bindparams(
                sa.bindparam("content", type_=postgresql.JSONB())
            ),
            {"id": row["id"], "content": converted},
        )

    op.execute("ALTER TABLE brief_versions DISABLE TRIGGER trg_brief_versions_immutable")
    rows = bind.execute(
        sa.text("SELECT id, project_id, content_jsonb FROM brief_versions ORDER BY id")
    ).mappings()
    for row in rows:
        old = dict(row["content_jsonb"])
        if "creative_intent" in old:
            continue
        converted = _forward_brief(old, source_id(int(row["project_id"]), old["source_text"]))
        bind.execute(
            sa.text(
                """
                UPDATE brief_versions
                   SET content_jsonb = :content,
                       content_hash = :content_hash
                 WHERE id = :id
                """
            ).bindparams(sa.bindparam("content", type_=postgresql.JSONB())),
            {
                "id": row["id"],
                "content": converted,
                "content_hash": _json_hash(converted),
            },
        )
    op.execute("ALTER TABLE brief_versions ENABLE TRIGGER trg_brief_versions_immutable")


def _reverse_convert_briefs() -> None:
    bind = op.get_bind()

    def source_text(project_id: int, source_record_ids: list[int]) -> str:
        if not source_record_ids:
            return "Legacy source was unavailable after downgrade."
        rows = bind.execute(
            sa.text(
                """
                SELECT content_text
                  FROM source_records
                 WHERE project_id = :project_id
                   AND id = ANY(:source_ids)
                 ORDER BY id
                """
            ),
            {"project_id": project_id, "source_ids": source_record_ids},
        ).scalars()
        values = list(rows)
        return "\n\n".join(values) or "Legacy source was unavailable after downgrade."

    rows = bind.execute(
        sa.text("SELECT id, project_id, draft_jsonb FROM briefs ORDER BY id")
    ).mappings()
    for row in rows:
        current = dict(row["draft_jsonb"])
        if not current or "one_line_concept" in current:
            continue
        converted = _reverse_brief(
            current,
            source_text(int(row["project_id"]), list(current["source_record_ids"])),
        )
        bind.execute(
            sa.text("UPDATE briefs SET draft_jsonb = :content WHERE id = :id").bindparams(
                sa.bindparam("content", type_=postgresql.JSONB())
            ),
            {"id": row["id"], "content": converted},
        )

    op.execute("ALTER TABLE brief_versions DISABLE TRIGGER trg_brief_versions_immutable")
    rows = bind.execute(
        sa.text("SELECT id, project_id, content_jsonb FROM brief_versions ORDER BY id")
    ).mappings()
    for row in rows:
        current = dict(row["content_jsonb"])
        if "one_line_concept" in current:
            continue
        converted = _reverse_brief(
            current,
            source_text(int(row["project_id"]), list(current["source_record_ids"])),
        )
        bind.execute(
            sa.text(
                """
                UPDATE brief_versions
                   SET content_jsonb = :content,
                       content_hash = :content_hash
                 WHERE id = :id
                """
            ).bindparams(sa.bindparam("content", type_=postgresql.JSONB())),
            {
                "id": row["id"],
                "content": converted,
                "content_hash": _json_hash(converted),
            },
        )
    op.execute("ALTER TABLE brief_versions ENABLE TRIGGER trg_brief_versions_immutable")


def _forward_brief(old: dict[str, Any], source_record_id: int) -> dict[str, Any]:
    constraints = [str(value) for value in old.get("constraints", []) if str(value).strip()]
    return {
        "source_record_ids": [source_record_id],
        "creative_intent": str(old.get("one_line_concept") or old["source_text"]).strip(),
        "reasoning_proposition": str(
            old.get("core_mystery") or old.get("player_goal") or old["source_text"]
        ).strip(),
        "resolution_mode": "agent_proposed",
        "author_answer": None,
        "author_anchors": [],
        "boundary_text": "\n".join(constraints) or None,
        "creative_constraints": [
            {
                "constraint_id": f"constraint_legacy_{index:02d}",
                "statement": statement,
                "strength": "hard",
            }
            for index, statement in enumerate(constraints, start=1)
        ],
    }


def _reverse_brief(current: dict[str, Any], source_text: str) -> dict[str, Any]:
    constraints = [
        str(item["statement"])
        for item in current.get("creative_constraints", [])
        if str(item.get("statement", "")).strip()
    ]
    return {
        "source_text": source_text,
        "one_line_concept": str(current["creative_intent"]),
        "core_mystery": str(current["reasoning_proposition"]),
        "player_goal": str(current["reasoning_proposition"]),
        "gameplay_loop": "Explore the evidence, compare hypotheses, and resolve the proposition.",
        "constraints": constraints,
    }


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
