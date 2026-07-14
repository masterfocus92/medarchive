"""Создание записи и раздача её файлов (flows/record-creation.md)."""

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account, ParseStatus, RecordFile
from app.repositories.members import list_by_family
from app.routes.deps import get_app_settings, get_current_account
from app.routes.pages import templates
from app.services.pipeline import run_extraction
from app.services.profiles import switcher_context
from app.services.records import (
    EmptyRecordError,
    FileValidationError,
    UnknownPatientError,
    create_record,
)

logger = logging.getLogger(__name__)

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
    background_tasks: BackgroundTasks,
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
        record = create_record(
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

    # Конвейер разбора (Э4, ❓5): фоном, после ответа — «сохранено
    # мгновенно» и «разобрано» разделены by design.
    if record.parse_status == ParseStatus.UPLOADED:
        background_tasks.add_task(
            run_extraction,
            record.id,
            settings=settings,
            session_factory=request.app.state.session_factory,
        )

    request.session["flash"] = TOAST_SAVED
    # Решение ❓2 потока: после сохранения — главная (карточка — Э5).
    return RedirectResponse("/", status_code=303)


@router.get("/records/{record_id}/files/{position}")
def record_file(
    record_id: int,
    position: int,
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> FileResponse:
    """Файл записи — только своей семье (T3.3).

    Middleware даёт аутентификацию; здесь — авторизация: запись обязана
    принадлежать семье оператора. Чужая, несуществующая и удалённая
    неразличимы (404) — не подтверждаем существование чужих данных.
    Путь берётся строго из БД: traversal из URL невозможен по построению.
    """
    row = db.scalar(
        select(RecordFile).where(
            RecordFile.record_id == record_id,
            RecordFile.position == position,
        )
    )
    if (
        row is None
        or row.record.deleted_at is not None
        or row.record.patient.family_id != account.member.family_id
    ):
        raise HTTPException(status_code=404)

    settings = get_app_settings(request)
    path = settings.files_dir / row.stored_path
    if not path.is_file():
        # Запись есть, файла нет — рассинхрон диска и БД: честный 404
        # пользователю, тревожный лог нам.
        logger.warning("Файл записи %s отсутствует на диске: %s", record_id, row.stored_path)
        raise HTTPException(status_code=404)

    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.original_name,
        content_disposition_type="inline",
    )
