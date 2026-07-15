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
from app.repositories.records import FEED_SORTS, list_by_patient
from app.routes.deps import get_current_account
from app.services.profiles import switcher_context
from app.services.ui import feed_badge

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
    feed = []
    for r in records:
        # Бейдж — только у неподтверждённых (❓2, правило в services/ui):
        # подтверждённая запись в ленте не носит подписи о состоянии.
        kind, label = feed_badge(r)
        feed.append(
            {
                "id": r.id,
                "title": r.title,
                "event_date": r.event_date,
                "created_at": r.created_at,
                "record_type": r.record_type,
                "clinic": r.clinic,
                "status_label": label,
                "status_kind": kind,
                "confirmed": r.confirmed_at is not None,
            }
        )
    context["records"] = feed
    # Flash-тост: положил-показал-стёр (контракт T3.2).
    context["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "index.html", context)
