"""Доменные модели.

Импорты нужны, чтобы один `import app.models` регистрировал все таблицы
в Base.metadata — на это полагается alembic (env.py) при автогенерации.
"""

from app.models.base import Base
from app.models.family import Account, Family, FamilyMember
from app.models.record import PARSE_STATUS_LABELS, HealthRecord, ParseStatus, RecordFile

__all__ = [
    "PARSE_STATUS_LABELS",
    "Account",
    "Base",
    "Family",
    "FamilyMember",
    "HealthRecord",
    "ParseStatus",
    "RecordFile",
]
