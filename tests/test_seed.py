"""Тесты seed-скрипта (T2.1-BE).

Ключевое свойство по тикету: код seed не содержит данных, данные
приходят из SeedSettings (.env.seed вне git, ADR-009). Здесь данные
тестовые — передаются в SeedSettings напрямую, без env-файла.
"""

import os
from datetime import date

import bcrypt
import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.config import SeedSettings
from app.models import Account, Family, FamilyMember
from app.seed import run_seed

SEED_TEST_DB = "medcard_test_seed"


def _test_settings(**overrides) -> SeedSettings:
    data = {
        "adult1_full_name": "Тестов Родитель Первый",
        "adult1_birth_date": date(1990, 1, 1),
        "adult1_sex": "male",
        "adult1_email": "parent1@test.local",
        "adult1_password": "secret-password-1",
        "adult2_full_name": "Тестова Родитель Вторая",
        "adult2_birth_date": date(1992, 2, 2),
        "adult2_sex": "female",
        "adult2_email": "parent2@test.local",
        "adult2_password": "secret-password-2",
        "child_full_name": "Тестова Дочь Первая",
        "child_birth_date": date(2024, 3, 3),
        "child_sex": "female",
    }
    data.update(overrides)
    return SeedSettings(_env_file=None, **data)


@pytest.fixture(scope="module")
def engine(admin_conn):
    recreate_db(admin_conn, SEED_TEST_DB)
    command.upgrade(alembic_config(db_url(SEED_TEST_DB)), "head")
    eng = create_engine(db_url(SEED_TEST_DB))
    yield eng
    eng.dispose()
    drop_db(admin_conn, SEED_TEST_DB)


def _counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(Family)),
        session.scalar(select(func.count()).select_from(FamilyMember)),
        session.scalar(select(func.count()).select_from(Account)),
    )


def test_seed_creates_structure_and_is_idempotent(engine):
    # Первый запуск: ровно 1 семья, 3 члена, 2 учётки.
    with Session(engine) as session:
        assert run_seed(_test_settings(), session) is True

    with Session(engine) as session:
        assert _counts(session) == (1, 3, 2)

        accounts = session.scalars(select(Account)).all()
        # Оба взрослых — админы (по спеке), у ребёнка учётки нет.
        assert all(a.is_admin for a in accounts)
        with_account = {a.family_member_id for a in accounts}
        members = session.scalars(select(FamilyMember)).all()
        without_account = [m for m in members if m.id not in with_account]
        assert len(without_account) == 1
        assert without_account[0].full_name == "Тестова Дочь Первая"

        # Пароль хранится только bcrypt-хэшем.
        first = next(a for a in accounts if a.email == "parent1@test.local")
        assert first.password_hash != "secret-password-1"
        assert bcrypt.checkpw(b"secret-password-1", first.password_hash.encode("ascii"))

    # Повторный запуск: без дублей, без исключений, счётчики не растут.
    with Session(engine) as session:
        assert run_seed(_test_settings(), session) is False

    with Session(engine) as session:
        assert _counts(session) == (1, 3, 2)


def test_seed_settings_require_data(monkeypatch):
    # Негативный сценарий тикета: без файла данных seed не работает.
    # Чистим окружение от возможных SEED_* разработчика.
    for key in list(os.environ):
        if key.startswith("SEED_"):
            monkeypatch.delenv(key)

    with pytest.raises(ValidationError):
        SeedSettings(_env_file=None)


def test_seed_rejects_blank_password():
    # Шаблон .env.seed.example оставляет пароли пустыми — запуск
    # «как есть» обязан падать с указанием поля, ничего не создав.
    with pytest.raises(ValidationError) as exc_info:
        _test_settings(adult1_password="")

    assert "adult1_password" in str(exc_info.value)
