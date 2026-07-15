"""Юниты UI-хелперов (T5.5.2): статус-бейдж и AI-поля.

Хелперы работают с транзиентными (несохранёнными) моделями — БД не нужна:
решение «какой бейдж и какие поля пометить» чисто вычислительное.
"""

from datetime import UTC, datetime

from app.models import HealthRecord
from app.services.ui import accent_class, ai_fields, badge_for, feed_badge

NOW = datetime.now(UTC)


# ---------- badge_for: вариант и подпись по ADR-012 ----------


def test_confirmed_record_is_done_badge():
    record = HealthRecord(parse_status="parsed", confirmed_at=NOW)

    assert badge_for(record) == ("done", "подтверждено")


def test_pipeline_statuses_map_to_kinds_and_labels():
    # (parse_status, ожидаемый вариант, ожидаемая подпись) — решения 15.07.2026:
    # загружено и разбирается — pending (работа идёт), parsed зовёт проверить.
    cases = [
        ("uploaded", "pending", "загружено"),
        ("parsing", "pending", "разбирается"),
        ("parsed", "review", "разобрано — проверьте"),
        ("parse_failed", "failed", "разбор не удался"),
    ]
    for status, kind, label in cases:
        record = HealthRecord(parse_status=status, confirmed_at=None)
        assert badge_for(record) == (kind, label), status


def test_none_status_has_no_badge():
    # Конвейера не существует и человек не подтверждал — показывать нечего.
    record = HealthRecord(parse_status="none", confirmed_at=None)

    assert badge_for(record) == (None, None)


# ---------- feed_badge: лента не маркирует норму ----------


def test_feed_badge_hides_confirmed():
    record = HealthRecord(parse_status="parsed", confirmed_at=NOW)

    assert feed_badge(record) == (None, None)


def test_feed_badge_shows_unconfirmed():
    record = HealthRecord(parse_status="parse_failed", confirmed_at=None)

    assert feed_badge(record) == ("failed", "разбор не удался")


# ---------- ai_fields: что подставил AI ----------


def test_ai_fields_are_filled_draft_fields_of_parsed_unconfirmed():
    record = HealthRecord(
        parse_status="parsed",
        confirmed_at=None,
        title="Общий анализ крови",
        clinic="Инвитро",
        comment="заметка человека",  # территория человека — не AI-поле
    )

    assert ai_fields(record) == {"title", "clinic"}


def test_ai_fields_empty_when_parse_failed():
    # Провал разбора полей не даёт; даже непустое поле не помечается —
    # при parse_failed его мог заполнить только человек (Э6-правки).
    record = HealthRecord(parse_status="parse_failed", confirmed_at=None, title="вручную")

    assert ai_fields(record) == set()


def test_ai_fields_empty_after_confirmation():
    record = HealthRecord(parse_status="parsed", confirmed_at=NOW, title="Анализ")

    assert ai_fields(record) == set()


def test_ai_fields_empty_for_note_without_pipeline():
    record = HealthRecord(parse_status="none", confirmed_at=None, comment="только заметка")

    assert ai_fields(record) == set()


# ---------- accent_class: персональные акценты по порядку ----------


def test_accent_classes_cycle_by_member_order():
    # Три токена кита; семьи больше трёх — по кругу.
    assert [accent_class(i) for i in range(4)] == [
        "accent-1",
        "accent-2",
        "accent-3",
        "accent-1",
    ]
