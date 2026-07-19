"""Интеграционные тесты хранилища векторов (T7.1): upsert, поиск
с фильтрами по умолчанию, порог, чистота миграции.

Векторы в тестах — рукотворные (ось-на-запись): близость управляется
конструированием, живой провайдер не нужен.
"""

from datetime import UTC, date, datetime

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.models import (
    Account,
    Family,
    FamilyMember,
    HealthRecord,
    RecordEmbedding,
    SearchQuery,
)
from app.models.search import EMBEDDING_DIM
from app.repositories.embeddings import (
    delete_for_record,
    log_query,
    search_similar,
    upsert,
)

EMB_TEST_DB = "medcard_test_embeddings"

NOW = datetime.now(UTC)


def _vec(axis: int, value: float = 1.0) -> list[float]:
    """Вектор-«ось»: единица в одной координате. Косинусная дистанция
    между разными осями = 1.0, между сонаправленными = 0.0 — близость
    в тестах строится конструированием, а не подбором чисел."""
    v = [0.0] * EMBEDDING_DIM
    v[axis] = value
    return v


def _mix(axis_a: int, axis_b: int, weight_b: float) -> list[float]:
    """Вектор между двумя осями: чем больше weight_b, тем дальше от оси A."""
    v = [0.0] * EMBEDDING_DIM
    v[axis_a] = 1.0
    v[axis_b] = weight_b
    return v


@pytest.fixture(scope="module")
def db(admin_conn):
    recreate_db(admin_conn, EMB_TEST_DB)
    command.upgrade(alembic_config(db_url(EMB_TEST_DB)), "head")
    engine = create_engine(db_url(EMB_TEST_DB))

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        account = Account(member=operator, email="op@emb.local", password_hash="x")

        stranger_family = Family()
        stranger = FamilyMember(
            family=stranger_family,
            last_name="Чужов",
            first_name="Сосед",
            birth_date=date(1985, 1, 1),
            sex="male",
        )
        stranger_account = Account(member=stranger, email="alien@emb.local", password_hash="x")
        session.add_all([account, stranger_account])
        session.commit()

        def record(patient, author, confirmed=True, deleted=False, comment="запись"):
            r = HealthRecord(
                author=author,
                patient=patient,
                comment=comment,
                confirmed_at=NOW if confirmed else None,
                deleted_at=NOW if deleted else None,
            )
            session.add(r)
            session.flush()
            return r.id

        ids = {
            "family_id": family.id,
            "confirmed": record(operator, account),
            "confirmed_far": record(operator, account),
            "unconfirmed": record(operator, account, confirmed=False),
            "deleted": record(operator, account, deleted=True),
            "alien": record(stranger, stranger_account),
        }
        session.commit()

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, EMB_TEST_DB)


def test_migration_up_down_up_is_clean(admin_conn):
    scratch = "medcard_test_emb_migration"
    recreate_db(admin_conn, scratch)
    config = alembic_config(db_url(scratch))
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
    finally:
        drop_db(admin_conn, scratch)


def test_upsert_then_search_finds_nearest_first(db):
    engine, ids = db
    with Session(engine) as session:
        upsert(session, ids["confirmed"], _vec(0), "baai/bge-m3")
        # Дальний, но проходящий порог: между осью запроса и чужой осью.
        upsert(session, ids["confirmed_far"], _mix(0, 1, 0.8), "baai/bge-m3")
        session.commit()

        found = search_similar(session, ids["family_id"], _vec(0), k=5, max_distance=0.9)

    assert [r.id for r, _ in found] == [ids["confirmed"], ids["confirmed_far"]]
    # Дистанции отсортированы и осмысленны: точное совпадение — около нуля.
    assert found[0][1] == pytest.approx(0.0, abs=1e-6)
    assert found[1][1] > found[0][1]


def test_reupsert_overwrites_vector_and_model(db):
    engine, ids = db
    with Session(engine) as session:
        upsert(session, ids["confirmed"], _vec(0), "baai/bge-m3")
        session.commit()
        upsert(session, ids["confirmed"], _vec(2), "new-model")
        session.commit()

        rows = session.scalars(
            select(RecordEmbedding).where(RecordEmbedding.record_id == ids["confirmed"])
        ).all()

        assert len(rows) == 1  # PK по record_id — дублей не бывает
        assert rows[0].model == "new-model"
        found = search_similar(session, ids["family_id"], _vec(2), k=1, max_distance=0.5)
        assert found[0][0].id == ids["confirmed"]

        # вернуть вектор для остальных тестов модуля
        upsert(session, ids["confirmed"], _vec(0), "baai/bge-m3")
        session.commit()


def test_search_ignores_unconfirmed_deleted_and_alien(db):
    engine, ids = db
    with Session(engine) as session:
        # Всем «невидимым» записям — вектор, идентичный запросу:
        # если фильтр дырявый, они всплывут первыми.
        for key in ("unconfirmed", "deleted", "alien"):
            upsert(session, ids[key], _vec(0), "baai/bge-m3")
        session.commit()

        found = search_similar(session, ids["family_id"], _vec(0), k=10, max_distance=0.99)

    found_ids = [r.id for r, _ in found]
    assert ids["unconfirmed"] not in found_ids
    assert ids["deleted"] not in found_ids
    assert ids["alien"] not in found_ids
    assert ids["confirmed"] in found_ids


def test_threshold_cuts_far_records(db):
    engine, ids = db
    with Session(engine) as session:
        # Запрос по чужой оси: обе записи семьи дальше порога.
        found = search_similar(session, ids["family_id"], _vec(3), k=5, max_distance=0.75)

    assert found == []


def test_k_limits_result_count(db):
    engine, ids = db
    with Session(engine) as session:
        found = search_similar(session, ids["family_id"], _vec(0), k=1, max_distance=0.9)

    assert len(found) == 1
    assert found[0][0].id == ids["confirmed"]


def test_delete_for_record_removes_row(db):
    engine, ids = db
    with Session(engine) as session:
        upsert(session, ids["confirmed_far"], _vec(1), "baai/bge-m3")
        session.commit()

        delete_for_record(session, ids["confirmed_far"])
        session.commit()

        row = session.get(RecordEmbedding, ids["confirmed_far"])
        assert row is None
        # Повторное удаление безопасно (идемпотентность для soft_delete).
        delete_for_record(session, ids["confirmed_far"])
        session.commit()


def test_log_query_writes_journal_row(db):
    engine, ids = db
    with Session(engine) as session:
        log_query(
            session,
            question="когда манту?",
            candidates=[{"record_id": ids["confirmed"], "distance": 0.42}],
            answer="12 марта 2026",
        )
        session.commit()

        row = session.scalar(select(SearchQuery).where(SearchQuery.question == "когда манту?"))

    assert row.candidates == [{"record_id": ids["confirmed"], "distance": 0.42}]
    assert row.answer == "12 марта 2026"
    assert row.created_at is not None
