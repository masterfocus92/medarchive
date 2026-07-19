"""Минимальный chat-completion хелпер (T7.3, ❓9).

Работает на конфиге экстрактора: тот же RouterAI, та же модель, тот же
ключ — один провайдер, один биллинг (решение ❓9); отдельный конфиг
не заводится, пока не понадобился. Паттерн ADR-013: снаружи — доменные
ошибки и JSON-словарь, никакой схемы провайдера.
"""

import json
from typing import Protocol

import httpx

from app.config import Settings

REQUEST_TIMEOUT = 60.0
MAX_OUTPUT_TOKENS = 1000

_RETRY_PROMPT = (
    "Твой ответ не является валидным JSON. "
    "Верни ТОЛЬКО JSON по заданной схеме, без каких-либо пояснений."
)


class LLMError(Exception):
    """Ответ модели не получен; message — для логов, не для пользователя."""


class LLMNotConfigured(LLMError):
    """Провайдер disabled — вызывающий деградирует честно (B4)."""


class ChatProvider(Protocol):
    """Любой chat-провайдер: system + user → валидный JSON-словарь."""

    model: str

    def complete_json(self, system: str, user: str) -> dict: ...


class OpenAICompatibleChat:
    """Адаптер OpenAI-совместимого /chat/completions (RouterAI, ADR-014).

    «Только JSON» в промпте + один корректирующий повтор — как у
    экстрактора: на structured output прослойки не надеемся.
    """

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

    def complete_json(self, system: str, user: str) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for attempt in (1, 2):
            text = self._post(messages)
            try:
                return self._parse(text)
            except json.JSONDecodeError:
                if attempt == 1:
                    messages = messages + [
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": _RETRY_PROMPT},
                    ]
        raise LLMError("ответ модели не является валидным JSON после повтора")

    def _post(self, messages: list[dict]) -> str:
        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": MAX_OUTPUT_TOKENS,
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"сетевая ошибка провайдера: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"неожиданная структура ответа провайдера: {exc}") from exc

    @staticmethod
    def _parse(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Модели любят обрамлять JSON в ```json ... ``` вопреки инструкции.
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json").strip()
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("ожидался объект", cleaned, 0)
        return payload


def build_chat(settings: Settings) -> ChatProvider:
    """Фабрика по конфигу экстрактора (❓9). Выключенный провайдер —
    доменная ошибка: поиск деградирует до списка похожих (B4)."""
    if settings.extractor_provider != "openai_compatible":
        raise LLMNotConfigured(f"chat-провайдер недоступен ({settings.extractor_provider})")
    return OpenAICompatibleChat(
        base_url=settings.extractor_base_url,
        api_key=settings.extractor_api_key,
        model=settings.extractor_model,
    )
