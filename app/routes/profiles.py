"""Переключение активного профиля."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.repositories.members import list_by_family
from app.routes.deps import get_current_account
from app.services.profiles import SESSION_ACTIVE_MEMBER_KEY

router = APIRouter()


@router.post("/profile/{member_id}")
def select_profile(
    member_id: int,
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> RedirectResponse:
    """Выбрать, чью карту смотрим. Обычная форма — работает без JS.

    Чужой и несуществующий id неразличимы (оба 404): не подтверждаем
    существование чужих идентификаторов.
    """
    family_members = list_by_family(db, account.member.family_id)
    if member_id not in {member.id for member in family_members}:
        raise HTTPException(status_code=404)
    request.session[SESSION_ACTIVE_MEMBER_KEY] = member_id
    return RedirectResponse("/", status_code=303)
