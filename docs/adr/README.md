# ADR — реестр архитектурных решений

Формат: один файл на решение, разделы **Статус · Контекст · Решение · Последствия**.
Правила работы с ADR (сверка перед задачей, запрет молчаливого пересмотра) — в `CLAUDE.MD`.

| № | Решение | Статус |
|---|---------|--------|
| [ADR-001](ADR-001-config-pydantic-settings.md) | Конфигурация через pydantic-settings, единственная точка чтения окружения | принято |
| [ADR-002](ADR-002-pre-commit-local-hooks.md) | Pre-commit: локальные хуки через `uv run ruff` | принято |
| [ADR-003](ADR-003-db-pgvector-image.md) | БД разработки: образ pgvector/pgvector:pg16, named volume, креды через `.env` | принято |
| [ADR-004](ADR-004-app-not-installed.md) | Пакет `app` не устанавливается в venv, импорт из рабочей копии | принято |
| [ADR-005](ADR-005-app-factory.md) | Фабрика приложения `create_app()` вместо модульного синглтона | принято |
| [ADR-006](ADR-006-authorship-via-account.md) | Авторство и админство — свойства учётной записи (структурные инварианты) | принято |
| [ADR-007](ADR-007-sync-sqlalchemy.md) | Синхронный SQLAlchemy; роуты с БД — `def`, не `async def` | принято |
| [ADR-008](ADR-008-self-hosted-fonts.md) | Шрифты self-hosted (woff2 в статике), не с CDN | принято |
| [ADR-009](ADR-009-seed-data-outside-git.md) | Данные seed вне git: код версионируется, данные — нет | принято |
| [ADR-010](ADR-010-session-auth-default-deny.md) | Cookie-сессия (подписанная, 180 дней) и default-deny защита роутов | принято |
| [ADR-011](ADR-011-vanilla-js-progressive.md) | JS: vanilla, прогрессивное улучшение, без сборки | принято |
| [ADR-012](ADR-012-status-two-axes.md) | Статусная модель записи: конвейер (parse_status) и подтверждение (confirmed_at) — две оси | принято |
| [ADR-013](ADR-013-extractor-domain-interface.md) | AI-экстрактор: доменный DTO + Protocol, провайдер — адаптер, промпты в адаптере | принято |
| [ADR-014](ADR-014-llm-via-aggregator.md) | LLM через OpenAI-совместимого посредника (RouterAI); размен приватности принят | принято |
| [ADR-015](ADR-015-release-contour.md) | Релизный контур: один VPS, изолированные prod/stg, ветки stg/main, деплой одним скриптом, Actions с ручным подтверждением прода | принято |
| [ADR-016](ADR-016-backups-and-prod2stg.md) | Бэкапы в Yandex Object Storage (rclone crypt, ретенция); стенд восстанавливается из бэкапа — проверка восстановимости и репетиция миграций | принято |
| [ADR-017](ADR-017-native-postgres-on-vps.md) | На VPS PostgreSQL нативно (PGDG + pgvector), docker остаётся для разработки | принято |
| [ADR-018](ADR-018-search-embeddings-routerai.md) | Поиск: эмбеддинги через RouterAI (вместо Voyage — изменение стека), таблица record_embeddings, reindex, журнал search_queries | принято |
