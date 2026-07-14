"""Ручная проверка качества экстрактора на реальных документах (T4.2).

Запуск: uv run python -m app.tools.try_extractor <файл> [файл2 ...]
Нужны .env (EXTRACTOR_*) и поднятая БД (семья читается из неё, чтобы
проверить и определение пациента). Вывод — в терминал владельца,
никуда не сохраняется.
"""

import sys
from pathlib import Path

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import FamilyMember
from app.services.extraction import ExtractionError, build_extractor
from app.services.imaging import prepare_for_vision
from app.services.records import sniff_file

# HEIC сниффером записей не распознаётся как «наш» тип провайдера,
# но для ручной проверки принимаем по расширению.
_EXT_MIMES = {".heic": "image/heic", ".pdf": "application/pdf"}


def _read(path: Path) -> tuple[str, bytes]:
    content = path.read_bytes()
    sniffed = sniff_file(content)
    if sniffed:
        return sniffed[1], content
    mime = _EXT_MIMES.get(path.suffix.lower())
    if mime:
        return mime, content
    raise SystemExit(f"Не понимаю формат файла: {path}")


def main() -> None:
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        raise SystemExit(
            "Использование: uv run python -m app.tools.try_extractor <файл> [файл2 ...]"
        )

    settings = get_settings()
    extractor = build_extractor(settings)

    with get_sessionmaker()() as session:
        family = list(session.query(FamilyMember).order_by(FamilyMember.id))
        pages = prepare_for_vision([_read(p) for p in paths])
        print(f"Страниц к разбору: {len(pages)}; модель: {extractor.model}")
        try:
            result, _ = extractor.extract(pages, family)
        except ExtractionError as exc:
            raise SystemExit(f"Разбор не удался: {exc}") from None

        by_id = {m.id: m for m in family}
        print("\n--- Результат ---")
        print(f"Название:   {result.title}")
        print(f"Дата:       {result.event_date}")
        print(f"Клиника:    {result.clinic}")
        print(f"Врач:       {result.doctor}")
        print(f"Тип:        {result.record_type}")
        suggested = by_id.get(result.suggested_patient_id)
        print(f"Пациент:    {suggested.full_name if suggested else '— (не определён)'}")
        print(f"\nСодержание:\n{result.content}")


if __name__ == "__main__":
    main()
