"""Точка входа приложения.

Запуск: uv run uvicorn app.main:app --reload (из корня проекта, ADR-004).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import pages, system

# Пути привязаны к положению пакета, а не к cwd: uvicorn с --reload
# и pytest могут запускаться с разными рабочими каталогами.
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Собирает приложение (ADR-005: фабрика, а не модульный синглтон).

    Фабрика сознательно не читает настройки (get_settings): роутам каркаса
    БД не нужна, и приложение обязано стартовать без поднятого docker
    и настроенного .env. Конфиг подключится там, где появится первый
    потребитель (сессия БД, T1.3+).
    """
    app = FastAPI(title="Семейная медкарта")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)

    return app


app = create_app()
