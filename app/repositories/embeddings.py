"""Доступ к векторам записей и журналу поиска.

ИНВАРИАНТ ретривала — фильтры по умолчанию: только подтверждённые
(ADR-012), только неудалённые, только своя семья. Любая выборка без них —
осознанное решение с отдельной функцией, а не флагом по месту вызова.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FamilyMember, HealthRecord, RecordEmbedding, SearchQuery


def upsert(session: Session, record_id: int, vector: list[float], model: str) -> None:
    """Кладёт/перезаписывает вектор записи. Один вектор на запись (PK=FK):
    повторная индексация — замена, история векторов не копится."""
    statement = insert(RecordEmbedding).values(
        record_id=record_id,
        embedding=vector,
        model=model,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[RecordEmbedding.record_id],
            set_={
                "embedding": statement.excluded.embedding,
                "model": statement.excluded.model,
                # server_default срабатывает только на INSERT — момент
                # переиндексации обновляем явно.
                "updated_at": func.now(),
            },
        )
    )


def delete_for_record(session: Session, record_id: int) -> None:
    """Убирает вектор удалённой записи. Идемпотентно: повторный вызов
    (двойной сабмит удаления) — no-op, не ошибка."""
    row = session.get(RecordEmbedding, record_id)
    if row is not None:
        session.delete(row)


def search_similar(
    session: Session,
    family_id: int,
    vector: list[float],
    k: int,
    max_distance: float,
) -> list[tuple[HealthRecord, float]]:
    """Топ-K ближайших записей семьи с порогом «не нашёл» (❓6).

    Дистанция косинусная (bge-m3 — нормированные векторы). ANN-индекса
    нет сознательно (ADR-018): на семейных объёмах точный перебор
    быстрее и точнее.
    """
    distance = RecordEmbedding.embedding.cosine_distance(vector)
    rows = session.execute(
        select(HealthRecord, distance)
        .join(RecordEmbedding, RecordEmbedding.record_id == HealthRecord.id)
        .join(FamilyMember, HealthRecord.patient_id == FamilyMember.id)
        .where(
            FamilyMember.family_id == family_id,
            HealthRecord.deleted_at.is_(None),
            HealthRecord.confirmed_at.is_not(None),
            distance <= max_distance,
        )
        .order_by(distance)
        .limit(k)
    )
    return [(record, float(dist)) for record, dist in rows]


def log_query(
    session: Session,
    question: str,
    candidates: list[dict],
    answer: str | None,
) -> None:
    """Строка журнала тюнинга (❓10): candidates — [{record_id, distance}]."""
    session.add(SearchQuery(question=question, candidates=candidates, answer=answer))
