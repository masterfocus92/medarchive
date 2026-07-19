"""Тесты chat-хелпера (T7.3): JSON-контракт с ретраем, доменные ошибки.

Сеть — httpx.MockTransport, как у экстрактора и эмбеддингов.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.services.llm import LLMError, LLMNotConfigured, OpenAICompatibleChat, build_chat


def _completion(text: str) -> dict:
    return {"model": "anthropic/claude-sonnet-5", "choices": [{"message": {"content": text}}]}


def _chat(handler) -> OpenAICompatibleChat:
    return OpenAICompatibleChat(
        base_url="https://provider.test/api/v1",
        api_key="test-key",
        model="anthropic/claude-sonnet-5",
        transport=httpx.MockTransport(handler),
    )


GOOD_JSON = '{"answer": "12 марта", "source_ids": [3]}'


def test_happy_path_returns_parsed_dict():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "anthropic/claude-sonnet-5"
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(200, json=_completion(GOOD_JSON))

    assert _chat(handler).complete_json("system", "user") == {
        "answer": "12 марта",
        "source_ids": [3],
    }


def test_json_in_code_fences_is_parsed():
    def handler(request):
        return httpx.Response(200, json=_completion(f"```json\n{GOOD_JSON}\n```"))

    assert _chat(handler).complete_json("s", "u")["answer"] == "12 марта"


def test_invalid_json_retried_once_then_ok():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_completion("Отвечаю прозой, без JSON"))
        return httpx.Response(200, json=_completion(GOOD_JSON))

    assert _chat(handler).complete_json("s", "u")["source_ids"] == [3]
    assert calls["n"] == 2


def test_double_invalid_json_raises_domain_error():
    def handler(request):
        return httpx.Response(200, json=_completion("не json"))

    with pytest.raises(LLMError):
        _chat(handler).complete_json("s", "u")


def test_http_error_raises_domain_error():
    def handler(request):
        return httpx.Response(402, json={"error": "Недостаточно средств"})

    with pytest.raises(LLMError) as exc_info:
        _chat(handler).complete_json("s", "u")

    assert "402" in str(exc_info.value)


def test_factory_uses_extractor_config():
    # ❓9: генерация — той же моделью и тем же ключом, что экстрактор.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
        extractor_provider="openai_compatible",
        extractor_base_url="https://provider.test/api/v1",
        extractor_model="anthropic/claude-sonnet-5",
        extractor_api_key="key",
    )

    assert build_chat(settings).model == "anthropic/claude-sonnet-5"


def test_factory_disabled_raises_not_configured():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
    )

    with pytest.raises(LLMNotConfigured):
        build_chat(settings)
