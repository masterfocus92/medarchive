"""Общая инфраструктура интеграционных тестов с БД.

Тесты работают в отдельных БД (medcard_test_*), основная medcard
не трогается. Если docker с Postgres не поднят — зависимые тесты
пропускаются с подсказкой, а не краснеют: красный прогон должен
означать дефект кода, а не невключённый docker.
"""

import os

import psycopg
import pytest
from alembic.config import Config

# Креды — как у docker-compose: дефолты совпадают с .env.example,
# переопределяются тем же окружением, что читает compose.
PG_USER = os.environ.get("POSTGRES_USER", "medcard")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "medcard")
PG_HOST = "localhost:5432"

ADMIN_URL = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}/postgres"


def db_url(name: str) -> str:
    return f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}/{name}"


def alembic_config(target_db_url: str) -> Config:
    # URL передаётся явно, чтобы миграции шли в тестовую БД,
    # а не в medcard из настроек приложения.
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", target_db_url)
    return config


def recreate_db(admin_conn, name: str) -> None:
    admin_conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin_conn.execute(f"CREATE DATABASE {name}")


def drop_db(admin_conn, name: str) -> None:
    admin_conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture(scope="session")
def admin_conn():
    """Соединение со служебной БД postgres для создания/удаления тестовых БД."""
    try:
        conn = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("БД недоступна. Подними её: docker compose up -d")
    yield conn
    conn.close()
