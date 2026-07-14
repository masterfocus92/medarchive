"""Наполнение БД стартовыми данными: семья, три члена, две учётки.

Запуск: uv run python -m app.seed (нужны .env и заполненный .env.seed).

В этом файле нет ни одной буквы реальных данных — всё приходит
из SeedSettings (.env.seed вне git, ADR-009). Скрипт идемпотентен:
повторный запуск ничего не меняет.
"""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SeedSettings
from app.db import get_sessionmaker
from app.models import Account, Family, FamilyMember
from app.repositories.accounts import get_by_email
from app.services.security import hash_password

# Значения из .env.seed.example: их появление в «заполненных» полях означает,
# что файл дозаполнили не до конца (инцидент 14.07.2026 — ребёнок
# «Фамилия Имя Отчество» прожил в БД до ручной SQL-выгрузки).
PLACEHOLDER_NAMES = {"Фамилия", "Имя", "Отчество"}
PLACEHOLDER_EMAIL_SUFFIX = "@example.com"

_MEMBER_PREFIXES = ("adult1", "adult2", "child")
_MEMBER_LABELS = {"adult1": "взрослый 1", "adult2": "взрослый 2", "child": "ребёнок"}
_FIELD_LABELS = {
    "last_name": "фамилия",
    "first_name": "имя",
    "middle_name": "отчество",
    "birth_date": "дата рождения",
    "sex": "пол",
}


def format_validation_error(exc: ValidationError) -> str:
    """Причина по каждому полю человеческим языком.

    «Короче N символов» и «не заполнено» — разные проблемы: первая
    заставляет проверять длину, вторая — наличие поля (T2.6, инцидент 1).
    """
    lines = []
    for error in exc.errors():
        field = str(error["loc"][0])
        if error["type"] == "missing" or error.get("input") in ("", None):
            reason = "не заполнено"
        elif error["type"] == "string_too_short":
            reason = f"короче {error['ctx']['min_length']} символов"
        else:
            reason = error["msg"]
        lines.append(f"  {field} — {reason}")
    return "\n".join(lines)


def detect_placeholders(settings: SeedSettings) -> list[str]:
    """Поля, в которых остались значения из шаблона .env.seed.example."""
    fields = []
    for prefix in _MEMBER_PREFIXES:
        for name_field in ("last_name", "first_name", "middle_name"):
            if getattr(settings, f"{prefix}_{name_field}") in PLACEHOLDER_NAMES:
                fields.append(f"{prefix}_{name_field}")
    for prefix in ("adult1", "adult2"):
        if getattr(settings, f"{prefix}_email").endswith(PLACEHOLDER_EMAIL_SUFFIX):
            fields.append(f"{prefix}_email")
    return fields


def _member_field_diffs(member: FamilyMember, settings: SeedSettings, prefix: str) -> list[str]:
    diffs = []
    for field, label in _FIELD_LABELS.items():
        if getattr(member, field) != getattr(settings, f"{prefix}_{field}"):
            diffs.append(label)
    return diffs


def seed_drift(settings: SeedSettings, session: Session) -> list[str]:
    """Расхождения существующей БД с .env.seed — имена полей БЕЗ значений
    (терминал попадает в скриншоты и логи, значения приватны).

    Взрослые матчатся по email учётки (не нашёлся — само по себе
    расхождение), ребёнок — единственный член семьи без учётки.
    """
    problems = []

    for prefix in ("adult1", "adult2"):
        account = get_by_email(session, getattr(settings, f"{prefix}_email"))
        if account is None:
            problems.append(f"{_MEMBER_LABELS[prefix]} — email (учётки с email из файла нет)")
            continue
        diffs = _member_field_diffs(account.member, settings, prefix)
        if diffs:
            problems.append(f"{_MEMBER_LABELS[prefix]} — {', '.join(diffs)}")

    child = session.scalar(
        select(FamilyMember).where(FamilyMember.id.not_in(select(Account.family_member_id)))
    )
    if child is None:
        problems.append("ребёнок — в БД не найден")
    else:
        diffs = _member_field_diffs(child, settings, "child")
        if diffs:
            problems.append(f"ребёнок — {', '.join(diffs)}")

    return problems


def run_seed(settings: SeedSettings, session: Session) -> bool:
    """Создаёт семейное пространство. Возвращает True, если данные созданы,
    False — если семья уже существует (в POC пространство одно).
    """
    # В POC пространство одно: любая существующая семья означает
    # «уже засеяно» — даже если email'ы в файле с тех пор поменяли
    # (иначе смена обеих почт тихо создала бы семью-дубль).
    if session.scalar(select(Family)) is not None:
        return False

    # Название семьи не передаётся — сработает server_default «Семья»
    # (продуктовое решение, см. модель Family).
    family = Family()

    adult1 = FamilyMember(
        family=family,
        last_name=settings.adult1_last_name,
        first_name=settings.adult1_first_name,
        middle_name=settings.adult1_middle_name,
        birth_date=settings.adult1_birth_date,
        sex=settings.adult1_sex,
    )
    adult2 = FamilyMember(
        family=family,
        last_name=settings.adult2_last_name,
        first_name=settings.adult2_first_name,
        middle_name=settings.adult2_middle_name,
        birth_date=settings.adult2_birth_date,
        sex=settings.adult2_sex,
    )
    child = FamilyMember(
        family=family,
        last_name=settings.child_last_name,
        first_name=settings.child_first_name,
        middle_name=settings.child_middle_name,
        birth_date=settings.child_birth_date,
        sex=settings.child_sex,
    )

    # Оба взрослых — админы (OVERVIEW §5); у ребёнка учётки нет —
    # он только пациент (ADR-006).
    session.add_all(
        [
            child,
            Account(
                member=adult1,
                email=settings.adult1_email,
                password_hash=hash_password(settings.adult1_password),
                is_admin=True,
            ),
            Account(
                member=adult2,
                email=settings.adult2_email,
                password_hash=hash_password(settings.adult2_password),
                is_admin=True,
            ),
        ]
    )
    # Один commit: семья, члены и учётки появляются вместе или никак.
    session.commit()
    return True


def main() -> None:
    try:
        # Незаполненный .env.seed падает здесь — до какого-либо обращения к БД.
        settings = SeedSettings()
    except ValidationError as exc:
        raise SystemExit(
            "Данные seed не заполнены или некорректны. Проверьте .env.seed "
            "(шаблон: cp .env.seed.example .env.seed):\n" + format_validation_error(exc)
        ) from None

    # Плейсхолдеры шаблона — отказ до записи: «Фамилия Имя Отчество»
    # не должна стать членом семьи (T2.6, инцидент 2).
    placeholders = detect_placeholders(settings)
    if placeholders:
        raise SystemExit(
            "В .env.seed остались значения из шаблона: "
            + ", ".join(placeholders)
            + ". Замените их реальными данными и запустите seed снова."
        )

    with get_sessionmaker()() as session:
        created = run_seed(settings, session)
        if created:
            # Пароли и данные сознательно не печатаются.
            print("Семья создана.")
            return
        drift = seed_drift(settings, session)
    if drift:
        print(
            "Данные уже есть, но отличаются от .env.seed: "
            + "; ".join(drift)
            + ". Пересоздайте БД (docker compose down -v && docker compose up -d, "
            "миграции, seed) или обновите строки вручную."
        )
    else:
        print("Данные уже есть — ничего не изменено.")


if __name__ == "__main__":
    main()
