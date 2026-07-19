"""Поиск-чат (flows/search.md): приглашение и вопрос-ответ.

Доступ закрыт default-deny middleware (ADR-010) — как у всех экранов.
Роут тонкий: вся логика поиска — в services/search_chat.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.repositories.members import list_by_family
from app.routes.deps import get_app_settings, get_current_account
from app.routes.pages import templates
from app.services.profiles import switcher_context
from app.services.search_chat import ask

router = APIRouter()


def _render(request: Request, account: Account, db: Session, question: str, result) -> HTMLResponse:
    members = list_by_family(db, account.member.family_id)
    context = switcher_context(request.session, account, members)
    context.update({"question": question, "result": result})
    return templates.TemplateResponse(request, "search.html", context)


@router.get("/search", response_class=HTMLResponse)
def search_screen(
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Пустой чат с приглашением (t₀ потока)."""
    return _render(request, account, db, question="", result=None)


@router.post("/search", response_class=HTMLResponse)
def search_ask(
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
    question: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Вопрос → ответ с источниками или честная деградация (B1–B6).

    Пустой вопрос возвращает приглашение (B1: result None) — сервис
    в этом случае не дёргает ни провайдеров, ни журнал.
    """
    result = ask(db, get_app_settings(request), account.member.family_id, question)
    return _render(request, account, db, question=question.strip(), result=result)
