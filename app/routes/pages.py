"""HTML-страницы приложения."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.repositories.members import list_by_family
from app.repositories.records import count_by_patient
from app.routes.deps import get_current_account
from app.services.profiles import switcher_context

router = APIRouter()

# Каталог шаблонов — от положения пакета, не от cwd (см. app/main.py).
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    members = list_by_family(db, account.member.family_id)
    context = switcher_context(request.session, account, members)
    context["records_count"] = count_by_patient(db, context["active_member"].id)
    # Flash-тост: положил-показал-стёр (контракт T3.2).
    context["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "index.html", context)
