"""Подключение к БД: engine и session-фабрика.

Синхронный SQLAlchemy (ADR-007): для одной семьи на self-host VPS
async не даёт ничего, а стоит отдельного драйверного стека и сложности
в каждом тесте. FastAPI выполняет sync-зависимости в threadpool.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # Ленивая инициализация: импорт модуля не требует настроенного
    # окружения — каркас приложения обязан стартовать без .env (ADR-005).
    return create_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос, закрытие гарантировано."""
    with get_sessionmaker()() as session:
        yield session
