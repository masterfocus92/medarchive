"""Общие зависимости роутов."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.middleware import SESSION_ACCOUNT_KEY
from app.models import Account


def get_current_account(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
) -> Account:
    """Учётка текущего оператора.

    Middleware уже не пускает неаутентифицированных, поэтому 401 здесь —
    страховка на аномалию (учётка исчезла из БД при живой сессии),
    а не рабочий путь.
    """
    account_id = request.session.get(SESSION_ACCOUNT_KEY)
    account = db.get(Account, account_id) if account_id is not None else None
    if account is None:
        raise HTTPException(status_code=401)
    return account
