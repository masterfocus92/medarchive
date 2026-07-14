"""HTML-страницы приложения."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PARSE_STATUS_LABELS, Account, ParseStatus
from app.repositories.members import list_by_family
from app.repositories.records import FEED_SORTS, list_by_patient
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

    # Лента активного профиля (Э5, ❓1): невалидный sort — молча дефолт.
    sort = request.query_params.get("sort", "created")
    if sort not in FEED_SORTS:
        sort = "created"
    records = list_by_patient(db, context["active_member"].id, sort)
    context["sort"] = sort
    context["records"] = [
        {
            "id": r.id,
            "title": r.title,
            "event_date": r.event_date,
            "created_at": r.created_at,
            "record_type": r.record_type,
            "clinic": r.clinic,
            # Бейдж — только у неподтверждённых (❓2): подтверждённая
            # запись в ленте не нуждается в подписи о своём состоянии.
            "status_label": None
            if r.confirmed_at is not None
            else PARSE_STATUS_LABELS.get(ParseStatus(r.parse_status)),
            "confirmed": r.confirmed_at is not None,
        }
        for r in records
    ]
    # Flash-тост: положил-показал-стёр (контракт T3.2).
    context["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "index.html", context)
