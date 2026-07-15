"""Создание записи, экран проверки и раздача файлов (flows/record-creation.md)."""

import logging
from datetime import date
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account, ParseStatus, RecordFile
from app.repositories.members import list_by_family
from app.repositories.records import get_for_family
from app.routes.deps import get_app_settings, get_current_account
from app.routes.pages import templates
from app.services.pipeline import can_retry, run_extraction
from app.services.profiles import switcher_context
from app.services.records import (
    EmptyRecordError,
    FileValidationError,
    UnknownPatientError,
    create_record,
)
from app.services.ui import ai_fields, badge_for

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


CONFIRM_TOAST = "Запись сохранена"
INVALID_DATE_ERROR = "Такой даты нет. Проверьте день и месяц."


def _record_context(request: Request, account: Account, db: Session, record) -> dict:
    members = list_by_family(db, account.member.family_id)
    # Вариант и подпись бейджа + AI-поля — готовыми из services/ui:
    # шаблон только рендерит, решений не принимает.
    status_kind, status_label = badge_for(record)
    suggested = None
    if record.suggested_patient_id is not None and record.suggested_patient_id != record.patient_id:
        suggested = next((m for m in members if m.id == record.suggested_patient_id), None)
    context = switcher_context(request.session, account, members)
    context.update(
        {
            "record": record,
            "status_label": status_label,
            "status_kind": status_kind,
            "ai_fields": ai_fields(record),
            "record_files": [
                {"position": f.position, "url": f"/records/{record.id}/files/{f.position}"}
                for f in record.files
            ],
            "suggested_patient": suggested,
            "can_retry": can_retry(record),
            # ❓4: страница сама переспрашивает сервер, пока конвейер активен.
            "auto_refresh": record.parse_status
            in (ParseStatus.UPLOADED.value, ParseStatus.PARSING.value),
            "error": None,
        }
    )
    return context


def _view_context(request: Request, account: Account, db: Session, record) -> dict:
    """Контекст карточки просмотра (T5.3).

    Доступность файла проверяется здесь, а не в шаблоне: пропавший с диска
    файл (ветка B7 потока) должен дать плашку и warning-лог, а не битую
    картинку и тем более не 500.
    """
    settings = get_app_settings(request)
    members = list_by_family(db, account.member.family_id)
    pages = []
    for f in record.files:
        available = (settings.files_dir / f.stored_path).is_file()
        if not available:
            logger.warning("Файл записи %s отсутствует на диске: %s", record.id, f.stored_path)
        pages.append(
            {
                "position": f.position,
                "url": f"/records/{record.id}/files/{f.position}",
                "mime": f.mime_type,
                # PDF не рендерится в превью (❓6) — шаблону нужен явный признак.
                "is_pdf": f.mime_type == "application/pdf",
                "available": available,
            }
        )
    # Карточку видят только подтверждённые записи (ветвление роута),
    # поэтому badge_for здесь всегда отдаёт («done», «подтверждено»).
    status_kind, status_label = badge_for(record)
    context = switcher_context(request.session, account, members)
    context.update(
        {
            "record": record,
            "status_label": status_label,
            "status_kind": status_kind,
            "pages": pages,
            "page_count": len(pages),
        }
    )
    return context


@router.get("/records/{record_id}", response_class=HTMLResponse)
def record_screen(
    record_id: int,
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    record = get_for_family(db, record_id, account.member.family_id)
    if record is None:
        raise HTTPException(status_code=404)
    # Ветвление потока просмотра (t₂): подтверждённой записи — карточка,
    # неподтверждённой — экран проверки. Один URL на оба состояния: ссылки
    # из ленты не должны знать, проверена ли запись.
    if record.confirmed_at is not None:
        return templates.TemplateResponse(
            request, "records/view.html", _view_context(request, account, db, record)
        )
    return templates.TemplateResponse(
        request, "records/record.html", _record_context(request, account, db, record)
    )


@router.post("/records/{record_id}/confirm")
def confirm_record(
    record_id: int,
    request: Request,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
    patient_id: Annotated[int, Form()],
    title: Annotated[str, Form()] = "",
    event_date: Annotated[str, Form()] = "",
    clinic: Annotated[str, Form()] = "",
    doctor: Annotated[str, Form()] = "",
    record_type: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    comment: Annotated[str, Form()] = "",
):
    record = get_for_family(db, record_id, account.member.family_id)
    if record is None:
        raise HTTPException(status_code=404)

    family_ids = {m.id for m in list_by_family(db, account.member.family_id)}
    if patient_id not in family_ids:
        raise HTTPException(status_code=404)

    parsed_date = None
    if event_date.strip():
        try:
            parsed_date = date.fromisoformat(event_date.strip())
        except ValueError:
            context = _record_context(request, account, db, record)
            context["error"] = INVALID_DATE_ERROR
            return templates.TemplateResponse(request, "records/record.html", context)

    # Пустые строки формы — «нет данных», не данные.
    record.title = title.strip() or None
    record.event_date = parsed_date
    record.clinic = clinic.strip() or None
    record.doctor = doctor.strip() or None
    record.record_type = record_type.strip() or None
    record.content = content.strip() or None
    record.comment = comment.strip() or None
    record.patient_id = patient_id
    # ADR-012: момент подтверждения одноразовый — повторные правки поля
    # обновляют, но историю «когда человек впервые проверил» не переписывают.
    if record.confirmed_at is None:
        record.confirmed_at = func.now()
    db.commit()

    request.session["flash"] = CONFIRM_TOAST
    return RedirectResponse("/", status_code=303)


@router.post("/records/{record_id}/reparse")
def reparse_record(
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    account: Annotated[Account, Depends(get_current_account)],
    db: Annotated[Session, Depends(get_session)],
):
    record = get_for_family(db, record_id, account.member.family_id)
    if record is None:
        raise HTTPException(status_code=404)

    # ❓6: ретрай из parse_failed и из зависшего конвейера; иначе — no-op
    # (страница просто перезагрузится с актуальным статусом).
    if can_retry(record):
        background_tasks.add_task(
            run_extraction,
            record.id,
            settings=get_app_settings(request),
            session_factory=request.app.state.session_factory,
        )
    return RedirectResponse(f"/records/{record_id}", status_code=303)


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
