"""Точка входа приложения.

Запуск (factory-режим — сборка требует настроек, импорт модуля нет):
uv run uvicorn app.main:create_app --factory --reload
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.middleware import AuthRequiredMiddleware
from app.routes import auth, pages, profiles, records, system

# Пути привязаны к положению пакета, а не к cwd: uvicorn с --reload
# и pytest могут запускаться с разными рабочими каталогами.
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Сессия живёт полгода: вход — одноразовый акт на устройство (OVERVIEW §6).
SESSION_MAX_AGE = 180 * 24 * 3600


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собирает приложение (ADR-005: фабрика, а не модульный синглтон).

    settings передаются параметром, чтобы тесты собирали приложение
    с явной конфигурацией без .env; по умолчанию — из окружения.
    """
    settings = settings if settings is not None else get_settings()

    app = FastAPI(title="Семейная медкарта")
    # Настройки — на app.state: роуты берут их через get_app_settings,
    # не трогая глобальный get_settings() (тестам не нужен .env).
    app.state.settings = settings

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(profiles.router)
    app.include_router(records.router)

    # Порядок важен: последняя добавленная middleware — внешняя.
    # SessionMiddleware должна отработать ДО AuthRequired, иначе
    # request.session ещё не существует.
    app.add_middleware(AuthRequiredMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        # https_only=False до деплоя за HTTPS (этап 8, см. ADR-010).
        https_only=False,
    )

    return app
