"""Конфигурация приложения — единственная точка чтения окружения.

Любой модуль, которому нужны настройки, импортирует их отсюда.
Чтение os.environ в остальном коде запрещено: разбросанные обращения
к окружению делают конфигурацию неотслеживаемой и непроверяемой.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения.

    Все поля обязательны без дефолтов сознательно: отсутствие переменной
    должно ронять приложение при старте с указанием конкретного поля,
    а не превращаться в молчаливый None, который всплывёт позже
    в непонятном месте.
    """

    # extra="ignore": в .env лежат и переменные для docker compose
    # (POSTGRES_*) — приложению они не нужны, но валить старт не должны.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    files_dir: Path
    # Подпись session-cookie (T2.2). Не короче 32 символов: пустой или
    # игрушечный секрет = подделываемая сессия, лучше не стартовать вовсе.
    secret_key: str = Field(min_length=32)


@lru_cache
def get_settings() -> Settings:
    """Возвращает настройки, прочитанные один раз на процесс.

    Функция, а не модульная константа, чтобы импорт модуля не требовал
    настроенного окружения (важно для тестов), а кэш — чтобы .env
    не перечитывался на каждый запрос.
    """
    return Settings()


class SeedSettings(BaseSettings):
    """Данные seed-скрипта — семья из трёх человек, две учётки взрослых.

    Живёт здесь, а не в app/seed.py, чтобы чтение окружения оставалось
    в одном модуле (ADR-001). Источник — .env.seed: файл в .gitignore,
    в git только шаблон .env.seed.example (ADR-009 — реальные данные
    семьи не попадают в репозиторий).

    Все поля обязательные: запуск с незаполненным шаблоном падает
    с именем поля до какого-либо обращения к БД. Пароль не короче 8 —
    в том числе отсекает пустые плейсхолдеры шаблона.
    """

    model_config = SettingsConfigDict(
        env_file=".env.seed", env_file_encoding="utf-8", env_prefix="SEED_", extra="ignore"
    )

    adult1_last_name: str = Field(min_length=1)
    adult1_first_name: str = Field(min_length=1)
    adult1_middle_name: str | None = None
    adult1_birth_date: date
    adult1_sex: str
    adult1_email: str
    adult1_password: str = Field(min_length=8)

    adult2_last_name: str = Field(min_length=1)
    adult2_first_name: str = Field(min_length=1)
    adult2_middle_name: str | None = None
    adult2_birth_date: date
    adult2_sex: str
    adult2_email: str
    adult2_password: str = Field(min_length=8)

    child_last_name: str = Field(min_length=1)
    child_first_name: str = Field(min_length=1)
    child_middle_name: str | None = None
    child_birth_date: date
    child_sex: str

    @field_validator("adult1_middle_name", "adult2_middle_name", "child_middle_name", mode="before")
    @classmethod
    def _empty_middle_name_is_none(cls, value):
        # Пустая строка в .env.seed (поле оставлено незаполненным) = нет отчества.
        return value or None

    @field_validator("adult1_email", "adult2_email")
    @classmethod
    def _email_lowercase(cls, value: str) -> str:
        # В БД email всегда lowercase; вход нормализует так же
        # (services/auth) — регистр не может сломать логин.
        return value.strip().lower()
