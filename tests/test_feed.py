"""Тесты ленты (T5.1/T5.2): сортировки, фильтр удалённых, статус-бейджи."""

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
from app.repositories.records import list_by_patient
from app.services.security import hash_password

FEED_TEST_DB = "medcard_test_feed"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    recreate_db(admin_conn, FEED_TEST_DB)
    command.upgrade(alembic_config(db_url(FEED_TEST_DB)), "head")
    engine = create_engine(db_url(FEED_TEST_DB))

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

        # Три записи с управляемыми датами:
        #   A: внесена 1-й, событие свежайшее (10 июня)
        #   B: внесена 2-й, события нет
        #   C: внесена 3-й (свежайшее внесение), событие старое (1 января)
        def make(comment, created, event):
            record = HealthRecord(
                author=account, patient=operator, comment=comment, event_date=event
            )
            session.add(record)
            session.flush()
            record.created_at = created
            return record

        a = make("A", datetime(2026, 7, 1, 10, tzinfo=UTC), date(2026, 6, 10))
        b = make("B", datetime(2026, 7, 2, 10, tzinfo=UTC), None)
        c = make("C", datetime(2026, 7, 3, 10, tzinfo=UTC), date(2026, 1, 1))
        deleted = make("удалёнка", datetime(2026, 7, 4, 10, tzinfo=UTC), None)
        deleted.deleted_at = datetime.now(UTC)

        # Второй пациент без учётки — для критерия «лента только активного
        # профиля»: его запись не должна попадать в ленту оператора.
        child = FamilyMember(
            family=family,
            last_name="Тестова",
            first_name="Ребёнок",
            birth_date=date(2020, 5, 5),
            sex="female",
        )
        child_record = HealthRecord(author=account, patient=child, comment="Д")
        session.add(child_record)
        session.commit()
        ids = {
            "member": operator.id,
            "a": a.id,
            "b": b.id,
            "c": c.id,
            "deleted": deleted.id,
            "child": child.id,
            "child_record": child_record.id,
        }

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, FEED_TEST_DB)


@pytest.fixture(scope="module")
def app(db_setup, tmp_path_factory):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(FEED_TEST_DB),
        files_dir=tmp_path_factory.mktemp("feed-files"),
        secret_key="test-secret-key-only-for-tests-0123456789",
    )
    application = create_app(settings)
    test_sessionmaker = sessionmaker(bind=engine)

    def override_session():
        with test_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app):
    test_client = TestClient(app, follow_redirects=False)
    test_client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    return test_client


# ---------- Репозиторий ----------


def test_sort_by_created_is_insertion_chronicle(db_setup):
    engine, ids = db_setup
    with Session(engine) as session:
        records = list_by_patient(session, ids["member"], sort="created")

    assert [r.comment for r in records] == ["C", "B", "A"]


def test_sort_by_event_uses_created_as_fallback(db_setup):
    engine, ids = db_setup
    with Session(engine) as session:
        records = list_by_patient(session, ids["member"], sort="event")

    # B без даты события встаёт по дате внесения (2 июля):
    # B (2.07) → A (10.06) → C (1.01)
    assert [r.comment for r in records] == ["B", "A", "C"]


def test_deleted_records_are_invisible_by_default(db_setup):
    engine, ids = db_setup
    with Session(engine) as session:
        records = list_by_patient(session, ids["member"], sort="created")

    assert ids["deleted"] not in [r.id for r in records]


# ---------- Лента на главной ----------


def test_feed_lists_records_with_links(client, db_setup):
    _, ids = db_setup

    html = client.get("/").text

    for key in ("a", "b", "c"):
        assert f'href="/records/{ids[key]}"' in html
    assert f'href="/records/{ids["deleted"]}"' not in html


def test_sort_toggle_changes_order(client, db_setup):
    _, ids = db_setup

    by_created = client.get("/").text
    by_event = client.get("/?sort=event").text

    assert by_created.index(f'/records/{ids["c"]}"') < by_created.index(f'/records/{ids["a"]}"')
    assert by_event.index(f'/records/{ids["b"]}"') < by_event.index(f'/records/{ids["a"]}"')


def test_invalid_sort_falls_back_to_default(client, db_setup):
    _, ids = db_setup

    html = client.get("/?sort=hacker").text

    assert f'href="/records/{ids["c"]}"' in html  # рендер не сломался


def test_status_badge_only_on_unconfirmed(client, db_setup):
    engine, ids = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, ids["a"])
        record.parse_status = "parse_failed"
        record.confirmed_at = None
        session.commit()

    html = client.get("/").text

    assert "разбор не удался" in html  # бейдж неподтверждённой
    # Подтверждённые (B, C — заметки) бейджа не носят:
    assert html.count("подтверждено") == 0

    # починим обратно, чтобы не влиять на другие тесты
    with Session(engine) as session:
        record = session.get(HealthRecord, ids["a"])
        record.parse_status = "none"
        record.confirmed_at = record.created_at
        session.commit()


def test_feed_is_scoped_to_active_profile(client, db_setup):
    _, ids = db_setup

    # По умолчанию активен профиль самого оператора — записи ребёнка нет.
    html = client.get("/").text
    assert f'href="/records/{ids["child_record"]}"' not in html

    # Переключение профиля меняет состав ленты целиком, не дополняет её.
    client.post(f"/profile/{ids['child']}")
    html = client.get("/").text
    assert f'href="/records/{ids["child_record"]}"' in html
    for key in ("a", "b", "c"):
        assert f'href="/records/{ids[key]}"' not in html


def test_feed_title_is_active_profile_name(client, db_setup):
    """Заголовок ленты — имя активного профиля (кит v2): «на чью карту
    я смотрю» читается прямо над записями."""
    _, ids = db_setup

    html = client.get("/").text
    assert '<h1 class="feed-title">Оператор</h1>' in html

    client.post(f"/profile/{ids['child']}")
    html = client.get("/").text
    assert '<h1 class="feed-title">Ребёнок</h1>' in html


def test_personal_accents_follow_active_profile(client, db_setup):
    """Кит v2: активный профиль и заголовок ленты несут класс персонального
    акцента (по порядку членов семьи); переключение меняет цвет."""
    _, ids = db_setup

    html = client.get("/").text
    # Оператор — первый член семьи → accent-1.
    assert 'class="who accent-1" aria-selected="true"' in html
    assert 'class="feed-head accent-1"' in html

    client.post(f"/profile/{ids['child']}")
    html = client.get("/").text
    assert 'class="who accent-2" aria-selected="true"' in html
    assert 'class="feed-head accent-2"' in html


def test_temporary_blocks_are_gone(client):
    html = client.get("/").text

    assert "Ждут проверки" not in html
    assert "записей:" not in html.lower()  # счётчик упразднён
