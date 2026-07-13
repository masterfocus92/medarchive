"""Интеграционные тесты доменных моделей и миграций (T1.3-BE).

Общая инфраструктура (тестовые БД, alembic, skip без docker) — conftest.py.
Constraints проверяются на настоящем Postgres, не на SQLite: уникальности,
server_default и CHECK — ровно то, что подменой диалекта не проверить.
"""

from datetime import date

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models.family import Account, Family, FamilyMember
from app.models.record import HealthRecord, RecordFile

TEST_DB = "medcard_test"
MIGRATION_TEST_DB = "medcard_test_migration"

DOMAIN_TABLES = {"families", "family_members", "accounts", "health_records", "record_files"}


def test_migration_cycle_up_down_up(admin_conn):
    """Критерий приёмки: upgrade → downgrade без остатка → повторный upgrade."""
    recreate_db(admin_conn, MIGRATION_TEST_DB)
    url = db_url(MIGRATION_TEST_DB)
    config = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(config, "head")
        assert DOMAIN_TABLES <= set(inspect(engine).get_table_names())

        command.downgrade(config, "base")
        # После отката остаётся только служебная таблица alembic (пустая).
        leftovers = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert leftovers == set(), f"downgrade оставил таблицы: {leftovers}"

        command.upgrade(config, "head")
        assert DOMAIN_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        drop_db(admin_conn, MIGRATION_TEST_DB)


@pytest.fixture(scope="module")
def engine(admin_conn):
    """Чистая тестовая БД, схема — реальной миграцией, не create_all:
    тесты проверяют то, что уедет на сервер, а не метаданные моделей."""
    recreate_db(admin_conn, TEST_DB)
    command.upgrade(alembic_config(db_url(TEST_DB)), "head")
    eng = create_engine(db_url(TEST_DB))
    yield eng
    eng.dispose()
    drop_db(admin_conn, TEST_DB)


@pytest.fixture
def session(engine):
    """Сессия в транзакции с откатом: тесты не видят данных друг друга."""
    with engine.connect() as conn:
        trans = conn.begin()
        sess = Session(bind=conn)
        yield sess
        sess.close()
        # После IntegrityError транзакция уже откачена самим SQLAlchemy —
        # повторный rollback дал бы SAWarning на каждый негативный тест.
        if trans.is_active:
            trans.rollback()


def _make_member(family: Family, **overrides) -> FamilyMember:
    defaults = {
        "full_name": "Иванова Анна Дмитриевна",
        "birth_date": date(2024, 1, 10),
        "sex": "female",
    }
    return FamilyMember(family=family, **{**defaults, **overrides})


def test_family_name_has_default(session):
    # Продуктовое решение: семья всегда названа, дефолт — «Семья».
    family = Family()
    session.add(family)
    session.flush()
    session.refresh(family)

    assert family.name == "Семья"


def test_member_without_account_is_valid(session):
    # Явное требование владельца: у ребёнка учётки может НЕ быть.
    # Ничто в схеме не должно требовать учётку у члена семьи.
    family = Family()
    member = _make_member(family)
    session.add(member)
    session.flush()

    assert member.id is not None
    assert member.account is None


def test_account_email_unique(session):
    family = Family()
    first = _make_member(family, full_name="Иванов Дмитрий")
    second = _make_member(family, full_name="Иванова Мария")
    session.add_all(
        [
            Account(member=first, email="ivanov@example.com", password_hash="x"),
            Account(member=second, email="ivanov@example.com", password_hash="y"),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_one_account_per_member(session):
    family = Family()
    member = _make_member(family)
    session.add_all(
        [
            Account(member=member, email="a@example.com", password_hash="x"),
            Account(member=member, email="b@example.com", password_hash="y"),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()


def _make_record(session) -> HealthRecord:
    family = Family()
    patient = _make_member(family)
    author_member = _make_member(family, full_name="Иванов Дмитрий", sex="male")
    author = Account(member=author_member, email="author@example.com", password_hash="x")
    record = HealthRecord(author=author, patient=patient, comment="первый приём")
    session.add(record)
    return record


def test_record_created_at_set_by_database(session):
    # Инвариант: дата_создания = момент внесения. Проставляет БД,
    # приложение её не передаёт — подделать «задним числом» нельзя.
    record = _make_record(session)
    session.flush()
    session.refresh(record)

    assert record.created_at is not None
    assert record.deleted_at is None


def test_file_position_unique_within_record(session):
    # Порядок файлов значим (страницы документа) — дубль позиции
    # внутри одной записи должен резаться constraint'ом, не приложением.
    record = _make_record(session)
    session.add_all(
        [
            RecordFile(
                record=record,
                position=1,
                stored_path="a/1.jpg",
                original_name="1.jpg",
                mime_type="image/jpeg",
                size_bytes=100,
            ),
            RecordFile(
                record=record,
                position=1,
                stored_path="a/2.jpg",
                original_name="2.jpg",
                mime_type="image/jpeg",
                size_bytes=200,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()
