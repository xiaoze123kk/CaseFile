"""Static metadata tests for the 14-table foundation."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from casefile.data_postgres.base import Base
from casefile.data_postgres import models  # noqa: F401

EXPECTED_TABLES = {
    "approvals",
    "audit_events",
    "canon_versions",
    "casefile_objects",
    "casefile_refs",
    "casefiles",
    "draft_operations",
    "draft_snapshots",
    "drafts",
    "memberships",
    "projects",
    "users",
    "workspace_settings",
    "workspaces",
}


def test_foundation_contains_exactly_fourteen_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_business_table_is_workspace_scoped() -> None:
    for table_name in EXPECTED_TABLES - {"users", "workspaces"}:
        assert "workspace_id" in Base.metadata.tables[table_name].columns


def test_cross_domain_links_use_composite_foreign_keys() -> None:
    expected = {
        "casefiles": 2,
        "drafts": 2,
        "casefile_objects": 3,
        "casefile_refs": 3,
        "draft_snapshots": 3,
        "canon_versions": 4,
    }
    for table_name, minimum_width in expected.items():
        foreign_keys = [
            constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, ForeignKeyConstraint)
        ]
        assert any(len(constraint.columns) >= minimum_width for constraint in foreign_keys)


def test_object_and_version_guards_exist_in_metadata() -> None:
    required_fragments = {
        "casefile_objects": {"revision >= 1", "confidence BETWEEN 0 AND 1"},
        "canon_versions": {"version_no >= 1", "content_hash"},
        "approvals": {"status IN", "decision_fields"},
    }
    for table_name, fragments in required_fragments.items():
        checks = " ".join(
            str(constraint.sqltext)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        )
        for fragment in fragments:
            if fragment == "decision_fields":
                assert "decided_by_actor_id" in checks
            else:
                assert fragment in checks
