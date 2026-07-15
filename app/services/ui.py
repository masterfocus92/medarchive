"""UI-хелперы: маппинг доменного состояния записи → визуальный код кита.

Решение «какой бейдж и какие поля пометить» принимается один раз здесь,
а не размазывается по шаблонам (DESIGN.MD: цвет кодирует смысл; Jinja —
только рендер готового). Соответствие «статус → вариант/подпись» живёт
рядом с моделью (PARSE_STATUS_KINDS / PARSE_STATUS_LABELS, ADR-012).
"""

from app.models import (
    CONFIRMED_KIND,
    CONFIRMED_LABEL,
    PARSE_STATUS_KINDS,
    PARSE_STATUS_LABELS,
    HealthRecord,
    ParseStatus,
)
from app.services.pipeline import DRAFT_FIELDS

# Число персональных акцентов в ките (--accent-1..3, кит v2);
# семьи больше трёх членов — по кругу.
ACCENT_COUNT = 3


def accent_class(index: int) -> str:
    """Класс персонального акцента i-го члена семьи (кит v2).

    Порядок стабилен: list_by_family сортирует по id, поэтому цвет
    закрепляется за человеком навсегда. Класс задаёт --who-accent —
    inline-стили в шаблонах запрещены стражем.
    """
    return f"accent-{index % ACCENT_COUNT + 1}"


def badge_for(record: HealthRecord) -> tuple[str | None, str | None]:
    """Вариант и подпись статус-бейджа (правило потребителя UI, ADR-012).

    Подтверждена → «подтверждено» (done); иначе — состояние конвейера;
    для none бейджа нет: несуществующий конвейер не показывается.
    """
    if record.confirmed_at is not None:
        return (CONFIRMED_KIND, CONFIRMED_LABEL)
    status = ParseStatus(record.parse_status)
    kind = PARSE_STATUS_KINDS.get(status)
    if kind is None:
        return (None, None)
    return (kind, PARSE_STATUS_LABELS[status])


def feed_badge(record: HealthRecord) -> tuple[str | None, str | None]:
    """Бейдж элемента ленты: подтверждённая запись бейджа не носит —
    норма не маркируется, маркируется отклонение (❓2 потока просмотра)."""
    if record.confirmed_at is not None:
        return (None, None)
    return badge_for(record)


def ai_fields(record: HealthRecord) -> set[str]:
    """Имена полей, значения которых подставил AI и человек ещё не проверил.

    Признак точный без отдельного хранения: до подтверждения поля записи
    наполняет только конвейер — форма создания пишет лишь заметку и пациента,
    а правки человека приходят только через confirm, который сразу ставит
    confirmed_at. Значит у parsed-неподтверждённой записи каждое непустое
    draft-поле пришло от экстрактора. raw_response не используется (ADR-013).
    """
    if record.confirmed_at is not None or record.parse_status != ParseStatus.PARSED.value:
        return set()
    return {field for field in DRAFT_FIELDS if getattr(record, field) is not None}
