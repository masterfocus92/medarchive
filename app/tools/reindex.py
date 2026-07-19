"""Бэкфилл-переиндексация записей (T7.2, ADR-018).

Запуск: uv run python -m app.tools.reindex
Нужны .env (EMBEDDINGS_*) и поднятая БД.

Идемпотентен: индексирует только записи без вектора и записи с вектором
другой модели (несовпадение model — признак устаревшего вектора).
Применение: деплой этапа, смена модели, доиндексация после провалов B7.
"""

from sqlalchemy import or_, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import HealthRecord, RecordEmbedding
from app.repositories.embeddings import upsert
from app.services.embeddings import (
    EmbeddingProvider,
    EmbeddingsNotConfigured,
    build_embeddings,
)
from app.services.indexing import build_index_text

# Батч ограничивает и размер запроса к провайдеру, и объём потерь
# при обрыве: каждый батч коммитится отдельно, прерванный бэкфилл
# продолжается с места остановки (идемпотентность).
BATCH_SIZE = 32


def reindex(session_factory, provider: EmbeddingProvider) -> int:
    """Доиндексирует подтверждённые неудалённые записи. Возвращает,
    сколько записей проиндексировано."""
    with session_factory() as session:
        records = session.scalars(
            select(HealthRecord)
            .outerjoin(RecordEmbedding, RecordEmbedding.record_id == HealthRecord.id)
            .where(
                HealthRecord.deleted_at.is_(None),
                HealthRecord.confirmed_at.is_not(None),
                or_(
                    RecordEmbedding.record_id.is_(None),
                    RecordEmbedding.model != provider.model,
                ),
            )
            .order_by(HealthRecord.id)
        ).all()

        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start : start + BATCH_SIZE]
            vectors = provider.embed([build_index_text(record) for record in batch])
            for record, vector in zip(batch, vectors, strict=True):
                upsert(session, record.id, vector, provider.model)
            session.commit()

    return len(records)


def main() -> None:
    settings = get_settings()
    try:
        provider = build_embeddings(settings)
    except EmbeddingsNotConfigured:
        raise SystemExit(
            "Эмбеддинги отключены (EMBEDDINGS_PROVIDER=disabled) — заполни EMBEDDINGS_* в .env"
        ) from None

    indexed = reindex(get_sessionmaker(), provider)
    print(f"Проиндексировано записей: {indexed} (модель {provider.model})")


if __name__ == "__main__":
    main()
