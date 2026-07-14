from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Один импорт регистрирует все таблицы в Base.metadata (см. app/models/__init__.py) —
# без него автогенерация видит пустую схему и предлагает всё дропнуть.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False обязателен: тесты гоняют миграции через
    # API в одном процессе с приложением, и дефолтное True молча глушит уже
    # созданные логгеры роутов — пропадают warning'и, которые тесты проверяют.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Приоритет URL: явно заданный (тесты подставляют тестовую БД) →
# конфиг приложения (ADR-001: окружение читается только через app.config).
if not config.get_main_option("sqlalchemy.url"):
    from app.config import get_settings

    config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """Офлайн-режим: генерация SQL без подключения к БД."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Онлайн-режим: миграции через живое подключение."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
