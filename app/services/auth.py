"""Аутентификация операторов."""

from sqlalchemy.orm import Session

from app.models import Account
from app.repositories.accounts import get_by_email
from app.services.security import DUMMY_HASH, verify_password


def authenticate(session: Session, email: str, password: str) -> Account | None:
    """Возвращает учётку при верной паре email+пароль, иначе None.

    Снаружи «нет такого email» и «неверный пароль» неразличимы:
    один и тот же None и одинаковое время ответа (при отсутствии учётки
    выполняется bcrypt-проверка против DUMMY_HASH). Иначе форма входа
    превращается в оракул существующих email.
    """
    # lowercase симметрично seed'у (SeedSettings нормализует при загрузке):
    # для почты регистр не значим, а телефонная клавиатура любит заглавные.
    account = get_by_email(session, email.strip().lower())
    if account is None:
        verify_password(password, DUMMY_HASH)
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account
