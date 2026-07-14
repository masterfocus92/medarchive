"""Адаптер OpenAI-совместимого провайдера (RouterAI и любой другой, ADR-014).

Промпт живёт здесь — он часть провайдера, не домена (ADR-013).
На structured output прослойки не надеемся: «только JSON» в промпте +
валидация + один корректирующий повтор.
"""

import base64
import json
from datetime import date

import httpx
from pydantic import ValidationError

from app.models import FamilyMember
from app.services.extraction import ExtractionError, ExtractionResult

REQUEST_TIMEOUT = 90.0
MAX_OUTPUT_TOKENS = 2000

# Граница AI (OVERVIEW §4): извлекать и цитировать, не интерпретировать.
_SYSTEM_PROMPT = """Ты — экстрактор данных из российских медицинских документов.
Твоя задача — извлечь факты из документа на изображениях. Ты НЕ интерпретируешь
результаты, НЕ даёшь оценок и советов — только извлекаешь то, что написано.

Верни ТОЛЬКО валидный JSON без пояснений и без markdown-ограждений, с ключами:
- "title": краткое название документа (например "Общий анализ крови") или null
- "event_date": дата документа/события в формате YYYY-MM-DD или null
- "clinic": название клиники/учреждения или null
- "doctor": ФИО врача или null
- "record_type": тип одним-двумя словами ("анализ", "приём", "прививка",
  "выписка", "узи"...) или null
- "content": существенное содержание документа с цитатами показателей
  и заключений (без интерпретации) или null
- "suggested_patient_id": число — id пациента из списка семьи, если из
  документа однозначно понятно, чей он; иначе null

Если что-то не разобрал — честно ставь null, не выдумывай."""

_RETRY_PROMPT = (
    "Твой ответ не является валидным JSON. "
    "Верни ТОЛЬКО JSON по заданной схеме, без каких-либо пояснений."
)


class OpenAICompatibleExtractor:
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

    def extract(
        self, files: list[tuple[str, bytes]], family: list[FamilyMember]
    ) -> tuple[ExtractionResult, dict | None]:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self._family_prompt(family)},
                    *(
                        {
                            "type": "image_url",
                            "image_url": {"url": self._data_uri(mime, content)},
                        }
                        for mime, content in files
                    ),
                ],
            },
        ]

        raw = None
        for attempt in (1, 2):
            raw = self._post(messages)
            text = self._response_text(raw)
            try:
                return self._parse(text, family), raw
            except (json.JSONDecodeError, ValidationError):
                if attempt == 1:
                    # Один корректирующий повтор: прослойка ничего не
                    # гарантирует (ADR-014), но модель обычно исправляется.
                    messages = messages + [
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": _RETRY_PROMPT},
                    ]
        raise ExtractionError("ответ модели не является валидным JSON после повтора")

    @staticmethod
    def _family_prompt(family: list[FamilyMember]) -> str:
        members = "\n".join(
            f"- id={m.id}: {m.full_name}, дата рождения {m.birth_date.isoformat()}" for m in family
        )
        return (
            f"Члены семьи (для suggested_patient_id):\n{members}\n\n"
            "Извлеки данные из документа на изображениях."
        )

    @staticmethod
    def _data_uri(mime: str, content: bytes) -> str:
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    def _post(self, messages: list[dict]) -> dict:
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
            raise ExtractionError(f"сетевая ошибка провайдера: {exc}") from exc
        if response.status_code != 200:
            raise ExtractionError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    @staticmethod
    def _response_text(raw: dict) -> str:
        try:
            return raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractionError(f"неожиданная структура ответа провайдера: {exc}") from exc

    @staticmethod
    def _parse(text: str, family: list[FamilyMember]) -> ExtractionResult:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Модели любят обрамлять JSON в ```json ... ``` вопреки инструкции.
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json").strip()
        payload = json.loads(cleaned)

        # Пустые строки — это «нет данных», не данные.
        for key, value in list(payload.items()):
            if isinstance(value, str) and not value.strip():
                payload[key] = None

        result = ExtractionResult.model_validate(payload)

        # Предложению пациента доверяем, только если id реально из семьи.
        family_ids = {m.id for m in family}
        if result.suggested_patient_id not in family_ids:
            result.suggested_patient_id = None
        # Дата из будущего — подозрение на галлюцинацию распознавания.
        if result.event_date and result.event_date > date.today():
            result.event_date = None
        return result
