"""Профили: инициалы для монограмм и выбор активного профиля.

Активный профиль («на кого я сейчас смотрю») живёт в сессии рядом
с account_id. Осознанное следствие (план T2.4): logout чистит сессию
целиком, поэтому после нового входа выбор сбрасывается на дефолт —
самого вошедшего. Хранение выбора в БД — лишняя сущность для POC.
"""

from app.models import Account, FamilyMember

SESSION_ACTIVE_MEMBER_KEY = "active_member_id"


def initials(first_name: str, last_name: str) -> str:
    """Монограмма: две буквы, имя + фамилия. Поля раздельные (T2.7) —
    никакого парсинга строк."""
    return (first_name[:1] + last_name[:1]).upper()


def resolve_active_member(
    session_data, account: Account, members: list[FamilyMember]
) -> FamilyMember:
    """Активный профиль: выбранный в сессии, если он из семьи оператора.

    Невалидный id в сессии (битая/устаревшая сессия) — молча дефолт,
    не ошибка: главная не должна ломаться из-за мусора в cookie.
    Дефолт — член семьи самого оператора.
    """
    by_id = {member.id: member for member in members}
    active_id = session_data.get(SESSION_ACTIVE_MEMBER_KEY)
    if active_id in by_id:
        return by_id[active_id]
    return by_id.get(account.family_member_id, members[0])
