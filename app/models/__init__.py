"""Доменные модели.

Импорты нужны, чтобы один `import app.models` регистрировал все таблицы
в Base.metadata — на это полагается alembic (env.py) при автогенерации.
"""

from app.models.base import Base
from app.models.family import Account, Family, FamilyMember
from app.models.record import HealthRecord, RecordFile

__all__ = ["Account", "Base", "Family", "FamilyMember", "HealthRecord", "RecordFile"]
