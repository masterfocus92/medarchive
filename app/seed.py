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
from app.services.security import hash_password


def run_seed(settings: SeedSettings, session: Session) -> bool:
    """Создаёт семейное пространство. Возвращает True, если данные созданы,
    False — если seed уже выполнялся (проверка по email учёток).
    """
    emails = [settings.adult1_email, settings.adult2_email]
    already = session.scalar(select(Account).where(Account.email.in_(emails)))
    if already is not None:
        return False

    # Название семьи не передаётся — сработает server_default «Семья»
    # (продуктовое решение, см. модель Family).
    family = Family()

    adult1 = FamilyMember(
        family=family,
        full_name=settings.adult1_full_name,
        birth_date=settings.adult1_birth_date,
        sex=settings.adult1_sex,
    )
    adult2 = FamilyMember(
        family=family,
        full_name=settings.adult2_full_name,
        birth_date=settings.adult2_birth_date,
        sex=settings.adult2_sex,
    )
    child = FamilyMember(
        family=family,
        full_name=settings.child_full_name,
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
        fields = ", ".join(str(err["loc"][0]) for err in exc.errors())
        raise SystemExit(
            "Данные seed не заполнены. Скопируйте шаблон (cp .env.seed.example .env.seed) "
            f"и заполните поля. Проблемные поля: {fields}"
        ) from None
    with get_sessionmaker()() as session:
        created = run_seed(settings, session)
    # Пароли и данные сознательно не печатаются.
    print("Семья создана." if created else "Данные уже есть — ничего не изменено.")


if __name__ == "__main__":
    main()
