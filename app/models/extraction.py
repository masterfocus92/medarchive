"""Журнал прогонов AI-разбора.

Каждый прогон (включая ретраи) — строка навсегда: это и отладка,
и датасет качества экстрактора (риск №2 OVERVIEW). raw_response —
артефакт провайдера (ADR-013): непереносим, бизнес-логика на него
не опирается, при смене провайдера не мигрирует.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'ok', 'error')", name="status_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("health_records.id"), index=True)
    provider: Mapped[str]
    model: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="running")
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
