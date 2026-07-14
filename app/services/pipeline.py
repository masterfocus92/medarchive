"""Конвейер разбора (T4.3): uploaded → parsing → parsed / parse_failed.

Запускается фоном (BackgroundTasks, ❓5) — поэтому работает в СОБСТВЕННОЙ
сессии БД, не в сессии запроса. Любой исход честен: провал разбора
никогда не трогает сохранённую запись (инвариант), а история прогонов
копится в extraction_runs (датасет качества).
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_sessionmaker
from app.models import ExtractionRun, HealthRecord, ParseStatus
from app.repositories.members import list_by_family
from app.services.extraction import ExtractionResult, Extractor, build_extractor
from app.services.imaging import prepare_for_vision

logger = logging.getLogger(__name__)

# Конвейер без вестей дольше этого срока считается зависшим (❓6):
# BackgroundTasks умирает вместе с процессом — ретрай это лечит.
STALE_AFTER = timedelta(minutes=10)

# Поля записи, которые конвейер имеет право заполнять — только пустые:
# правки человека не перезаписываются никогда.
_DRAFT_FIELDS = ("title", "event_date", "clinic", "doctor", "record_type", "content")


def can_retry(record: HealthRecord, now: datetime | None = None) -> bool:
    """Разрешён ли ручной перезапуск разбора (кнопка «Разобрать ещё раз»)."""
    now = now or datetime.now(UTC)
    if record.parse_status == ParseStatus.PARSE_FAILED:
        return True
    if record.parse_status in (ParseStatus.UPLOADED, ParseStatus.PARSING):
        return record.created_at < now - STALE_AFTER
    return False  # parsed — черновик уже есть; none — разбирать нечего


def run_extraction(
    record_id: int,
    *,
    settings: Settings | None = None,
    session_factory=None,
    files_dir: Path | None = None,
    extractor: Extractor | None = None,
) -> None:
    """Один прогон разбора. Все исключения гасятся в parse_failed —
    фоновая задача не имеет права умирать молча."""
    factory = session_factory or get_sessionmaker()
    if files_dir is None:
        assert settings is not None, "нужны settings или files_dir"
        files_dir = settings.files_dir

    with factory() as session:
        record = session.get(HealthRecord, record_id)
        if record is None or record.deleted_at is not None:
            return
        if record.parse_status not in (
            ParseStatus.UPLOADED,
            ParseStatus.PARSING,
            ParseStatus.PARSE_FAILED,
        ):
            # parsed/none — терминальные для конвейера, не трогаем.
            return

        # Статус виден в UI сразу (отдельный commit до долгого вызова).
        record.parse_status = ParseStatus.PARSING
        run = ExtractionRun(
            record_id=record.id,
            provider=settings.extractor_provider
            if settings
            else getattr(extractor, "provider", "?"),
            model=settings.extractor_model if settings else getattr(extractor, "model", "?"),
        )
        session.add(run)
        session.commit()

        try:
            active_extractor = extractor or build_extractor(settings)
            # Фактические provider/model — из адаптера (конфиг мог мутировать).
            run.provider = active_extractor.provider
            run.model = active_extractor.model

            pages = prepare_for_vision(
                [(f.mime_type, (files_dir / f.stored_path).read_bytes()) for f in record.files]
            )
            family = list_by_family(session, record.patient.family_id)
            result, raw = active_extractor.extract(pages, family)

            _apply_draft(record, result)
            record.parse_status = ParseStatus.PARSED
            run.status = "ok"
            run.raw_response = raw
        except Exception as exc:  # noqa: BLE001 — фон не умирает молча
            logger.warning("Разбор записи %s не удался: %s", record_id, exc)
            record.parse_status = ParseStatus.PARSE_FAILED
            run.status = "error"
            run.error = str(exc)
        finally:
            run.finished_at = func.now()
            session.commit()


def _apply_draft(record: HealthRecord, result: ExtractionResult) -> None:
    """Черновик ложится только в пустые поля; comment и patient_id —
    территория человека, конвейер их не касается вовсе."""
    for field in _DRAFT_FIELDS:
        if getattr(record, field) is None:
            setattr(record, field, getattr(result, field))
    # Предложение пациента — отдельная колонка, выбор человека не трогаем (B7).
    record.suggested_patient_id = result.suggested_patient_id


def find_stale_run_started(session: Session, record_id: int) -> datetime | None:
    return session.scalar(
        select(ExtractionRun.started_at)
        .where(ExtractionRun.record_id == record_id)
        .order_by(ExtractionRun.started_at.desc())
        .limit(1)
    )
