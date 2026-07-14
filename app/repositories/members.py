"""Доступ к членам семьи.

Фильтра soft delete нет сознательно: члены семьи в POC не удаляются
(состав фиксирован сидом). Записи о здоровье — другое дело (этап 6).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FamilyMember


def list_by_family(session: Session, family_id: int) -> list[FamilyMember]:
    # Порядок стабильный, по id: как создал seed — взрослые, потом дочь.
    return list(
        session.scalars(
            select(FamilyMember)
            .where(FamilyMember.family_id == family_id)
            .order_by(FamilyMember.id)
        )
    )
