"""Доступ к учётным записям.

Фильтра soft delete здесь нет сознательно: учётки в POC не удаляются
(двое операторов, состав фиксирован сидом).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account


def get_by_email(session: Session, email: str) -> Account | None:
    return session.scalar(select(Account).where(Account.email == email))
