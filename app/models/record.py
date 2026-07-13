"""Запись о здоровье и её файлы.

ИНВАРИАНТ (не выразим на уровне СУБД — файл и комментарий живут
в разных таблицах): запись содержит хотя бы один файл ИЛИ непустой
комментарий, пустых записей не существует. Обеспечивается сервисным
слоем создания записи (этап 3) — единственной точкой, через которую
записи появляются.
"""

from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.family import Account, FamilyMember


class HealthRecord(Base):
    """Структурированный факт о здоровье + опциональный комментарий."""

    __tablename__ = "health_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Автор — FK на учётку, не на члена семьи: инвариант «ребёнок без
    # учётки не может быть автором» обеспечен структурой (ADR-006).
    author_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # Пациент — любой член семьи, учётка не нужна.
    patient_id: Mapped[int] = mapped_column(ForeignKey("family_members.id"), index=True)
    # Инвариант «дата_создания = момент внесения»: проставляет БД,
    # приложение значение не передаёт — записи задним числом невозможны.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Всё ниже — опционально: ничто не блокирует сохранение (главный
    # принцип продукта — минимум трения при вводе).
    event_date: Mapped[date | None]  # реальная дата события ≠ дата внесения
    title: Mapped[str | None]
    clinic: Mapped[str | None]
    doctor: Mapped[str | None]
    # Открытое поле по спеке: AI предлагает, человек волен вписать своё.
    # Enum здесь — против продукта.
    record_type: Mapped[str | None]
    content: Mapped[str | None] = mapped_column(Text)  # текст из документа
    comment: Mapped[str | None] = mapped_column(Text)  # «карандаш» — пометка человека

    # Soft delete: физически записи не стираются. Фильтрация удалённых —
    # обязанность репозитория по умолчанию (инвариант, этап 6).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))

    author: Mapped[Account] = relationship(foreign_keys=[author_account_id])
    patient: Mapped[FamilyMember] = relationship()
    files: Mapped[list["RecordFile"]] = relationship(
        back_populates="record", order_by="RecordFile.position"
    )


class RecordFile(Base):
    """Файл записи. Коллекция упорядочена: position — страницы документа."""

    __tablename__ = "record_files"
    __table_args__ = (
        # Порядок страниц значим — дубль позиции внутри записи режется схемой.
        UniqueConstraint("record_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("health_records.id"), index=True)
    position: Mapped[int]
    # Путь относительно FILES_DIR — хранилище переносимо между машинами.
    stored_path: Mapped[str]
    original_name: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]

    record: Mapped[HealthRecord] = relationship(back_populates="files")
