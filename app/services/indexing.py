"""Индексация записей для поиска (T7.2): текст → эмбеддинг → upsert.

Запускается фоном после подтверждения (паттерн run_extraction): работает
в собственной сессии БД. Провал любого шага — warning-лог и выход (B7):
индексация никогда не блокирует подтверждение, запись доиндексируется
бэкфиллом (app.tools.reindex) или следующей правкой.
"""

import logging

from app.config import Settings
from app.db import get_sessionmaker
from app.models import HealthRecord
from app.repositories.embeddings import upsert
from app.services.embeddings import EmbeddingProvider, build_embeddings

logger = logging.getLogger(__name__)


def build_index_text(record: HealthRecord) -> str:
    """Индексируемый текст записи — все поля + имя пациента (❓5).

    Русские подписи полей не декорация: они дают эмбеддинг-модели
    контекст значения («Врач: Петрова» ≠ просто фамилия в тексте).
    Пустые поля пропускаются целиком — «Клиника: None» индексировать нельзя.
    """
    parts = [
        ("Пациент", record.patient.full_name),
        ("Название", record.title),
        ("Тип", record.record_type),
        ("Клиника", record.clinic),
        ("Врач", record.doctor),
        ("Дата события", record.event_date.isoformat() if record.event_date else None),
        ("Содержание", record.content),
        ("Заметка", record.comment),
    ]
    return "\n".join(f"{label}: {value}" for label, value in parts if value)


def index_record(
    record_id: int,
    *,
    settings: Settings | None = None,
    session_factory=None,
    provider: EmbeddingProvider | None = None,
) -> None:
    """Один прогон индексации. Все исключения гасятся в warning —
    фоновая задача не имеет права ни умереть молча, ни уронить продукт."""
    factory = session_factory or get_sessionmaker()
    try:
        with factory() as session:
            record = session.get(HealthRecord, record_id)
            if record is None or record.deleted_at is not None:
                return
            if record.confirmed_at is None:
                # В поиске только подтверждённое (ADR-012) — черновики
                # не индексируются, даже если задачу поставили по ошибке.
                return

            active_provider = provider or build_embeddings(settings)
            vector = active_provider.embed([build_index_text(record)])[0]
            upsert(session, record.id, vector, active_provider.model)
            session.commit()
    except Exception as exc:  # noqa: BLE001 — B7: провал не блокирует ничего
        logger.warning("Индексация записи %s не удалась: %s", record_id, exc)
