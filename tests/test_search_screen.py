"""Тесты экрана поиска-чата и плавающей кнопки (T7.4).

Сервис поиска подменяется monkeypatch'ем — тесты проверяют рендер
каждого исхода (ответ, «не нашлось», деградации B2/B3/B4) и правила
кита: одна primary на экран, тексты говорят что делать, пустые состояния.
"""

from datetime import UTC, date, datetime

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Account, Family, FamilyMember, HealthRecord
from app.services.search_chat import SearchResult

SCREEN_TEST_DB = "medcard_test_search_screen"

EMAIL = "op@screen.local"
PASSWORD = "correct-password-1"

NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def db(admin_conn):
    recreate_db(admin_conn, SCREEN_TEST_DB)
    command.upgrade(alembic_config(db_url(SCREEN_TEST_DB)), "head")
    engine = create_engine(db_url(SCREEN_TEST_DB))

    from app.services.security import hash_password

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Иванов",
            first_name="Дмитрий",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        # Ребёнок без записей — для проверки пустой ленты без кнопки «Найти».
        child = FamilyMember(
            family=family,
            last_name="Иванова",
            first_name="Арина",
            birth_date=date(2020, 1, 1),
            sex="female",
        )
        account = Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD))
        record = HealthRecord(
            author=account, patient=operator, comment="запись в ленте", confirmed_at=NOW
        )
        session.add_all([account, child, record])
        session.commit()
        ids = {"record": record.id, "child": child.id}

    yield sessionmaker(bind=engine), ids
    engine.dispose()
    drop_db(admin_conn, SCREEN_TEST_DB)


@pytest.fixture(scope="module")
def app(db, tmp_path_factory):
    factory, _ = db
    settings = Settings(
        _env_file=None,
        database_url=db_url(SCREEN_TEST_DB),
        files_dir=tmp_path_factory.mktemp("screen-files"),
        secret_key="test-secret-key-only-for-tests-0123456789",
    )
    application = create_app(settings)

    def override_session():
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app):
    test_client = TestClient(app, follow_redirects=False)
    test_client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    return test_client


def _source(record_id: int) -> dict:
    return {
        "id": record_id,
        "title": "Проба Манту",
        "event_date": date(2026, 3, 12),
        "created_at": NOW,
        "record_type": "прививка",
        "clinic": "Поликлиника №1",
        "patient_name": "Арина",
        "initials": "АИ",
        "accent": "accent-2",
        "distance": 0.42,
    }


def _stub(monkeypatch, result: SearchResult):
    monkeypatch.setattr("app.routes.search.ask", lambda *args, **kwargs: result)


# ---------- доступ и приглашение ----------


def test_search_requires_auth(app):
    anonymous = TestClient(app, follow_redirects=False)

    assert anonymous.get("/search").status_code == 303
    assert anonymous.post("/search", data={"question": "манту?"}).status_code == 303


def test_invitation_on_get(client):
    html = client.get("/search").text

    assert "Спросите" in html  # приглашение-пример, а не пустая страница
    assert 'name="question"' in html
    assert "Спросить" in html
    assert 'href="/"' in html  # «В ленту»


def test_empty_question_renders_invitation(client):
    # B1 без monkeypatch: настоящий сервис на пустом вопросе не дёргает
    # ни провайдеров, ни журнал — экран просто остаётся приглашением.
    html = client.post("/search", data={"question": "   "}).text

    assert "Спросите" in html


# ---------- исходы ----------


def test_answer_with_sources_rendered(client, db, monkeypatch):
    _, ids = db
    _stub(
        monkeypatch,
        SearchResult(
            kind="answer",
            answer="Манту делали 12 марта 2026, результат отрицательный",
            sources=[_source(ids["record"])],
        ),
    )

    html = client.post("/search", data={"question": "когда Арине делали манту?"}).text

    assert "когда Арине делали манту?" in html  # реплика оператора
    assert "результат отрицательный" in html
    assert "Источники" in html
    assert f'href="/records/{ids["record"]}"' in html  # тап → карточка
    assert "Арина" in html and "АИ" in html  # чей источник — видно (❓3)
    assert "accent-2" in html  # акцент пациента
    assert "12.03.2026" in html  # дата — моноширинный rec-lite


def test_not_found_rendered(client, monkeypatch):
    _stub(monkeypatch, SearchResult(kind="not_found"))

    html = client.post("/search", data={"question": "про панду"}).text

    assert "В карте этого не нашлось" in html
    assert "другими словами" in html  # текст говорит, что делать


def test_not_found_with_similar_rendered(client, db, monkeypatch):
    _, ids = db
    _stub(
        monkeypatch,
        SearchResult(kind="not_found_with_similar", similar=[_source(ids["record"])]),
    )

    html = client.post("/search", data={"question": "манту?"}).text

    assert "В карте этого не нашлось" in html
    assert "Похожие записи" in html
    assert f'href="/records/{ids["record"]}"' in html


def test_llm_unavailable_degrades_to_similar(client, db, monkeypatch):
    _, ids = db
    _stub(monkeypatch, SearchResult(kind="llm_unavailable", similar=[_source(ids["record"])]))

    html = client.post("/search", data={"question": "манту?"}).text

    assert "Ответ сейчас не собрать" in html
    assert f'href="/records/{ids["record"]}"' in html


def test_unavailable_rendered(client, monkeypatch):
    _stub(monkeypatch, SearchResult(kind="unavailable"))

    html = client.post("/search", data={"question": "манту?"}).text

    assert "Поиск сейчас недоступен" in html
    assert "позже" in html


# ---------- правила кита ----------


def test_single_primary_button_on_search_screen(client):
    html = client.get("/search").text

    assert html.count("btn-primary") == 1  # «Спросить» — единственная primary


# ---------- плавающая «Найти» на ленте ----------


def test_fab_on_feed_with_records(client):
    html = client.get("/").text

    assert 'class="fab"' in html
    assert 'href="/search"' in html
    assert "Найти" in html
    # «Найти» — не primary: первичное действие ленты — «Добавить запись».
    assert "fab btn-primary" not in html


def test_no_fab_on_empty_feed(client, db):
    _, ids = db
    client.post(f"/profile/{ids['child']}")  # у ребёнка записей нет

    html = client.get("/").text

    assert 'class="fab"' not in html  # искать не в чем
