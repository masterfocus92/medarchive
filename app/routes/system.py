"""Служебные роуты."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness-проба: жив ли процесс приложения.

    Сознательно не трогает БД: иначе перезапуск контейнера Postgres
    делает «мёртвым» живое приложение, и мониторинг перезапускает
    не то, что сломалось.
    """
    return {"status": "ok"}
