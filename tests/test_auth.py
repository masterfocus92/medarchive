"""Тесты аутентификации (T2.2-BE): вход, сессия, выход, default-deny.

Приложение собирается с явными тестовыми настройками (фабрика по ADR-005),
БД — тестовая из conftest, сессия БД подменяется через dependency_overrides.
Каждому тесту — свежий TestClient: своя cookie-банка, сессии тестов
не пересекаются.
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
from app.services.security import hash_password

AUTH_TEST_DB = "medcard_test_auth"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


@pytest.fixture(scope="module")
def engine(admin_conn):
    recreate_db(admin_conn, AUTH_TEST_DB)
    command.upgrade(alembic_config(db_url(AUTH_TEST_DB)), "head")
    eng = create_engine(db_url(AUTH_TEST_DB))

    # Одна учётка для всех тестов модуля.
    with Session(eng) as session:
        family = Family()
        member = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        session.add(
            Account(
                member=member,
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                is_admin=True,
            )
        )
        session.commit()

    yield eng
    eng.dispose()
    drop_db(admin_conn, AUTH_TEST_DB)


@pytest.fixture(scope="module")
def app(engine):
    settings = Settings(
        _env_file=None,
        database_url=db_url(AUTH_TEST_DB),
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
    # follow_redirects=False: редиректы — предмет проверок, а не транспорт.
    return TestClient(app, follow_redirects=False)


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password})


def test_login_page_is_public(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text


def test_root_requires_auth(client):
    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_health_and_static_are_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200


def test_login_success_opens_app(client):
    response = _login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Сессия долгоживущая: cookie с Max-Age, а не сессионная браузерная.
    assert "max-age" in response.headers["set-cookie"].lower()

    # После входа главная рендерится: пустое состояние на примитиве кита
    # (тесты рендера переехали сюда из test_app — «/» теперь защищена).
    index = client.get("/")
    assert index.status_code == 200
    assert 'class="empty"' in index.text
    # Заголовок персонализирован активным профилем (T2.5).
    assert "записей пока нет" in index.text


def test_wrong_password_rejected(client):
    response = _login(client, password="wrong-password-1")

    assert response.status_code == 200
    assert 'name="email"' in response.text  # снова форма
    assert EMAIL in response.text  # введённый email сохранён
    # Сессии нет — приложение по-прежнему закрыто.
    assert client.get("/").status_code == 303


def test_unknown_email_indistinguishable_from_wrong_password(client):
    # Утечка «такой email существует» недопустима: оба отказа —
    # один и тот же ответ байт-в-байт (кроме отражённого email).
    wrong_password = _login(client, password="wrong-password-1")
    unknown_email = _login(client, email="ghost@test.local", password="wrong-password-1")

    assert wrong_password.status_code == unknown_email.status_code
    normalized_a = wrong_password.text.replace(EMAIL, "X")
    normalized_b = unknown_email.text.replace("ghost@test.local", "X")
    assert normalized_a == normalized_b


def test_logout_clears_session(client):
    _login(client)
    assert client.get("/").status_code == 200

    response = client.post("/logout")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # Сессии больше нет.
    assert client.get("/").status_code == 303


def test_logout_without_session_does_not_fail(client):
    response = client.post("/logout")

    assert response.status_code == 303


def test_settings_require_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(Exception) as exc_info:
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost:5432/db",
            files_dir="./files",
        )

    assert "secret_key" in str(exc_info.value).lower()
