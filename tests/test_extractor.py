"""Тесты адаптера OpenAI-совместимого провайдера (T4.2).

Сеть подменяется httpx.MockTransport — тесты не зависят ни от ключа,
ни от RouterAI. Живое качество проверяется отдельно (app.tools.try_extractor).
"""

import json
from datetime import date

import httpx
import pytest

from app.config import Settings
from app.models import FamilyMember
from app.services.extraction import ExtractionError, ExtractorNotConfigured, build_extractor
from app.services.extractor_openai import OpenAICompatibleExtractor

FAMILY = [
    FamilyMember(
        id=1, last_name="Иванов", first_name="Дмитрий", birth_date=date(1990, 1, 1), sex="male"
    ),
    FamilyMember(
        id=2, last_name="Иванова", first_name="Анна", birth_date=date(2024, 1, 1), sex="female"
    ),
]

GOOD_JSON = json.dumps(
    {
        "title": "Общий анализ крови",
        "event_date": "2026-03-12",
        "clinic": "Клиника Здоровье",
        "doctor": "Петрова С.И.",
        "record_type": "анализ",
        "content": "Гемоглобин: 132 г/л",
        "suggested_patient_id": 2,
    },
    ensure_ascii=False,
)


def _completion(text: str) -> dict:
    return {"model": "anthropic/claude-sonnet-5", "choices": [{"message": {"content": text}}]}


def _extractor(handler) -> OpenAICompatibleExtractor:
    return OpenAICompatibleExtractor(
        base_url="https://provider.test/api/v1",
        api_key="test-key",
        model="anthropic/claude-sonnet-5",
        transport=httpx.MockTransport(handler),
    )


def test_happy_path_parses_all_fields():
    def handler(request):
        # Авторизация и модель уходят как надо
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content)["model"] == "anthropic/claude-sonnet-5"
        return httpx.Response(200, json=_completion(GOOD_JSON))

    result, raw = _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert result.title == "Общий анализ крови"
    assert result.event_date == date(2026, 3, 12)
    assert result.suggested_patient_id == 2
    assert raw is not None  # артефакт провайдера для журнала прогонов


def test_json_in_code_fences_is_parsed():
    def handler(request):
        return httpx.Response(200, json=_completion(f"```json\n{GOOD_JSON}\n```"))

    result, _ = _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert result.title == "Общий анализ крови"


def test_invalid_json_retried_once_then_ok():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_completion("Вот ваши данные: гемоглобин 132"))
        return httpx.Response(200, json=_completion(GOOD_JSON))

    result, _ = _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert calls["n"] == 2
    assert result.title == "Общий анализ крови"


def test_double_invalid_json_raises_domain_error():
    def handler(request):
        return httpx.Response(200, json=_completion("не json"))

    with pytest.raises(ExtractionError):
        _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)


def test_http_error_raises_domain_error():
    def handler(request):
        return httpx.Response(402, json={"error": "Недостаточно средств"})

    with pytest.raises(ExtractionError) as exc_info:
        _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert "402" in str(exc_info.value)


def test_alien_suggested_patient_becomes_none():
    # Модель предложила id, которого нет в семье — не доверяем.
    payload = json.loads(GOOD_JSON)
    payload["suggested_patient_id"] = 777

    def handler(request):
        return httpx.Response(200, json=_completion(json.dumps(payload, ensure_ascii=False)))

    result, _ = _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert result.suggested_patient_id is None


def test_empty_strings_become_none():
    payload = {**json.loads(GOOD_JSON), "clinic": "", "doctor": "  "}

    def handler(request):
        return httpx.Response(200, json=_completion(json.dumps(payload, ensure_ascii=False)))

    result, _ = _extractor(handler).extract([("image/jpeg", b"fake")], FAMILY)

    assert result.clinic is None
    assert result.doctor is None


def test_factory_disabled_raises_not_configured():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        files_dir="./files",
        secret_key="test-secret-key-only-for-tests-0123456789",
    )

    with pytest.raises(ExtractorNotConfigured):
        build_extractor(settings)
