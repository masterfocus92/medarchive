"""Поиск: векторы записей и журнал вопросов (ADR-018).

Обе таблицы — производные данные: восстановимы reindex'ом и не участвуют
в доменных инвариантах записи. Отдельная таблица векторов (не колонка
health_records) — чтобы смена модели/размерности не трогала основную
таблицу (решение ❓7).
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Размерность выбранной модели (baai/bge-m3, ADR-018). Зашита в схему
# (vector(1024)) — смена модели с другой размерностью означает новую
# миграцию + reindex, это осознанная цена компактного хранения.
EMBEDDING_DIM = 1024


class RecordEmbedding(Base):
    """Вектор подтверждённой записи. PK = FK: у записи максимум один вектор,
    повторная индексация — перезапись (upsert), не накопление."""

    __tablename__ = "record_embeddings"

    record_id: Mapped[int] = mapped_column(ForeignKey("health_records.id"), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    # Какой моделью посчитан вектор: несовпадение с конфигом — признак
    # устаревшего вектора, лечится reindex'ом (ADR-018).
    model: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchQuery(Base):
    """Журнал поиска (❓10): датасет обещанного тюнинга точности.

    Аналог extraction_runs: пишется всегда, в UI не показывается,
    не чистится. candidates — [{record_id, distance}] на момент вопроса:
    id, а не снапшот полей — для тюнинга нужна геометрия выдачи,
    а не копия записей.
    """

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list] = mapped_column(JSONB)
    # NULL = ответа не было: retrieval пуст, LLM недоступен или не нашёл.
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
