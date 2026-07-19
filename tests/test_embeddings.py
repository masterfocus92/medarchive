"""Тесты адаптера эмбеддингов (T7.1).

Сеть подменяется httpx.MockTransport — как в тестах экстрактора:
качество живого провайдера проверяется руками, тесты проверяют контракт.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.services.embeddings import (
    EmbeddingError,
    EmbeddingsNotConfigured,
    OpenAICompatibleEmbeddings,
    build_embeddings,
)


def _response(vectors: list[list[float]]) -> dict:
    # Порядок в ответе перемешан сознательно: адаптер обязан сортировать
    # по index, а не полагаться на порядок массива data у провайдера.
    data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"object": "list", "data": list(reversed(data)), "model": "baai/bge-m3"}


def _adapter(handler) -> OpenAICompatibleEmbeddings:
    return OpenAICompatibleEmbeddings(
        base_url="https://provider.test/api/v1",
        api_key="test-key",
        model="baai/bge-m3",
        transport=httpx.MockTransport(handler),
    )


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
        **kwargs,
    )


def test_happy_path_returns_vectors_in_input_order():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "baai/bge-m3"
        assert payload["input"] == ["первый", "второй"]
        return httpx.Response(200, json=_response([[0.1, 0.2], [0.3, 0.4]]))

    vectors = _adapter(handler).embed(["первый", "второй"])

    # Ответ пришёл перемешанным (см. _response) — адаптер восстановил порядок.
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_single_text_batch():
    def handler(request):
        return httpx.Response(200, json=_response([[1.0, 0.0]]))

    assert _adapter(handler).embed(["вопрос"]) == [[1.0, 0.0]]


def test_http_error_raises_domain_error():
    def handler(request):
        return httpx.Response(402, json={"error": "Недостаточно средств"})

    with pytest.raises(EmbeddingError) as exc_info:
        _adapter(handler).embed(["вопрос"])

    assert "402" in str(exc_info.value)


def test_network_error_raises_domain_error():
    def handler(request):
        raise httpx.ConnectError("нет сети")

    with pytest.raises(EmbeddingError):
        _adapter(handler).embed(["вопрос"])


def test_vector_count_mismatch_raises_domain_error():
    # Провайдер вернул меньше векторов, чем текстов — тихо продолжать
    # нельзя: векторы разъедутся с записями.
    def handler(request):
        return httpx.Response(200, json=_response([[0.1, 0.2]]))

    with pytest.raises(EmbeddingError):
        _adapter(handler).embed(["первый", "второй"])


def test_unexpected_response_shape_raises_domain_error():
    def handler(request):
        return httpx.Response(200, json={"object": "list"})

    with pytest.raises(EmbeddingError):
        _adapter(handler).embed(["вопрос"])


def test_factory_disabled_raises_not_configured():
    with pytest.raises(EmbeddingsNotConfigured):
        build_embeddings(_settings())


def test_factory_builds_adapter_from_config():
    provider = build_embeddings(
        _settings(
            embeddings_provider="openai_compatible",
            embeddings_base_url="https://provider.test/api/v1",
            embeddings_model="baai/bge-m3",
            embeddings_api_key="key",
        )
    )

    assert provider.model == "baai/bge-m3"


def test_incomplete_embeddings_config_fails_at_startup():
    # Включённый провайдер с дырявым конфигом — ошибка старта,
    # а не сюрприз при первом поиске (паттерн экстрактора).
    with pytest.raises(ValueError):
        _settings(embeddings_provider="openai_compatible", embeddings_model="baai/bge-m3")
