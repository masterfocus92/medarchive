"""Статусная модель: конвейер отдельно, подтверждение отдельно (T3.5, ADR-012)

parse_status теперь только про файлы: none|uploaded|parsing|parsed|parse_failed.
Подтверждение человеком — новая колонка confirmed_at. Существующие
записи 'confirmed' (заметки без файлов, T3.2) мигрируют без потерь:
parse_status='none', confirmed_at=created_at.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_CHECK = "parse_status IN ('none', 'uploaded', 'parsing', 'parsed', 'parse_failed')"
OLD_CHECK = "parse_status IN ('uploaded', 'parsing', 'parsed', 'parse_failed', 'confirmed')"


def upgrade() -> None:
    op.add_column(
        "health_records",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Порядок важен: старый CHECK не знает 'none' — сначала снять его,
    # потом переносить данные, потом вешать новый.
    op.drop_constraint("parse_status_allowed", "health_records", type_="check")
    # Перенос: confirmed были только у записей без файлов (T3.2) —
    # момент подтверждения совпадает с созданием.
    op.execute(
        "UPDATE health_records SET parse_status = 'none', confirmed_at = created_at "
        "WHERE parse_status = 'confirmed'"
    )
    op.create_check_constraint("parse_status_allowed", "health_records", NEW_CHECK)
    op.alter_column("health_records", "parse_status", server_default="none")


def downgrade() -> None:
    # Симметричный порядок: снять CHECK до переноса данных.
    op.drop_constraint("parse_status_allowed", "health_records", type_="check")
    op.execute(
        "UPDATE health_records SET parse_status = 'confirmed' "
        "WHERE confirmed_at IS NOT NULL AND parse_status = 'none'"
    )
    op.create_check_constraint("parse_status_allowed", "health_records", OLD_CHECK)
    op.alter_column("health_records", "parse_status", server_default="uploaded")
    op.drop_column("health_records", "confirmed_at")
