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


def test_missing_database_url_fails_loudly(monkeypatch):
    monkeypatch.setenv("FILES_DIR", "./files")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    # Сообщение должно указывать на конкретное поле —
    # иначе «внятная ошибка при старте» не выполняется.
    assert "database_url" in str(exc_info.value).lower()


def test_missing_files_dir_fails_loudly(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "files_dir" in str(exc_info.value).lower()


def test_reads_values_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("FILES_DIR", "./files")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/db"
    assert str(settings.files_dir) == "files"
