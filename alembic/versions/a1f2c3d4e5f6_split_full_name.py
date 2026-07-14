"""family_members: full_name -> last_name + first_name + middle_name (T2.7)

Миграция с переносом данных: существующие ФИО «Фамилия Имя Отчество»
сплитятся по пробелам (1-е слово — фамилия, 2-е — имя, остаток — отчество).
Downgrade собирает строку обратно — для трёхсловных имён без потерь.

Revision ID: a1f2c3d4e5f6
Revises: e6bc69637424
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1f2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e6bc69637424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Шаг 1: колонки nullable — чтобы перенести данные до запрета NULL.
    op.add_column("family_members", sa.Column("last_name", sa.String(), nullable=True))
    op.add_column("family_members", sa.Column("first_name", sa.String(), nullable=True))
    op.add_column("family_members", sa.Column("middle_name", sa.String(), nullable=True))

    # Шаг 2: перенос. Для одно-словного ФИО имя дублирует фамилию —
    # лучше странное имя, чем упавший NOT NULL на живых данных.
    op.execute(
        """
        UPDATE family_members SET
          last_name  = split_part(full_name, ' ', 1),
          first_name = COALESCE(
              NULLIF(split_part(full_name, ' ', 2), ''),
              split_part(full_name, ' ', 1)
          ),
          middle_name = NULLIF(
              array_to_string((string_to_array(full_name, ' '))[3:], ' '),
              ''
          )
        """
    )

    # Шаг 3: обязательность и удаление старой колонки.
    op.alter_column("family_members", "last_name", nullable=False)
    op.alter_column("family_members", "first_name", nullable=False)
    op.drop_column("family_members", "full_name")


def downgrade() -> None:
    op.add_column("family_members", sa.Column("full_name", sa.String(), nullable=True))
    # concat_ws пропускает NULL — отчество не оставляет хвостовой пробел.
    op.execute(
        "UPDATE family_members SET full_name = concat_ws(' ', last_name, first_name, middle_name)"
    )
    op.alter_column("family_members", "full_name", nullable=False)
    op.drop_column("family_members", "middle_name")
    op.drop_column("family_members", "first_name")
    op.drop_column("family_members", "last_name")
