"""Тесты каркаса приложения (T1.2-BE).

Ключевое свойство: приложение собирается и оба роута отвечают
без поднятой БД и без настроенного окружения — /health это liveness,
а не readiness, и падать из-за мёртвого контейнера БД он не должен.
Поэтому тесты не готовят ни .env, ни docker.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    # Каждому тесту — свежее приложение: проверяем заодно,
    # что фабрика не хранит глобального состояния между сборками.
    return TestClient(create_app())


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_html(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # Страница не пустая — шаблон реально отрендерился, а не отдал заглушку.
    assert response.text.strip()


def test_static_is_mounted(client):
    # Файл может отсутствовать (статика появится в T1.4) — важно,
    # что маршрут /static обслуживается приложением: 404 от StaticFiles,
    # а не «маршрут не существует» с дефолтным JSON-телом FastAPI.
    response = client.get("/static/nonexistent.css")

    assert response.status_code == 404
    assert "not found" in response.text.lower()
