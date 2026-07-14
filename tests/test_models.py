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
        "last_name": "Иванова",
        "first_name": "Анна",
        "middle_name": "Дмитриевна",
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


def test_member_without_middle_name_is_valid(session):
    # Отчество опционально (T2.7): есть не у всех, NULL честнее пустой строки.
    family = Family()
    member = _make_member(family, middle_name=None)
    session.add(member)
    session.flush()

    assert member.id is not None
    assert member.full_name == "Иванова Анна"  # без NULL-хвоста


def test_full_name_property_joins_three_parts(session):
    family = Family()
    member = _make_member(family)
    session.add(member)
    session.flush()

    assert member.full_name == "Иванова Анна Дмитриевна"


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
    first = _make_member(family, first_name="Дмитрий")
    second = _make_member(family, first_name="Мария")
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
    author_member = _make_member(family, first_name="Дмитрий", sex="male")
    author = Account(member=author_member, email="author@example.com", password_hash="x")
    record = HealthRecord(author=author, patient=patient, comment="первый приём")
    session.add(record)
    return record


def test_record_parse_status_defaults_to_none(session):
    # Дефолт ставит БД (T3.5): 'none' — самое безопасное состояние,
    # ничего не заявляющее о несуществующем конвейере.
    record = _make_record(session)
    session.flush()
    session.refresh(record)

    assert record.parse_status == "none"
    assert record.confirmed_at is None


def test_record_parse_status_rejects_unknown_value(session):
    from sqlalchemy import text

    record = _make_record(session)
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE health_records SET parse_status = 'weird' WHERE id = :id"),
            {"id": record.id},
        )


def test_record_parse_status_rejects_legacy_confirmed(session):
    # 'confirmed' — легаси T3.1: подтверждение переехало в confirmed_at (T3.5).
    from sqlalchemy import text

    record = _make_record(session)
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE health_records SET parse_status = 'confirmed' WHERE id = :id"),
            {"id": record.id},
        )


def test_migration_c9d0_converts_confirmed_with_data(admin_conn):
    """Миграция T3.5 на БД С ДАННЫМИ: старый 'confirmed' переезжает
    в none+confirmed_at. Пустые БД такой класс ошибок (порядок операций
    с CHECK) не ловят — поймано на живой dev-базе 14.07.2026."""
    from sqlalchemy import text

    recreate_db(admin_conn, "medcard_test_data_migration")
    url = db_url("medcard_test_data_migration")
    config = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(config, "b7c8d9e0f1a2")  # схема со старым 'confirmed'
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO families (name) VALUES ('Семья');"
                    "INSERT INTO family_members (family_id, last_name, first_name, birth_date, sex)"
                    " VALUES (1, 'Тестов', 'Оператор', '1990-01-01', 'male');"
                    "INSERT INTO accounts (family_member_id, email, password_hash, is_admin)"
                    " VALUES (1, 'op@test.local', 'x', true);"
                    "INSERT INTO health_records"
                    " (author_account_id, patient_id, comment, parse_status)"
                    " VALUES (1, 1, 'заметка', 'confirmed');"
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT parse_status, confirmed_at, created_at FROM health_records")
            ).one()
        assert row.parse_status == "none"
        assert row.confirmed_at == row.created_at
    finally:
        engine.dispose()
        drop_db(admin_conn, "medcard_test_data_migration")


def test_record_created_at_set_by_database(session):
    # Инвариант: дата_создания = момент внесения. Проставляет БД,
    # приложение её не передаёт — подделать «задним числом» нельзя.
    record = _make_record(session)
    session.flush()
    session.refresh(record)

    assert record.created_at is not None
    assert record.deleted_at is None


def test_extraction_run_records_provider_and_model(session):
    # Журнал прогонов (T4.1): датасет качества экстрактора и история ретраев.
    from app.models import ExtractionRun

    record = _make_record(session)
    session.flush()
    run = ExtractionRun(record_id=record.id, provider="openai_compatible", model="test-model")
    session.add(run)
    session.flush()
    session.refresh(run)

    assert run.status == "running"  # дефолт ставит БД
    assert run.started_at is not None
    assert run.finished_at is None
    assert run.raw_response is None  # артефакт провайдера, опционален


def test_extraction_run_rejects_unknown_status(session):
    from sqlalchemy import text

    from app.models import ExtractionRun

    record = _make_record(session)
    session.flush()
    run = ExtractionRun(record_id=record.id, provider="p", model="m")
    session.add(run)
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE extraction_runs SET status = 'weird' WHERE id = :id"),
            {"id": run.id},
        )


def test_record_suggested_patient_is_separate_from_choice(session):
    # Предложение AI не затирает выбор человека (ветка B7 потока).
    record = _make_record(session)
    session.flush()
    session.refresh(record)

    assert record.suggested_patient_id is None
    record.suggested_patient_id = record.patient_id
    session.flush()


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
