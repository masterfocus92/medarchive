"""Поиск-чат (T7.3): вопрос словами → ответ строго по записям с источниками.

Цепочка потока поиска: эмбеддинг вопроса → pgvector топ-K → LLM по
выдержкам → ответ + источники. Ответ без источников не существует (B6):
не на что опереться — честное «не нашлось». Каждый непустой вопрос —
строка журнала search_queries (❓10, датасет тюнинга).
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import HealthRecord
from app.repositories.embeddings import log_query, search_similar
from app.repositories.members import list_by_family
from app.services.embeddings import EmbeddingError, EmbeddingProvider, build_embeddings
from app.services.indexing import build_index_text
from app.services.llm import ChatProvider, LLMError, build_chat
from app.services.profiles import initials
from app.services.ui import accent_class

logger = logging.getLogger(__name__)

# Граница AI (OVERVIEW §4) в промпте: извлекать и цитировать, не советовать.
_SYSTEM_PROMPT = """Ты — поиск по семейной медицинской карте. Тебе дают вопрос
и выдержки из записей карты, каждая помечена id. Отвечай ТОЛЬКО фактами
из выдержек: не интерпретируй результаты, не давай оценок и советов,
не додумывай того, чего в выдержках нет.

Верни ТОЛЬКО валидный JSON без пояснений и без markdown-ограждений, с ключами:
- "answer": короткий ответ на вопрос фактами из выдержек (строка) или null,
  если ответа в выдержках нет
- "source_ids": массив id записей, на факты которых опирается ответ
  (пустой массив, если answer null)

Если ответа в выдержках нет — честно верни {"answer": null, "source_ids": []}."""


class _LLMAnswer(BaseModel):
    """Контракт JSON-ответа модели; лишние ключи игнорируются pydantic'ом."""

    answer: str | None = None
    source_ids: list[int] = []


@dataclass
class SearchResult:
    """Результат вопроса — контракт шаблона search.html (T7.3).

    kind: answer | not_found | not_found_with_similar | unavailable |
    llm_unavailable. sources — источники ответа; similar — «похожие
    записи» деградаций (B4/B5). Элементы обоих списков — словари
    rec-lite: id, title, event_date, created_at, record_type, clinic,
    patient_name, initials, accent, distance.
    """

    kind: str
    answer: str | None = None
    sources: list[dict] = field(default_factory=list)
    similar: list[dict] = field(default_factory=list)


def ask(
    session: Session,
    settings: Settings,
    family_id: int,
    question: str,
    *,
    embeddings: EmbeddingProvider | None = None,
    chat: ChatProvider | None = None,
) -> SearchResult | None:
    """Один вопрос → один результат; None для пустого вопроса (B1).

    embeddings/chat пробрасываются тестами; продукт собирает провайдеров
    фабриками по конфигу.
    """
    question = question.strip()
    if not question:
        return None  # B1: провайдеры не дёргаются, журнал не пишется

    try:
        provider = embeddings or build_embeddings(settings)
        vector = provider.embed([question])[0]
    except EmbeddingError as exc:
        # B3: без вектора нет ретривала — поиск честно недоступен,
        # остальной продукт этим не задет.
        logger.warning("Поиск: эмбеддинг вопроса не удался: %s", exc)
        return SearchResult(kind="unavailable")

    candidates = search_similar(
        session, family_id, vector, settings.search_top_k, settings.search_max_distance
    )
    if not candidates:
        # B2: нечего подавать — нечего сочинять, LLM не дёргается.
        _log(session, question, candidates, None)
        return SearchResult(kind="not_found")

    lite = _present(session, family_id, candidates)
    try:
        llm = chat or build_chat(settings)
        parsed = _LLMAnswer.model_validate(
            llm.complete_json(_SYSTEM_PROMPT, _user_prompt(question, candidates))
        )
    except (LLMError, ValidationError) as exc:
        # B4: retrieval-ядро автономно — похожие записи без ответа.
        logger.warning("Поиск: генерация ответа не удалась: %s", exc)
        _log(session, question, candidates, None)
        return SearchResult(kind="llm_unavailable", similar=lite)

    # Источники ⊆ кандидатов: галлюцинированные id (чужие, выдуманные)
    # отбрасываются молча — модель не может расширить выдачу ретривала.
    valid_ids = {record.id for record, _ in candidates}
    source_ids = [i for i in parsed.source_ids if i in valid_ids]

    if parsed.answer and source_ids:
        _log(session, question, candidates, parsed.answer)
        return SearchResult(
            kind="answer",
            answer=parsed.answer,
            sources=[item for item in lite if item["id"] in source_ids],
        )

    # B5/B6: ответа нет или он без опоры — ответ без источников
    # не существует, показываем похожие записи.
    _log(session, question, candidates, None)
    return SearchResult(kind="not_found_with_similar", similar=lite)


def _user_prompt(question: str, candidates: list[tuple[HealthRecord, float]]) -> str:
    # Выдержка = индексируемый текст записи (❓5): модель видит ровно то,
    # по чему запись была найдена.
    excerpts = "\n\n".join(
        f"[id={record.id}]\n{build_index_text(record)}" for record, _ in candidates
    )
    return f"Вопрос: {question}\n\nВыдержки из записей:\n\n{excerpts}"


def _present(
    session: Session, family_id: int, candidates: list[tuple[HealthRecord, float]]
) -> list[dict]:
    """Словари rec-lite для шаблона. Акцент и монограмма — по членам семьи
    (❓3: поиск семейный, принадлежность видна в каждом источнике)."""
    members = list_by_family(session, family_id)
    accents = {member.id: accent_class(index) for index, member in enumerate(members)}
    return [
        {
            "id": record.id,
            "title": record.title,
            "event_date": record.event_date,
            "created_at": record.created_at,
            "record_type": record.record_type,
            "clinic": record.clinic,
            "patient_name": record.patient.first_name,
            "initials": initials(record.patient.first_name, record.patient.last_name),
            "accent": accents.get(record.patient_id, ""),
            "distance": distance,
        }
        for record, distance in candidates
    ]


def _log(
    session: Session,
    question: str,
    candidates: list[tuple[HealthRecord, float]],
    answer: str | None,
) -> None:
    log_query(
        session,
        question=question,
        candidates=[
            {"record_id": record.id, "distance": distance} for record, distance in candidates
        ],
        answer=answer,
    )
    session.commit()
