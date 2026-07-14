"""Тесты модуля конфигурации.

Конфиг — единственная точка чтения окружения (T1.1-INFRA).
Главный негативный сценарий: отсутствие обязательной переменной
должно ронять приложение при старте громко и понятно,
а не всплывать молчаливым None где-то в глубине позже.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Изолируем тесты от реального окружения разработчика и его .env:
    # тест должен вести себя одинаково на любой машине.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FILES_DIR", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)


TEST_SECRET = "test-secret-key-only-for-tests-0123456789"


def test_missing_database_url_fails_loudly(monkeypatch):
    monkeypatch.setenv("FILES_DIR", "./files")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    # Сообщение должно указывать на конкретное поле —
    # иначе «внятная ошибка при старте» не выполняется.
    assert "database_url" in str(exc_info.value).lower()


def test_missing_files_dir_fails_loudly(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "files_dir" in str(exc_info.value).lower()


def test_short_secret_key_rejected(monkeypatch):
    # Игрушечный секрет = подделываемая сессия — не стартуем (T2.2).
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("FILES_DIR", "./files")
    monkeypatch.setenv("SECRET_KEY", "short")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "secret_key" in str(exc_info.value).lower()


def test_ignores_extra_env_keys(tmp_path, monkeypatch):
    # Регрессия: в .env лежат и ключи для docker compose (POSTGRES_*) —
    # они не должны валить старт приложения (extra="ignore").
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://u:p@localhost:5432/db\n"
        "FILES_DIR=./files\n"
        f"SECRET_KEY={TEST_SECRET}\n"
        "POSTGRES_USER=medcard\n"
        "POSTGRES_PASSWORD=medcard\n"
        "POSTGRES_DB=medcard\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.database_url.endswith("/db")


def test_reads_values_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("FILES_DIR", "./files")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/db"
    assert str(settings.files_dir) == "files"
