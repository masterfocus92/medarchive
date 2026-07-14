"""Доступ к записям о здоровье.

ИНВАРИАНТ: фильтр soft delete — здесь, по умолчанию, во всех выборках.
Любой запрос без исключения удалённых — осознанное решение с отдельной
функцией, а не флагом по месту вызова.
"""

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.models import FamilyMember, HealthRecord, ParseStatus

FEED_SORTS = ("created", "event")


def list_by_patient(session: Session, patient_id: int, sort: str = "created") -> list[HealthRecord]:
    """Лента профиля (❓1 потока просмотра): «по внесению» — хроника того,
    что вносили; «по событию» — медицинская хронология, записи без даты
    события встают по дате внесения. Удалённые отфильтрованы по умолчанию."""
    if sort == "event":
        order = (
            func.coalesce(HealthRecord.event_date, cast(HealthRecord.created_at, Date)).desc(),
            HealthRecord.created_at.desc(),
        )
    else:
        order = (HealthRecord.created_at.desc(),)
    return list(
        session.scalars(
            select(HealthRecord)
            .where(
                HealthRecord.patient_id == patient_id,
                HealthRecord.deleted_at.is_(None),
            )
            .order_by(*order)
        )
    )


def count_by_patient(session: Session, patient_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(HealthRecord)
        .where(
            HealthRecord.patient_id == patient_id,
            HealthRecord.deleted_at.is_(None),
        )
    )


def get_for_family(session: Session, record_id: int, family_id: int) -> HealthRecord | None:
    """Запись, если она принадлежит семье. Чужая, удалённая и несуществующая
    неразличимы (None → 404) — не подтверждаем существование чужих данных."""
    return session.scalar(
        select(HealthRecord)
        .join(FamilyMember, HealthRecord.patient_id == FamilyMember.id)
        .where(
            HealthRecord.id == record_id,
            FamilyMember.family_id == family_id,
            HealthRecord.deleted_at.is_(None),
        )
    )


def list_awaiting_review(session: Session, family_id: int) -> list[HealthRecord]:
    """Записи, ждущие человека: терминальный конвейер, но не подтверждены
    (предикат из ADR-012). Вход на экран проверки до появления ленты (Э5)."""
    return list(
        session.scalars(
            select(HealthRecord)
            .join(FamilyMember, HealthRecord.patient_id == FamilyMember.id)
            .where(
                FamilyMember.family_id == family_id,
                HealthRecord.parse_status.in_(
                    [ParseStatus.PARSED.value, ParseStatus.PARSE_FAILED.value]
                ),
                HealthRecord.confirmed_at.is_(None),
                HealthRecord.deleted_at.is_(None),
            )
            .order_by(HealthRecord.created_at.desc())
        )
    )
