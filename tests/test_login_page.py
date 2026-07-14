"""Тесты экрана входа (T2.3-FE).

Экран входа собран на примитивах кита (field, btn-primary) — эти тесты
проверяют именно вёрстку контракта T2.3-FE, а не логику auth (она в
test_auth.py). БД здесь не нужна: GET /login к базе не обращается, а
ветку ошибки проверяем, подменив authenticate, — тест остаётся быстрым
и не зависит от поднятого docker.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app


def _test_settings() -> Settings:
    # Явные настройки как в test_app: экрану входа БД не нужна, URL фиктивный.
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
    )


def _client() -> TestClient:
    return TestClient(create_app(_test_settings()), follow_redirects=False)


def test_login_renders_field_primitives():
    """Оба поля собраны на примитиве `field` кита: .field + .control,
    имена контракта T2.2-BE сохранены (name=email/password)."""
    text = _client().get("/login").text

    assert text.count('class="field"') == 2, "email и пароль — оба на примитиве field"
    assert text.count('class="control"') == 2, "оба поля через .control кита"
    assert 'name="email"' in text
    assert 'name="password"' in text


def test_login_has_single_primary_button():
    """Одна первичная кнопка на экран (DESIGN.MD §5): ровно одна btn-primary."""
    text = _client().get("/login").text

    assert text.count("btn-primary") == 1, "одна и только одна первичная кнопка"
    assert "Войти" in text


def test_login_accessibility_attributes():
    """Вход с телефона за секунды: автофокус на email, autocomplete для
    менеджеров паролей, поля связаны с label через for/id."""
    text = _client().get("/login").text

    assert "autofocus" in text, "фокус сразу в email"
    assert 'autocomplete="username"' in text
    assert 'autocomplete="current-password"' in text
    assert 'for="f-email"' in text and 'id="f-email"' in text, "label связан с control"


def test_login_clean_get_has_no_error_block():
    """На чистом GET ошибки нет: ни .field.error, ни alert — форма-приглашение,
    а не форма-с-ошибкой."""
    text = _client().get("/login").text

    assert "field error" not in text
    assert 'role="alert"' not in text


def test_login_error_uses_field_error_helper(monkeypatch):
    """Отказ входа рендерится через механизм кита .field.error/.helper с
    role="alert"; введённый email сохраняется (DESIGN.MD §5 — ошибка на месте).
    authenticate подменён на отказ — БД не нужна."""
    app = create_app(_test_settings())
    # get_session не используется (authenticate подменён), но зависимость
    # должна чем-то разрешиться — отдаём заглушку вместо реальной сессии.
    app.dependency_overrides[get_session] = lambda: iter([None])
    monkeypatch.setattr("app.routes.auth.authenticate", lambda db, email, password: None)

    client = TestClient(app, follow_redirects=False)
    response = client.post("/login", data={"email": "user@example.com", "password": "x"})

    assert response.status_code == 200
    assert "field error" in response.text, "ошибка — через .field.error кита"
    assert 'role="alert"' in response.text, "ошибка озвучивается скринридеру"
    assert "user@example.com" in response.text, "введённый email сохранён в форме"
