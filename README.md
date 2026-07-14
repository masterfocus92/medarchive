# medarchive

Семейная медкарта (POC). Спека — `docs/OVERVIEW.MD`, дизайн — `docs/DESIGN.MD`.

## Как поднять окружение

1. `cp .env.example .env` — дефолты рабочие, менять не обязательно.
2. `docker compose up -d` — PostgreSQL 16 + pgvector на `localhost:5432`.
3. `uv sync` — зависимости и виртуальное окружение.
4. `uv run pre-commit install` — линтер на каждый коммит.
5. Проверка: `uv run pytest` — тесты зелёные.

## Запуск приложения

```
uv run uvicorn app.main:create_app --factory --reload
```

Приложение — http://localhost:8000, liveness — http://localhost:8000/health.
Запускать из корня проекта (импорт пакета `app` — из рабочей копии).
Нужен `SECRET_KEY` в `.env` (см. `.env.example`).
