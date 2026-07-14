"""Тесты конвейера разбора (T4.3): переходы статусов по ADR-012,
журнал прогонов, недеструктивность к правкам человека."""

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.models import Account, ExtractionRun, Family, FamilyMember, HealthRecord
from app.services.extraction import ExtractionError, ExtractionResult
from app.services.pipeline import can_retry, run_extraction
from app.services.records import create_record

PIPELINE_TEST_DB = "medcard_test_pipeline"


def _real_png() -> bytes:
    # Настоящее изображение: конвейер открывает файлы через PIL,
    # поддельных magic bytes ему недостаточно.
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _real_png()

RESULT = ExtractionResult(
    title="Общий анализ крови",
    clinic="Клиника",
    doctor="Петрова",
    record_type="анализ",
    content="Гемоглобин 132",
    suggested_patient_id=None,
)


class FakeExtractor:
    provider = "fake"
    model = "fake-model"

    def __init__(self, result=RESULT):
        self.result = result

    def extract(self, files, family):
        return self.result, {"fake": True, "pages": len(files)}


class FailingExtractor:
    provider = "fake"
    model = "fake-model"

    def extract(self, files, family):
        raise ExtractionError("модель упала")


@pytest.fixture(scope="module")
def db(admin_conn, tmp_path_factory):
    recreate_db(admin_conn, PIPELINE_TEST_DB)
    command.upgrade(alembic_config(db_url(PIPELINE_TEST_DB)), "head")
    engine = create_engine(db_url(PIPELINE_TEST_DB))
    factory = sessionmaker(bind=engine)
    files_dir = tmp_path_factory.mktemp("pipeline-files")

    with factory() as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=datetime(1990, 1, 1).date(),
            sex="male",
        )
        account = Account(member=operator, email="op@test.local", password_hash="x")
        session.add(account)
        session.commit()
        account_id = account.id

    yield {"factory": factory, "files_dir": files_dir, "account_id": account_id}
    engine.dispose()
    drop_db(admin_conn, PIPELINE_TEST_DB)


def _new_record(db, comment="", files=(("скан.png", PNG),)) -> int:
    with db["factory"]() as session:
        account = session.get(Account, db["account_id"])
        record = create_record(
            session,
            files_dir=Path(db["files_dir"]),
            author=account,
            patient_id=account.family_member_id,
            files=list(files),
            comment=comment,
        )
        return record.id


def _run(db, record_id, extractor):
    run_extraction(
        record_id,
        session_factory=db["factory"],
        files_dir=Path(db["files_dir"]),
        extractor=extractor,
    )


def _record(db, record_id) -> HealthRecord:
    with db["factory"]() as session:
        record = session.get(HealthRecord, record_id)
        session.refresh(record)
        _ = record.files
        return record


def test_success_fills_empty_fields_and_marks_parsed(db):
    record_id = _new_record(db)

    _run(db, record_id, FakeExtractor())

    record = _record(db, record_id)
    assert record.parse_status == "parsed"
    assert record.title == "Общий анализ крови"
    assert record.confirmed_at is None  # подтверждает человек, не конвейер
    with db["factory"]() as session:
        run = session.scalar(select(ExtractionRun).where(ExtractionRun.record_id == record_id))
        assert run.status == "ok"
        assert run.provider == "fake"
        assert run.raw_response == {"fake": True, "pages": 1}
        assert run.finished_at is not None


def test_human_edits_are_never_overwritten(db):
    record_id = _new_record(db)
    with db["factory"]() as session:
        record = session.get(HealthRecord, record_id)
        record.title = "Моё название"  # человек уже вписал
        session.commit()

    _run(db, record_id, FakeExtractor())

    record = _record(db, record_id)
    assert record.title == "Моё название"  # не затёрто
    assert record.clinic == "Клиника"  # пустое — заполнено


def test_failure_marks_parse_failed_and_keeps_record(db):
    record_id = _new_record(db)

    _run(db, record_id, FailingExtractor())

    record = _record(db, record_id)
    assert record.parse_status == "parse_failed"
    assert record.title is None
    with db["factory"]() as session:
        run = session.scalar(select(ExtractionRun).where(ExtractionRun.record_id == record_id))
        assert run.status == "error"
        assert "модель упала" in run.error


def test_retry_after_failure_appends_second_run(db):
    record_id = _new_record(db)
    _run(db, record_id, FailingExtractor())

    _run(db, record_id, FakeExtractor())

    record = _record(db, record_id)
    assert record.parse_status == "parsed"
    with db["factory"]() as session:
        runs = session.scalars(
            select(ExtractionRun).where(ExtractionRun.record_id == record_id)
        ).all()
        assert [r.status for r in runs] == ["error", "ok"]


def test_parsed_record_is_not_reprocessed(db):
    record_id = _new_record(db)
    _run(db, record_id, FakeExtractor())

    # Повторный запуск не должен ни упасть, ни создать новый прогон.
    _run(db, record_id, FakeExtractor())

    with db["factory"]() as session:
        runs = session.scalars(
            select(ExtractionRun).where(ExtractionRun.record_id == record_id)
        ).all()
        assert len(runs) == 1


def test_can_retry_rules(db):
    now = datetime.now(UTC)
    record_id = _new_record(db)
    with db["factory"]() as session:
        record = session.get(HealthRecord, record_id)

        # свежая uploaded — конвейер ещё идёт, ретрай не предлагаем
        assert can_retry(record, now=now) is False
        # зависшая uploaded (>10 минут) — предлагаем
        assert can_retry(record, now=now + timedelta(minutes=11)) is True

        record.parse_status = "parse_failed"
        assert can_retry(record, now=now) is True

        record.parse_status = "parsed"
        assert can_retry(record, now=now + timedelta(days=1)) is False

        record.parse_status = "none"
        assert can_retry(record, now=now + timedelta(days=1)) is False
