"""Поиск (Э7, ADR-018): расширение vector, векторы записей, журнал вопросов.

record_embeddings: PK = FK на запись (максимум один вектор), размерность
1024 — baai/bge-m3. ANN-индекс не создаётся сознательно: на семейных
объёмах (сотни строк) точный поиск быстрее и точнее.
search_queries: датасет тюнинга (❓10), пишется всегда, не чистится.

Revision ID: d4e5f6a7b8c9
Revises: 39f593f639da
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "39f593f639da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Образ pgvector/pgvector (dev, ADR-003) и нативный Postgres на VPS
    # (ADR-017) содержат расширение — миграция лишь включает его в БД.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "record_embeddings",
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("health_records.id"), primary_key=True),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("search_queries")
    op.drop_table("record_embeddings")
    # Симметрия up→down: расширение включила эта миграция — она и выключает.
    op.execute("DROP EXTENSION IF EXISTS vector")
