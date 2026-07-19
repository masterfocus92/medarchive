"""Тесты поиска-чата (T7.3): ветвления B1–B6 потока, фильтры семьи, журнал.

Провайдеры — фейки, ретривал — настоящий pgvector: проверяется вся цепочка
«вопрос → вектор → кандидаты → LLM → ответ с источниками» без сети.
"""

from datetime import UTC, date, datetime

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.models import Account, Family, FamilyMember, HealthRecord, SearchQuery
from app.models.search import EMBEDDING_DIM
from app.repositories.embeddings import upsert
from app.services.embeddings import EmbeddingError
from app.services.llm import LLMError
from app.services.search_chat import ask

CHAT_TEST_DB = "medcard_test_search_chat"

NOW = datetime.now(UTC)


def _vec(axis: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[axis] = 1.0
    return v


class FakeEmbeddings:
    """Отдаёт заранее заданный вектор вопроса — близость к записям
    управляется конструированием осей."""

    model = "fake-emb"

    def __init__(self, vector=None, error=False):
        self.vector = vector if vector is not None else _vec(0)
        self.error = error

    def embed(self, texts):
        if self.error:
            raise EmbeddingError("провайдер недоступен")
        return [self.vector for _ in texts]


class FakeChat:
    model = "fake-llm"

    def __init__(self, payload=None, error=False):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if self.error:
            raise LLMError("модель упала")
        return self.payload


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        database_url=db_url(CHAT_TEST_DB),
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
        **kwargs,
    )


@pytest.fixture(scope="module")
def db(admin_conn):
    recreate_db(admin_conn, CHAT_TEST_DB)
    command.upgrade(alembic_config(db_url(CHAT_TEST_DB)), "head")
    engine = create_engine(db_url(CHAT_TEST_DB))

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Иванов",
            first_name="Дмитрий",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        child = FamilyMember(
            family=family,
            last_name="Иванова",
            first_name="Арина",
            birth_date=date(2020, 1, 1),
            sex="female",
        )
        account = Account(member=operator, email="op@chat.local", password_hash="x")

        alien_family = Family()
        alien = FamilyMember(
            family=alien_family,
            last_name="Чужов",
            first_name="Сосед",
            birth_date=date(1985, 1, 1),
            sex="male",
        )
        alien_account = Account(member=alien, email="alien@chat.local", password_hash="x")
        session.add_all([account, alien_account, child])
        session.commit()

        def record(patient, author, title, axis, **fields):
            r = HealthRecord(
                author=author,
                patient=patient,
                title=title,
                confirmed_at=NOW,
                **fields,
            )
            session.add(r)
            session.flush()
            upsert(session, r.id, _vec(axis), "fake-emb")
            return r.id

        ids = {
            "family_id": family.id,
            "alien_family_id": alien_family.id,
            # Манту у Арины — ось 0 (её и будет «спрашивать» фейк-вопрос).
            "mantu": record(
                child, account, "Проба Манту", 0, record_type="прививка", content="отрицательно"
            ),
            # Анализ у оператора — ось 1: вне порога для вопроса по оси 0.
            "analysis": record(operator, account, "Анализ крови", 1),
            # Чужая семья — тоже ось 0: идеальное совпадение, но чужое.
            "alien_rec": record(alien, alien_account, "Чужая манту", 0),
        }
        session.commit()

    yield sessionmaker(bind=engine), ids
    engine.dispose()
    drop_db(admin_conn, CHAT_TEST_DB)


def _journal_count(session) -> int:
    return session.scalar(select(func.count()).select_from(SearchQuery))


# ---------- happy-path: ответ с источниками ----------


def test_answer_with_sources(db):
    factory, ids = db
    chat = FakeChat(
        {"answer": "Манту делали, результат отрицательный", "source_ids": [ids["mantu"]]}
    )
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "когда Арине делали манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    assert result.kind == "answer"
    assert result.answer == "Манту делали, результат отрицательный"
    assert [s["id"] for s in result.sources] == [ids["mantu"]]
    # Источник несёт имя пациента и акцент (❓3: поиск семейный).
    assert result.sources[0]["patient_name"] == "Арина"
    assert result.sources[0]["accent"]  # класс акцента присвоен
    assert result.sources[0]["distance"] == pytest.approx(0.0, abs=1e-6)


def test_llm_receives_question_and_excerpts(db):
    factory, ids = db
    chat = FakeChat({"answer": "ответ", "source_ids": [ids["mantu"]]})
    with factory() as session:
        ask(
            session,
            _settings(),
            ids["family_id"],
            "когда Арине делали манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    system, user = chat.calls[0]
    assert "когда Арине делали манту?" in user
    assert f"id={ids['mantu']}" in user
    assert "Проба Манту" in user  # выдержка — текст записи
    assert "только" in system.lower()  # промпт требует отвечать строго по выдержкам


# ---------- галлюцинации и ответ без опоры ----------


def test_hallucinated_source_ids_are_dropped(db):
    factory, ids = db
    chat = FakeChat({"answer": "ответ", "source_ids": [ids["mantu"], 999999, ids["alien_rec"]]})
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    # Чужая запись и выдуманный id не могут стать источниками, даже если
    # модель их назвала: источники ⊆ кандидатов ретривала.
    assert result.kind == "answer"
    assert [s["id"] for s in result.sources] == [ids["mantu"]]


def test_answer_without_sources_becomes_not_found_with_similar(db):
    factory, ids = db
    chat = FakeChat({"answer": "громкий ответ без опоры", "source_ids": []})
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    # B6: ответ без источников не существует.
    assert result.kind == "not_found_with_similar"
    assert result.answer is None
    assert [s["id"] for s in result.similar] == [ids["mantu"]]


def test_llm_found_nothing_returns_similar(db):
    factory, ids = db
    chat = FakeChat({"answer": None, "source_ids": []})
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    assert result.kind == "not_found_with_similar"
    assert [s["id"] for s in result.similar] == [ids["mantu"]]


# ---------- деградации B2/B3/B4 ----------


def test_empty_retrieval_is_not_found_and_llm_not_called(db):
    factory, ids = db
    chat = FakeChat({"answer": "не должен вызываться", "source_ids": []})
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "что-то постороннее",
            embeddings=FakeEmbeddings(vector=_vec(7)),  # далеко от всех записей
            chat=chat,
        )

    assert result.kind == "not_found"
    assert chat.calls == []  # B2: нечего подавать — нечего сочинять


def test_embedding_failure_is_unavailable(db):
    factory, ids = db
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(error=True),
            chat=FakeChat({}),
        )

    assert result.kind == "unavailable"
    assert result.sources == [] and result.similar == []


def test_llm_failure_degrades_to_similar_records(db):
    factory, ids = db
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=FakeChat(error=True),
        )

    # B4: retrieval-ядро автономно — список похожих без текстового ответа.
    assert result.kind == "llm_unavailable"
    assert [s["id"] for s in result.similar] == [ids["mantu"]]


# ---------- B1 и журнал ----------


def test_empty_question_returns_none_and_skips_everything(db):
    factory, ids = db
    with factory() as session:
        before = _journal_count(session)
        result = ask(
            session,
            _settings(),
            ids["family_id"],
            "   ",
            embeddings=FakeEmbeddings(error=True),  # не должен дёрнуться
            chat=FakeChat(error=True),
        )
        assert result is None
        assert _journal_count(session) == before


def test_every_question_lands_in_journal(db):
    factory, ids = db
    with factory() as session:
        before = _journal_count(session)

        # Ответ найден — строка с ответом и кандидатами.
        ask(
            session,
            _settings(),
            ids["family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=FakeChat({"answer": "ответ", "source_ids": [ids["mantu"]]}),
        )
        # «Не нашлось» — тоже строка (❓10: датасет тюнинга, не лог успехов).
        ask(
            session,
            _settings(),
            ids["family_id"],
            "постороннее",
            embeddings=FakeEmbeddings(vector=_vec(7)),
            chat=FakeChat({}),
        )

        assert _journal_count(session) == before + 2
        row = session.scalars(select(SearchQuery).order_by(SearchQuery.id.desc()).limit(2)).all()
        answered = next(r for r in row if r.question == "манту?")
        empty = next(r for r in row if r.question == "постороннее")

    assert answered.answer == "ответ"
    assert answered.candidates[0]["record_id"] == ids["mantu"]
    assert "distance" in answered.candidates[0]
    assert empty.answer is None and empty.candidates == []


# ---------- чужая семья ----------


def test_foreign_family_never_appears(db):
    factory, ids = db
    # Вопрос из чужой семьи по оси 0: их собственная запись найдётся,
    # записи первой семьи — нет.
    chat = FakeChat({"answer": "ответ", "source_ids": [ids["alien_rec"], ids["mantu"]]})
    with factory() as session:
        result = ask(
            session,
            _settings(),
            ids["alien_family_id"],
            "манту?",
            embeddings=FakeEmbeddings(),
            chat=chat,
        )

    assert [s["id"] for s in result.sources] == [ids["alien_rec"]]
