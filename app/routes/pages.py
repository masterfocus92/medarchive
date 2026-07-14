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
from app.routes.deps import get_current_account
from app.services.profiles import initials, resolve_active_member

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
    active = resolve_active_member(request.session, account, members)
    # Контракт T2.4-BE для переключателя (рендер — T2.5-FE).
    context = {
        "members": [
            {
                "id": member.id,
                "full_name": member.full_name,
                # Подпись под монограммой — имя без фамилии (шапка тесная).
                "first_name": member.first_name,
                "initials": initials(member.first_name, member.last_name),
                "is_active": member.id == active.id,
            }
            for member in members
        ],
        "active_member": active,
    }
    return templates.TemplateResponse(request, "index.html", context)
