"""Тесты профилей (T2.4-BE): активный профиль и переключение.

Логика выбора активного профиля покрыта юнитами напрямую (снаружи она
станет видимой только с переключателем T2.5-FE); роут переключения —
интеграционно. В тестовой БД две семьи: вторая нужна негативному
сценарию «чужой член семьи».
"""

from datetime import date

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Account, Family, FamilyMember
from app.services.profiles import SESSION_ACTIVE_MEMBER_KEY, initials, resolve_active_member
from app.services.security import hash_password

PROFILES_TEST_DB = "medcard_test_profiles"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


# ---------- Юниты: инициалы ----------


def test_initials_are_first_name_plus_last_name():
    # Монограмма «имя + фамилия» — без парсинга строк (T2.7).
    assert initials("Анна", "Иванова") == "АИ"
    assert initials("Дмитрий", "Иванов") == "ДИ"


# ---------- Юниты: активный профиль ----------


def _make_family_members():
    operator_member = FamilyMember(
        id=1, last_name="Иванов", first_name="Дмитрий", birth_date=date(1990, 1, 1), sex="male"
    )
    child = FamilyMember(
        id=2, last_name="Иванова", first_name="Анна", birth_date=date(2024, 1, 1), sex="female"
    )
    account = Account(id=10, family_member_id=1, email=EMAIL, password_hash="x")
    return account, [operator_member, child]


def test_active_defaults_to_operator():
    account, members = _make_family_members()

    active = resolve_active_member({}, account, members)

    assert active.id == 1


def test_active_respects_session_choice():
    account, members = _make_family_members()

    active = resolve_active_member({SESSION_ACTIVE_MEMBER_KEY: 2}, account, members)

    assert active.id == 2


def test_active_falls_back_on_alien_id_in_session():
    # В сессии мусор (id не из этой семьи) — молча дефолт, не ошибка:
    # битая сессия не должна ломать главную.
    account, members = _make_family_members()

    active = resolve_active_member({SESSION_ACTIVE_MEMBER_KEY: 777}, account, members)

    assert active.id == 1


# ---------- Интеграция: роут переключения ----------


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    """Тестовая БД с двумя семьями; возвращает id членов для тестов."""
    recreate_db(admin_conn, PROFILES_TEST_DB)
    command.upgrade(alembic_config(db_url(PROFILES_TEST_DB)), "head")
    engine = create_engine(db_url(PROFILES_TEST_DB))

    with Session(engine) as session:
        family_a = Family()
        operator = FamilyMember(
            family=family_a,
            last_name="Иванов",
            first_name="Дмитрий",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        adult2 = FamilyMember(
            family=family_a,
            last_name="Иванова",
            first_name="Мария",
            birth_date=date(1992, 1, 1),
            sex="female",
        )
        child = FamilyMember(
            family=family_a,
            last_name="Иванова",
            first_name="Анна",
            birth_date=date(2024, 1, 1),
            sex="female",
        )
        family_b = Family()
        stranger = FamilyMember(
            family=family_b,
            last_name="Чужаков",
            first_name="Пётр",
            birth_date=date(1985, 1, 1),
            sex="male",
        )
        session.add_all(
            [
                adult2,
                child,
                stranger,
                Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD)),
            ]
        )
        session.commit()
        ids = {"child": child.id, "stranger": stranger.id}

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, PROFILES_TEST_DB)


@pytest.fixture(scope="module")
def app(db_setup):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(PROFILES_TEST_DB),
        files_dir="./files",
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
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in(client):
    client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    return client


def test_switch_to_own_family_member(logged_in, db_setup):
    _, ids = db_setup

    response = logged_in.post(f"/profile/{ids['child']}")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Главная после переключения открывается (полная видимая проверка
    # активного профиля — с переключателем T2.5-FE).
    assert logged_in.get("/").status_code == 200


def test_foreign_family_member_is_404(logged_in, db_setup):
    _, ids = db_setup

    response = logged_in.post(f"/profile/{ids['stranger']}")

    assert response.status_code == 404


def test_nonexistent_member_is_404(logged_in):
    response = logged_in.post("/profile/999999")

    assert response.status_code == 404


# ---------- Интеграция: переключатель в шапке (T2.5-FE) ----------


def test_switcher_shows_all_members_one_active(logged_in):
    html = logged_in.get("/").text

    # Три лица семьи, активный ровно один (по умолчанию — вошедший).
    assert html.count('class="mono-badge"') == 3
    assert html.count('aria-selected="true"') == 1
    # Выход присутствует и обособлен от переключателя: настоящая форма.
    assert 'action="/logout"' in html
    assert "Выйти" in html


def test_switcher_moves_active_mark_after_switch(logged_in, db_setup):
    _, ids = db_setup

    logged_in.post(f"/profile/{ids['child']}")
    html = logged_in.get("/").text

    # Активная пометка — внутри формы переключения на ребёнка.
    child_form = html.split(f'action="/profile/{ids["child"]}"', 1)[1].split("</form>", 1)[0]
    assert 'aria-selected="true"' in child_form
    assert html.count('aria-selected="true"') == 1


def test_empty_state_is_personalized(logged_in, db_setup):
    _, ids = db_setup

    # По умолчанию — вошедший (Дмитрий), после переключения — Анна.
    assert "Дмитрий — записей пока нет" in logged_in.get("/").text
    logged_in.post(f"/profile/{ids['child']}")
    assert "Анна — записей пока нет" in logged_in.get("/").text


def test_login_page_has_no_switcher(client):
    html = client.get("/login").text

    assert "mono-badge" not in html
    assert 'action="/logout"' not in html


def test_switch_without_session_redirects_to_login(client, db_setup):
    _, ids = db_setup

    response = client.post(f"/profile/{ids['child']}")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
