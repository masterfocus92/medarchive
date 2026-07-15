"""Тесты плашки стенда (T6.5.3): стенд нельзя перепутать с продом —
его данные перезаписываются каждым синком (ADR-015/016).

БД не нужна: плашка проверяется на публичном /login.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BANNER_TEXT = "Тестовый стенд"


def _client(app_env: str | None = None) -> TestClient:
    kwargs = {"app_env": app_env} if app_env is not None else {}
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
        **kwargs,
    )
    return TestClient(create_app(settings))


def test_stg_shows_banner_on_every_screen():
    html = _client("stg").get("/login").text

    assert 'class="env-banner"' in html
    assert BANNER_TEXT in html


def test_prod_and_dev_have_no_banner():
    for env in ("prod", "dev"):
        html = _client(env).get("/login").text
        assert BANNER_TEXT not in html, env

    # Дефолт (переменная не задана) — dev, плашки нет.
    assert BANNER_TEXT not in _client().get("/login").text


def test_two_apps_in_one_process_do_not_leak_banner():
    """Флаг стенда живёт в состоянии приложения, не в общем Jinja-окружении:
    прод-приложение, собранное ПОСЛЕ стендового, плашку не наследует."""
    stg = _client("stg")
    prod = _client("prod")

    assert BANNER_TEXT in stg.get("/login").text
    assert BANNER_TEXT not in prod.get("/login").text
    # И наоборот — стенд не потерял плашку после сборки прод-приложения.
    assert BANNER_TEXT in stg.get("/login").text
