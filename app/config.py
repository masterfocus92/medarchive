"""Конфигурация приложения — единственная точка чтения окружения.

Любой модуль, которому нужны настройки, импортирует их отсюда.
Чтение os.environ в остальном коде запрещено: разбросанные обращения
к окружению делают конфигурацию неотслеживаемой и непроверяемой.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения.

    Оба поля обязательны без дефолтов сознательно: отсутствие переменной
    должно ронять приложение при старте с указанием конкретного поля,
    а не превращаться в молчаливый None, который всплывёт позже
    в непонятном месте.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    files_dir: Path


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
        env_file=".env.seed", env_file_encoding="utf-8", env_prefix="SEED_"
    )

    adult1_full_name: str
    adult1_birth_date: date
    adult1_sex: str
    adult1_email: str
    adult1_password: str = Field(min_length=8)

    adult2_full_name: str
    adult2_birth_date: date
    adult2_sex: str
    adult2_email: str
    adult2_password: str = Field(min_length=8)

    child_full_name: str
    child_birth_date: date
    child_sex: str
