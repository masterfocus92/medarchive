"""Тесты индексации в жизненном цикле записи (T7.2).

Провайдер эмбеддингов подменяется фейком через monkeypatch фабрики:
жизненный цикл (confirm → вектор, delete → чистка, reindex → бэкфилл)
проверяется без сети. Провал провайдера никогда не блокирует
подтверждение (B7) — главный негативный сценарий этапа.
"""

import logging
from datetime import UTC, date, datetime

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Account, Family, FamilyMember, HealthRecord, RecordEmbedding
from app.models.search import EMBEDDING_DIM
from app.repositories.embeddings import upsert
from app.repositories.records import soft_delete
from app.services.embeddings import EmbeddingError
from app.services.indexing import build_index_text, index_record
from app.tools.reindex import reindex

IDX_TEST_DB = "medcard_test_indexing"

EMAIL = "op@idx.local"
PASSWORD = "correct-password-1"

NOW = datetime.now(UTC)


class FakeEmbeddings:
    model = "fake-emb"

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[0.1] * EMBEDDING_DIM for _ in texts]


class FailingEmbeddings:
    model = "fake-emb"

    def embed(self, texts):
        raise EmbeddingError("провайдер упал")


@pytest.fixture(scope="module")
def db(admin_conn):
    recreate_db(admin_conn, IDX_TEST_DB)
    command.upgrade(alembic_config(db_url(IDX_TEST_DB)), "head")
    engine = create_engine(db_url(IDX_TEST_DB))

    from app.services.security import hash_password

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        account = Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD))
        session.add(account)
        session.commit()
        ids = {"member": operator.id, "account": account.id}

    yield {"engine": engine, "factory": sessionmaker(bind=engine), "ids": ids}
    engine.dispose()
    drop_db(admin_conn, IDX_TEST_DB)


@pytest.fixture(scope="module")
def app(db, tmp_path_factory):
    settings = Settings(
        _env_file=None,
        database_url=db_url(IDX_TEST_DB),
        files_dir=tmp_path_factory.mktemp("idx-files"),
        secret_key="test-secret-key-only-for-tests-0123456789",
        # Дефолт disabled: тест провайдера включает фейк через monkeypatch
        # фабрики, а тест деградации пользуется дефолтом как есть.
    )
    application = create_app(settings)

    def override_session():
        with db["factory"]() as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app):
    test_client = TestClient(app, follow_redirects=False)
    test_client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    return test_client


@pytest.fixture
def fake(monkeypatch):
    provider = FakeEmbeddings()
    monkeypatch.setattr("app.services.indexing.build_embeddings", lambda settings: provider)
    return provider


def _make_record(db, confirmed=False, **fields) -> int:
    with db["factory"]() as session:
        record = HealthRecord(
            author_account_id=db["ids"]["account"],
            patient_id=db["ids"]["member"],
            parse_status="parsed" if not fields.get("comment") else "none",
            confirmed_at=NOW if confirmed else None,
            **fields,
        )
        session.add(record)
        session.commit()
        return record.id


def _embedding_row(db, record_id) -> RecordEmbedding | None:
    with db["factory"]() as session:
        return session.get(RecordEmbedding, record_id)


# ---------- build_index_text: состав по ❓5 ----------


def test_index_text_contains_all_fields():
    patient = FamilyMember(
        last_name="Иванова",
        first_name="Арина",
        birth_date=date(2020, 1, 1),
        sex="female",
    )
    record = HealthRecord(
        patient=patient,
        title="Проба Манту",
        record_type="прививка",
        clinic="Поликлиника №1",
        doctor="Петрова С.И.",
        event_date=date(2026, 3, 12),
        content="Результат отрицательный",
        comment="реакции не было",
    )

    text = build_index_text(record)

    for value in (
        "Иванова Арина",
        "Проба Манту",
        "прививка",
        "Поликлиника №1",
        "Петрова С.И.",
        "2026-03-12",
        "Результат отрицательный",
        "реакции не было",
    ):
        assert value in text


def test_index_text_skips_empty_fields():
    patient = FamilyMember(
        last_name="Тестов", first_name="Оператор", birth_date=date(1990, 1, 1), sex="male"
    )
    record = HealthRecord(patient=patient, comment="только заметка")

    text = build_index_text(record)

    assert "None" not in text
    assert "только заметка" in text
    assert "Тестов Оператор" in text


# ---------- confirm → вектор (первичный и повторный) ----------


def test_confirm_indexes_record(client, db, fake):
    record_id = _make_record(db, title="Анализ крови")

    response = client.post(
        f"/records/{record_id}/confirm",
        data={"patient_id": db["ids"]["member"], "title": "Анализ крови"},
    )

    assert response.status_code == 303
    row = _embedding_row(db, record_id)
    assert row is not None
    assert row.model == "fake-emb"
    # Индексировался именно текст записи по ❓5.
    assert "Анализ крови" in fake.calls[0][0]


def test_reconfirm_reindexes_updated_text(client, db, fake):
    record_id = _make_record(db, title="Старое название", confirmed=True)

    client.post(
        f"/records/{record_id}/confirm",
        data={"patient_id": db["ids"]["member"], "title": "Новое название"},
    )

    with db["factory"]() as session:
        rows = session.scalars(
            select(RecordEmbedding).where(RecordEmbedding.record_id == record_id)
        ).all()
    assert len(rows) == 1  # перезапись, не дубль
    assert "Новое название" in fake.calls[-1][0]


def test_note_creation_indexes_confirmed_note(client, db, fake):
    # Запись «только комментарий» подтверждается в момент создания
    # (confirmed_at ставит create_record) — это её первичное подтверждение,
    # индексация обязана сработать здесь, а не ждать первой правки.
    response = client.post(
        "/records",
        data={"patient_id": db["ids"]["member"], "comment": "манту сделали в марте"},
    )

    assert response.status_code == 303
    assert any("манту сделали в марте" in call[0] for call in fake.calls)


# ---------- B7: провал индексации не блокирует подтверждение ----------


def test_confirm_with_disabled_provider_still_confirms(client, db, caplog):
    record_id = _make_record(db, title="Без провайдера")

    with caplog.at_level(logging.WARNING):
        response = client.post(
            f"/records/{record_id}/confirm",
            data={"patient_id": db["ids"]["member"], "title": "Без провайдера"},
        )

    assert response.status_code == 303
    with db["factory"]() as session:
        assert session.get(HealthRecord, record_id).confirmed_at is not None
    assert _embedding_row(db, record_id) is None
    assert any("индексация" in message.lower() for message in caplog.messages)


def test_confirm_with_failing_provider_still_confirms(client, db, caplog, monkeypatch):
    monkeypatch.setattr(
        "app.services.indexing.build_embeddings", lambda settings: FailingEmbeddings()
    )
    record_id = _make_record(db, title="Провайдер упал")

    with caplog.at_level(logging.WARNING):
        response = client.post(
            f"/records/{record_id}/confirm",
            data={"patient_id": db["ids"]["member"], "title": "Провайдер упал"},
        )

    assert response.status_code == 303
    with db["factory"]() as session:
        assert session.get(HealthRecord, record_id).confirmed_at is not None
    assert _embedding_row(db, record_id) is None


def test_index_record_skips_unconfirmed(db):
    # Прямой вызов с фейком: неподтверждённая запись не индексируется
    # (ADR-012 — в поиске только подтверждённое).
    record_id = _make_record(db, title="Черновик")

    index_record(record_id, session_factory=db["factory"], provider=FakeEmbeddings())

    assert _embedding_row(db, record_id) is None


# ---------- удаление чистит вектор ----------


def test_soft_delete_removes_embedding_row(db):
    record_id = _make_record(db, comment="удаляемая", confirmed=True)
    with db["factory"]() as session:
        upsert(session, record_id, [0.2] * EMBEDDING_DIM, "fake-emb")
        session.commit()

    with db["factory"]() as session:
        record = session.get(HealthRecord, record_id)
        account = session.get(Account, db["ids"]["account"])
        soft_delete(session, record, account)

    assert _embedding_row(db, record_id) is None


# ---------- reindex: бэкфилл идемпотентен ----------


def test_reindex_backfills_missing_and_stale_only(db):
    provider = FakeEmbeddings()
    missing = _make_record(db, comment="без вектора", confirmed=True)
    stale = _make_record(db, comment="вектор старой модели", confirmed=True)
    fresh = _make_record(db, comment="уже проиндексирована", confirmed=True)
    unconfirmed = _make_record(db, title="не подтверждена")
    deleted = _make_record(db, comment="удалена", confirmed=True)

    with db["factory"]() as session:
        upsert(session, stale, [0.3] * EMBEDDING_DIM, "old-model")
        upsert(session, fresh, [0.4] * EMBEDDING_DIM, provider.model)
        record = session.get(HealthRecord, deleted)
        record.deleted_at = NOW
        session.commit()

    indexed = reindex(db["factory"], provider)

    assert indexed >= 2  # missing и stale (плюс возможные хвосты других тестов)
    assert _embedding_row(db, missing).model == provider.model
    assert _embedding_row(db, stale).model == provider.model
    assert _embedding_row(db, unconfirmed) is None
    assert _embedding_row(db, deleted) is None
    # fresh не переиндексировалась: её вектор остался прежним.
    with db["factory"]() as session:
        assert list(session.get(RecordEmbedding, fresh).embedding) == [0.4] * EMBEDDING_DIM

    # Повторный прогон — нечего делать: инструмент идемпотентен.
    assert reindex(db["factory"], provider) == 0
