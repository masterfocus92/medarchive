"""Доступ к записям о здоровье.

ИНВАРИАНТ: фильтр soft delete — здесь, по умолчанию, во всех выборках.
Любой запрос без исключения удалённых — осознанное решение с отдельной
функцией, а не флагом по месту вызова.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import HealthRecord


def count_by_patient(session: Session, patient_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(HealthRecord)
        .where(
            HealthRecord.patient_id == patient_id,
            HealthRecord.deleted_at.is_(None),
        )
    )
