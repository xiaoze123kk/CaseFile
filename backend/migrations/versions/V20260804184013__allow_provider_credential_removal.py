"""allow_provider_credential_removal

Revision ID: 20260804184013
Revises: 20260730093618
Create Date: 2026-08-04 18:40:14.470113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804184013"
down_revision: str | None = "20260730093618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_provider_settings",
        sa.Column("credential_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_user_provider_settings_credential_status_allowed"),
        "user_provider_settings",
        type_="check",
    )
    for column_name, existing_type in (
        ("secret_ciphertext", sa.LargeBinary()),
        ("secret_nonce", sa.LargeBinary()),
        ("key_version", sa.BigInteger()),
        ("secret_last_four", sa.String(length=4)),
    ):
        op.alter_column(
            "user_provider_settings",
            column_name,
            existing_type=existing_type,
            nullable=True,
        )
    op.create_check_constraint(
        op.f("ck_user_provider_settings_credential_status_allowed"),
        "user_provider_settings",
        "credential_status IN ('unverified', 'valid', 'invalid', 'deleted')",
    )
    op.create_check_constraint(
        op.f("ck_user_provider_settings_credential_material_consistent"),
        "user_provider_settings",
        "(credential_status = 'deleted' "
        "AND credential_deleted_at IS NOT NULL "
        "AND secret_ciphertext IS NULL "
        "AND secret_nonce IS NULL "
        "AND key_version IS NULL "
        "AND secret_last_four IS NULL) "
        "OR (credential_status <> 'deleted' "
        "AND credential_deleted_at IS NULL "
        "AND secret_ciphertext IS NOT NULL "
        "AND secret_nonce IS NOT NULL "
        "AND key_version IS NOT NULL "
        "AND secret_last_four IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM user_provider_settings
                 WHERE credential_status = 'deleted'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while deleted provider credentials are retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint(
        op.f("ck_user_provider_settings_credential_material_consistent"),
        "user_provider_settings",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_user_provider_settings_credential_status_allowed"),
        "user_provider_settings",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_user_provider_settings_credential_status_allowed"),
        "user_provider_settings",
        "credential_status IN ('unverified', 'valid', 'invalid')",
    )
    for column_name, existing_type in (
        ("secret_ciphertext", sa.LargeBinary()),
        ("secret_nonce", sa.LargeBinary()),
        ("key_version", sa.BigInteger()),
        ("secret_last_four", sa.String(length=4)),
    ):
        op.alter_column(
            "user_provider_settings",
            column_name,
            existing_type=existing_type,
            nullable=False,
        )
    op.drop_column("user_provider_settings", "credential_deleted_at")
