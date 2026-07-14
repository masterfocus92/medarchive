"""Создание записи о здоровье — единственная точка, где записи рождаются.

Здесь живут оба обещанных инварианта:
- «запись содержит файл ИЛИ комментарий» (обещан docstring'ом модели с T1.3);
- «файлы на диске и строки в БД появляются вместе или никак» (ветка B6
  потока flows/record-creation.md): при сбое уже записанные файлы удаляются.
"""

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Account, HealthRecord, ParseStatus, RecordFile
from app.repositories.members import list_by_family

# Решение ❓7 потока: белый список типов и лимит размера.
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ

# Сигнатуры (magic bytes): mime от браузера и расширение в имени — враньё,
# которое пользователь не контролирует (переименованный .exe), контент — нет.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"%PDF", "pdf", "application/pdf"),
]


class EmptyRecordError(Exception):
    """Ни файла, ни комментария — пустых записей не существует (инвариант)."""


class UnknownPatientError(Exception):
    """Пациент не из семьи автора (или не существует) — снаружи это 404."""


class FileValidationError(Exception):
    """Файл не прошёл проверку; message говорит пользователю, что делать."""


def sniff_file(content: bytes) -> tuple[str, str] | None:
    """Определяет (расширение, mime) по сигнатуре контента; None — не наш тип."""
    for signature, ext, mime in _SIGNATURES:
        if content.startswith(signature):
            return ext, mime
    # WEBP: RIFF....WEBP — сигнатура с переменными байтами длины в середине.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    # HEIC/HEIF: ftyp-бокс со смещением 4 (решение ❓8: принимаем как есть,
    # конвертация в JPEG — обязательство экстрактора, этап 4).
    if content[4:8] == b"ftyp" and content[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "heic", "image/heic"
    return None


def _validate_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes, str, str]]:
    """Проверяет все файлы ДО какой-либо записи. Возвращает (имя, контент, ext, mime)."""
    validated = []
    for original_name, content in files:
        if len(content) == 0:
            raise FileValidationError(f"Файл «{original_name}» пустой. Выберите файл ещё раз.")
        if len(content) > MAX_FILE_SIZE:
            raise FileValidationError(
                f"Файл «{original_name}» больше 20 МБ. Сожмите его или выберите другой."
            )
        sniffed = sniff_file(content)
        if sniffed is None:
            raise FileValidationError(
                f"Файл «{original_name}» не поддерживается. "
                "Подойдут фото (JPEG, PNG, WEBP, HEIC) и PDF."
            )
        validated.append((original_name, content, sniffed[0], sniffed[1]))
    return validated


def create_record(
    db: Session,
    files_dir: Path,
    author: Account,
    patient_id: int,
    files: list[tuple[str, bytes]],
    comment: str,
) -> HealthRecord:
    """Атомарно создаёт запись: строки в БД + файлы на диске.

    Порядок файлов в списке = порядок страниц (позиции с 1).
    Статус: с файлами — uploaded (дальше конвейер Э4), без — сразу
    confirmed (решение ❓1: разбирать нечего).
    """
    comment = comment.strip()
    if not files and not comment:
        raise EmptyRecordError

    family_ids = {member.id for member in list_by_family(db, author.member.family_id)}
    if patient_id not in family_ids:
        raise UnknownPatientError

    validated = _validate_files(files)

    status = ParseStatus.UPLOADED if validated else ParseStatus.CONFIRMED
    record = HealthRecord(
        author_account_id=author.id,
        patient_id=patient_id,
        comment=comment or None,
        parse_status=status.value,
    )
    db.add(record)
    db.flush()  # нужен id — он входит в путь хранения

    record_dir = files_dir / str(record.id)
    written: list[Path] = []
    try:
        if validated:
            record_dir.mkdir(parents=True, exist_ok=True)
        for position, (original_name, content, ext, mime) in enumerate(validated, start=1):
            stored_path = f"{record.id}/{position:02}.{ext}"
            target = files_dir / stored_path
            target.write_bytes(content)
            written.append(target)
            db.add(
                RecordFile(
                    record_id=record.id,
                    position=position,
                    stored_path=stored_path,
                    original_name=original_name,
                    mime_type=mime,
                    size_bytes=len(content),
                )
            )
        db.commit()
    except Exception:
        # Компенсация: БД откатится сама, файлы — только руками.
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    return record
