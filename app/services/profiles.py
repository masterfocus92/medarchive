"""Профили: инициалы для монограмм и выбор активного профиля.

Активный профиль («на кого я сейчас смотрю») живёт в сессии рядом
с account_id. Осознанное следствие (план T2.4): logout чистит сессию
целиком, поэтому после нового входа выбор сбрасывается на дефолт —
самого вошедшего. Хранение выбора в БД — лишняя сущность для POC.
"""

from app.models import Account, FamilyMember
from app.services.ui import accent_class

SESSION_ACTIVE_MEMBER_KEY = "active_member_id"


def initials(first_name: str, last_name: str) -> str:
    """Монограмма: две буквы, имя + фамилия. Поля раздельные (T2.7) —
    никакого парсинга строк."""
    return (first_name[:1] + last_name[:1]).upper()


def switcher_context(session_data, account: Account, members: list[FamilyMember]) -> dict:
    """Контекст шапки (переключатель) — общий для всех экранов с members.

    Контракт T2.4-BE/T2.5-FE (+Э5.5): members[{id, full_name, first_name,
    initials, is_active, accent}] + active_member + active_accent —
    класс акцента активного профиля для заголовка ленты (кит v2).
    """
    active = resolve_active_member(session_data, account, members)
    context_members = []
    active_accent = ""
    for index, member in enumerate(members):
        accent = accent_class(index)
        if member.id == active.id:
            active_accent = accent
        context_members.append(
            {
                "id": member.id,
                "full_name": member.full_name,
                # Подпись под монограммой — имя без фамилии (шапка тесная).
                "first_name": member.first_name,
                "initials": initials(member.first_name, member.last_name),
                "is_active": member.id == active.id,
                "accent": accent,
            }
        )
    return {
        "members": context_members,
        "active_member": active,
        "active_accent": active_accent,
    }


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
