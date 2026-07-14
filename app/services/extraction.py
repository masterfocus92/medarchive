"""Контракт AI-экстрактора — язык домена (ADR-013).

Всё, что за пределами адаптеров, говорит только на этом языке:
никаких терминов провайдера (RouterAI, Claude, OpenAI-схема) снаружи.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from app.models import FamilyMember


class ExtractionResult(BaseModel):
    """Черновик полей записи, извлечённый из документа.

    None по каждому полю — честное «не разобрал»: экстрактор извлекает
    и цитирует, но не выдумывает (граница AI, OVERVIEW §4).
    """

    title: str | None = None
    event_date: date | None = None
    clinic: str | None = None
    doctor: str | None = None
    record_type: str | None = None
    content: str | None = None
    # Предложение пациента — id из переданного списка семьи, не затирает
    # выбор человека (ветка B7 потока).
    suggested_patient_id: int | None = None


class ExtractionError(Exception):
    """Разбор не удался; message — для журнала прогонов, не для пользователя."""


class ExtractorNotConfigured(ExtractionError):
    """Провайдер disabled или адаптер не реализован — конвейер честно
    кладёт parse_failed, сохранение записи не страдает (инвариант)."""


class Extractor(Protocol):
    """Любой экстрактор: файлы страниц + семья → черновик полей.

    files: список (mime, содержимое) в порядке страниц — уже в форматах,
    пригодных провайдеру (конвертация — забота вызывающего/imaging).
    """

    provider: str  # для extraction_runs.provider
    model: str  # для extraction_runs.model

    def extract(
        self, files: list[tuple[str, bytes]], family: list[FamilyMember]
    ) -> ExtractionResult: ...
