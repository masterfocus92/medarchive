"""Слой эмбеддингов — язык домена (паттерн ADR-013, решение ADR-018).

Снаружи адаптера — только Protocol и доменные ошибки: код поиска
и индексации не знает ни про RouterAI, ни про схему /embeddings.
"""

from typing import Protocol

import httpx

from app.config import Settings

# Эмбеддинги короткие — таймаут меньше экстракторного (там страницы-картинки).
REQUEST_TIMEOUT = 30.0


class EmbeddingError(Exception):
    """Эмбеддинг не получен; message — для логов, не для пользователя."""


class EmbeddingsNotConfigured(EmbeddingError):
    """Провайдер disabled — поиск честно недоступен, индексация
    пропускается; ни то ни другое не блокирует остальной продукт."""


class EmbeddingProvider(Protocol):
    """Любой провайдер эмбеддингов: тексты → векторы, порядок сохранён.

    model нужен хранилищу (record_embeddings.model): несовпадение
    с конфигом — признак устаревшего вектора (ADR-018).
    """

    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddings:
    """Адаптер OpenAI-совместимого /embeddings (RouterAI, ADR-018)."""

    provider = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = REQUEST_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                "/embeddings",
                json={"model": self.model, "input": texts},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"сетевая ошибка провайдера: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingError(f"HTTP {response.status_code}: {response.text[:300]}")

        try:
            data = response.json()["data"]
            # Порядок в data не гарантирован спецификацией — восстанавливаем
            # по index: перепутанные векторы разъехались бы с записями молча.
            vectors = [item["embedding"] for item in sorted(data, key=lambda i: i["index"])]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"неожиданная структура ответа провайдера: {exc}") from exc

        if len(vectors) != len(texts):
            raise EmbeddingError(f"текстов {len(texts)}, векторов {len(vectors)} — рассинхрон")
        return vectors


def build_embeddings(settings: Settings) -> EmbeddingProvider:
    """Фабрика по конфигу (паттерн build_extractor). Выключенный провайдер —
    доменная ошибка: индексация превратит её в warning, поиск — в честное
    «недоступен»."""
    if settings.embeddings_provider == "disabled":
        raise EmbeddingsNotConfigured("эмбеддинги отключены (EMBEDDINGS_PROVIDER=disabled)")
    return OpenAICompatibleEmbeddings(
        base_url=settings.embeddings_base_url,
        api_key=settings.embeddings_api_key,
        model=settings.embeddings_model,
    )
