"""Вход и выход."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.middleware import SESSION_ACCOUNT_KEY
from app.routes.pages import templates
from app.services.auth import authenticate

router = APIRouter()

# Текст одинаковый для «нет email» и «неверный пароль» — форма не должна
# быть оракулом существующих email. Финальная формулировка — T2.3-FE.
LOGIN_ERROR = "Пара email–пароль не подошла. Проверьте раскладку и попробуйте ещё раз."


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None, "email": ""})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_session)],
):
    account = authenticate(db, email, password)
    if account is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": LOGIN_ERROR, "email": email}
        )
    request.session[SESSION_ACCOUNT_KEY] = account.id
    # 303: после POST браузер уходит GET'ом, форма не переотправляется.
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    # clear() безопасен и без сессии — logout не падает никогда.
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
