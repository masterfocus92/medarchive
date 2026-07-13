"""Тесты каркаса приложения (T1.2-BE).

Ключевое свойство: приложение собирается и оба роута отвечают
без поднятой БД и без настроенного окружения — /health это liveness,
а не readiness, и падать из-за мёртвого контейнера БД он не должен.
Поэтому тесты не готовят ни .env, ни docker.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TEMPLATES_DIR = Path("app/templates")
CSS_DIR = Path("app/static/css")

# Сырой hex-цвет: #FFF, #1E1C19, #RRGGBBAA.
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")


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


def test_index_shows_empty_state(client):
    # Пустое состояние — приглашение, не заглушка (DESIGN.MD §5):
    # примитив empty из кита, заголовок + одна фраза.
    response = client.get("/")

    assert response.status_code == 200
    assert 'class="empty"' in response.text
    assert "Записей пока нет" in response.text


def test_css_is_served(client):
    for name in ("tokens.css", "app.css"):
        response = client.get(f"/static/css/{name}")
        assert response.status_code == 200, f"{name} не отдаётся"


def test_templates_have_no_raw_styles():
    """Страж дизайн-системы (DESIGN.MD §2): в шаблонах нет сырых цветов
    и inline-стилей — только классы примитивов и токены. Проверяет все
    шаблоны, включая будущие: новый экран с #hex упадёт здесь."""
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not HEX_COLOR.search(text), f"сырой цвет в {path}"
        assert "style=" not in text, f"inline-стиль в {path}"


def test_screen_css_uses_tokens_only():
    """В CSS экранов цвета существуют только как var(--...) из tokens.css —
    сам tokens.css единственное место, где живут hex-значения (копия кита)."""
    for path in CSS_DIR.glob("*.css"):
        if path.name == "tokens.css":
            continue
        assert not HEX_COLOR.search(path.read_text(encoding="utf-8")), f"сырой цвет в {path}"


def test_static_is_mounted(client):
    # Файл может отсутствовать (статика появится в T1.4) — важно,
    # что маршрут /static обслуживается приложением: 404 от StaticFiles,
    # а не «маршрут не существует» с дефолтным JSON-телом FastAPI.
    response = client.get("/static/nonexistent.css")

    assert response.status_code == 404
    assert "not found" in response.text.lower()
