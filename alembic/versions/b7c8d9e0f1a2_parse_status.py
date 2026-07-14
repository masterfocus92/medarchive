"""health_records: parse_status — статус конвейера разбора (T3.1)

Все пять значений потока (flows/record-creation.md §6) заводятся сразу,
чтобы этап 4 не требовал миграции. Существующие записи получают
'uploaded' через server_default.

Revision ID: b7c8d9e0f1a2
Revises: a1f2c3d4e5f6
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "a1f2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "health_records",
        sa.Column("parse_status", sa.String(), server_default="uploaded", nullable=False),
    )
    op.create_check_constraint(
        "parse_status_allowed",
        "health_records",
        "parse_status IN ('uploaded', 'parsing', 'parsed', 'parse_failed', 'confirmed')",
    )


def downgrade() -> None:
    # Короткое имя: naming convention сама добавит префикс ck_health_records_.
    op.drop_constraint("parse_status_allowed", "health_records", type_="check")
    op.drop_column("health_records", "parse_status")
