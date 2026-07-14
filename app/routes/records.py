"""Создание записи: форма и приём (flows/record-creation.md, t₀–t₂+ε)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.repositories.members import list_by_family
from app.routes.deps import get_app_settings, get_current_account
from app.routes.pages import templates
from app.services.profiles import switcher_context
from app.services.records import (
    EmptyRecordError,
    FileValidationError,
    UnknownPatientError,
    create_record,
)

router = APIRouter()

# Тексты ошибок: говорят, что делать, не извиняются (DESIGN.MD §5).
EMPTY_RECORD_ERROR = "Добавьте фото или заметку — пустых записей не бывает."
SAVE_FAILED_ERROR = "Не получилось сохранить. Проверьте связь и попробуйте ещё раз."
TOAST_SAVED = "Запись сохранена"  # имя действия сквозное: «Сохранить» → «сохранена»


def _render_form(
    request: Request,
    account: Account,
    db: Session,
    error: str | None = None,
    comment: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    members = list_by_family(db, account.member.family_id)
    context = {
        **switcher_context(request.session, account, members),
        "error": error,
        "comment": comment,
    }
    return templates.TemplateResponse(request, "records/new.html", context, status_code=status_code)


@router.get("/records/new", response_class=HTMLResponse)
def new_record_form(
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return _render_form(request, account, db)


@router.post("/records")
def create_record_route(
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
    patient_id: Annotated[int, Form()],
    comment: Annotated[str, Form()] = "",
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    settings = get_app_settings(request)
    # Пустая часть формы без имени файла — не файл (браузер шлёт её,
    # когда input оставлен нетронутым).
    pairs = [(upload.filename, upload.file.read()) for upload in (files or []) if upload.filename]

    try:
        create_record(
            db,
            files_dir=settings.files_dir,
            author=account,
            patient_id=patient_id,
            files=pairs,
            comment=comment,
        )
    except EmptyRecordError:
        return _render_form(request, account, db, error=EMPTY_RECORD_ERROR, comment=comment)
    except FileValidationError as exc:
        return _render_form(request, account, db, error=str(exc), comment=comment)
    except UnknownPatientError:
        # Чужой и несуществующий пациент неразличимы (как в T2.4).
        raise HTTPException(status_code=404) from None
    except OSError:
        # Ветка B6 потока: сбой записи на диск. Компенсация уже отработала
        # в сервисе — пользователю честный текст и сохранённая форма.
        return _render_form(request, account, db, error=SAVE_FAILED_ERROR, comment=comment)

    request.session["flash"] = TOAST_SAVED
    # Решение ❓2 потока: после сохранения — главная (карточка — Э5).
    return RedirectResponse("/", status_code=303)
