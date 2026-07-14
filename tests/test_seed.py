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
from app.seed import detect_placeholders, format_validation_error, run_seed, seed_drift

SEED_TEST_DB = "medcard_test_seed"


def _test_settings(**overrides) -> SeedSettings:
    data = {
        "adult1_last_name": "Тестов",
        "adult1_first_name": "Родитель",
        "adult1_middle_name": "Первый",
        "adult1_birth_date": date(1990, 1, 1),
        "adult1_sex": "male",
        "adult1_email": "parent1@test.local",
        "adult1_password": "secret-password-1",
        "adult2_last_name": "Тестова",
        "adult2_first_name": "Родитель",
        "adult2_middle_name": None,  # отчество опционально (T2.7)
        "adult2_birth_date": date(1992, 2, 2),
        "adult2_sex": "female",
        "adult2_email": "parent2@test.local",
        "adult2_password": "secret-password-2",
        "child_last_name": "Тестова",
        "child_first_name": "Дочь",
        "child_middle_name": "Первая",
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
        assert without_account[0].first_name == "Дочь"
        # full_name собирается свойством из трёх полей.
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


# ---------- T2.6: точная причина ошибки валидации ----------


def _validation_error(**overrides) -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        _test_settings(**overrides)
    return exc_info.value


def test_error_message_blank_password_says_not_filled():
    message = format_validation_error(_validation_error(adult1_password=""))

    assert "adult1_password — не заполнено" in message


def test_error_message_short_password_says_too_short():
    message = format_validation_error(_validation_error(adult1_password="short"))

    assert "adult1_password — короче 8 символов" in message
    assert "не заполнено" not in message


def test_error_message_mixed_reports_each_reason():
    message = format_validation_error(
        _validation_error(adult1_password="", adult2_password="short")
    )

    assert "adult1_password — не заполнено" in message
    assert "adult2_password — короче 8 символов" in message


def test_error_message_missing_field_says_not_filled(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SEED_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValidationError) as exc_info:
        SeedSettings(_env_file=None)

    message = format_validation_error(exc_info.value)
    assert "adult1_last_name — не заполнено" in message


# ---------- T2.6: детект плейсхолдеров шаблона ----------


def test_placeholders_are_detected():
    settings = _test_settings(adult1_last_name="Фамилия", adult2_email="parent2@example.com")

    fields = detect_placeholders(settings)

    assert "adult1_last_name" in fields
    assert "adult2_email" in fields


def test_real_data_has_no_placeholders():
    assert detect_placeholders(_test_settings()) == []


def test_seed_emails_are_normalized_to_lowercase():
    # Email нормализуется на входе в настройки: в БД всегда lowercase,
    # вход сравнивает так же — регистр не может сломать логин.
    settings = _test_settings(adult1_email="Parent1@TEST.local")

    assert settings.adult1_email == "parent1@test.local"


# ---------- T2.6: сверка БД с файлом при «данные уже есть» ----------


@pytest.fixture(scope="module")
def drift_engine(admin_conn):
    """Отдельная БД, засеянная эталонными настройками."""
    recreate_db(admin_conn, "medcard_test_drift")
    command.upgrade(alembic_config(db_url("medcard_test_drift")), "head")
    engine = create_engine(db_url("medcard_test_drift"))
    with Session(engine) as session:
        run_seed(_test_settings(), session)
    yield engine
    engine.dispose()
    drop_db(admin_conn, "medcard_test_drift")


def test_no_drift_when_db_matches_file(drift_engine):
    with Session(drift_engine) as session:
        assert seed_drift(_test_settings(), session) == []


def test_drift_names_member_and_fields_but_not_values(drift_engine):
    changed = _test_settings(
        child_middle_name="Другая",
        adult1_birth_date=date(1991, 12, 31),
        adult2_email="new-address@test.local",
    )

    with Session(drift_engine) as session:
        drift = seed_drift(changed, session)

    text = "; ".join(drift)
    assert "ребёнок" in text and "отчество" in text
    assert "взрослый 1" in text and "дата рождения" in text
    assert "взрослый 2" in text and "email" in text
    # Значения приватны: в выводе их нет (терминал попадает в скриншоты/логи).
    assert "Другая" not in text
    assert "1991" not in text
    assert "new-address@test.local" not in text


def test_seed_rejects_blank_name():
    # Фамилия и имя обязательны — пустой плейсхолдер шаблона режется валидацией.
    with pytest.raises(ValidationError) as exc_info:
        _test_settings(adult1_last_name="")

    assert "adult1_last_name" in str(exc_info.value)


def test_seed_rejects_blank_password():
    # Шаблон .env.seed.example оставляет пароли пустыми — запуск
    # «как есть» обязан падать с указанием поля, ничего не создав.
    with pytest.raises(ValidationError) as exc_info:
        _test_settings(adult1_password="")

    assert "adult1_password" in str(exc_info.value)
