"""Тесты создания записи (T3.2-BE).

Ключевые свойства: инвариант «файл ИЛИ комментарий» живёт в сервисе;
файлы на диске и строки в БД появляются вместе или никак (компенсация);
типы файлов проверяются по сигнатуре, а не по имени.
"""

from datetime import date
from pathlib import Path

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Account, Family, FamilyMember, HealthRecord, RecordFile
from app.services.records import sniff_file
from app.services.security import hash_password

RECORDS_TEST_DB = "medcard_test_records"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"

# Минимальные валидные сигнатуры
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
EXE = b"MZ\x90\x00" + b"\x00" * 64


# ---------- Юниты: сниффер сигнатур ----------


@pytest.mark.parametrize(
    ("content", "expected_ext"),
    [(PNG, "png"), (JPEG, "jpg"), (PDF, "pdf"), (WEBP, "webp"), (HEIC, "heic")],
)
def test_sniff_recognizes_allowed_types(content, expected_ext):
    sniffed = sniff_file(content)

    assert sniffed is not None
    assert sniffed[0] == expected_ext


def test_sniff_rejects_unknown_content():
    assert sniff_file(EXE) is None
    assert sniff_file(b"just text") is None


# ---------- Интеграция ----------


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    recreate_db(admin_conn, RECORDS_TEST_DB)
    command.upgrade(alembic_config(db_url(RECORDS_TEST_DB)), "head")
    engine = create_engine(db_url(RECORDS_TEST_DB))

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        child = FamilyMember(
            family=family,
            last_name="Тестова",
            first_name="Дочь",
            birth_date=date(2024, 1, 1),
            sex="female",
        )
        stranger = FamilyMember(
            family=Family(),
            last_name="Чужаков",
            first_name="Пётр",
            birth_date=date(1985, 1, 1),
            sex="male",
        )
        session.add_all(
            [
                child,
                stranger,
                Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD)),
            ]
        )
        session.commit()
        ids = {"child": child.id, "stranger": stranger.id}

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, RECORDS_TEST_DB)


@pytest.fixture(scope="module")
def files_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("files")


@pytest.fixture(scope="module")
def app(db_setup, files_dir):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(RECORDS_TEST_DB),
        files_dir=files_dir,
        secret_key="test-secret-key-only-for-tests-0123456789",
    )
    application = create_app(settings)

    test_sessionmaker = sessionmaker(bind=engine)

    def override_session():
        with test_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app):
    test_client = TestClient(app, follow_redirects=False)
    test_client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    return test_client


@pytest.fixture
def counts(db_setup):
    engine, _ = db_setup

    def _counts() -> tuple[int, int]:
        with Session(engine) as session:
            return (
                session.scalar(select(func.count()).select_from(HealthRecord)),
                session.scalar(select(func.count()).select_from(RecordFile)),
            )

    return _counts


def _post_record(client, ids, files=(), comment="", patient_key="child"):
    return client.post(
        "/records",
        data={"patient_id": ids[patient_key], "comment": comment},
        files=[("files", (name, content, mime)) for name, content, mime in files],
    )


def test_new_record_form_renders(client):
    response = client.get("/records/new")

    assert response.status_code == 200
    assert 'name="files"' in response.text
    assert 'name="comment"' in response.text
    assert 'name="patient_id"' in response.text


def test_photo_with_note_creates_uploaded_record(client, db_setup, files_dir, counts):
    engine, ids = db_setup

    response = _post_record(
        client, ids, files=[("скан.png", PNG, "image/png")], comment="первый приём"
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    with Session(engine) as session:
        record = session.scalars(select(HealthRecord).order_by(HealthRecord.id.desc())).first()
        assert record.parse_status == "uploaded"
        assert record.comment == "первый приём"
        assert record.patient_id == ids["child"]
        assert record.created_at is not None
        files = record.files
        assert len(files) == 1
        assert files[0].position == 1
        assert files[0].original_name == "скан.png"
        assert files[0].mime_type == "image/png"
        # Файл лежит на диске по относительному пути из БД.
        stored = files_dir / files[0].stored_path
        assert stored.read_bytes() == PNG


def test_multiple_files_keep_order(client, db_setup):
    engine, ids = db_setup

    response = _post_record(
        client,
        ids,
        files=[("стр1.png", PNG, "image/png"), ("стр2.jpg", JPEG, "image/jpeg")],
    )

    assert response.status_code == 303
    with Session(engine) as session:
        record = session.scalars(select(HealthRecord).order_by(HealthRecord.id.desc())).first()
        names = [(f.position, f.original_name) for f in record.files]
        assert names == [(1, "стр1.png"), (2, "стр2.jpg")]


def test_note_only_record_is_confirmed(client, db_setup):
    engine, ids = db_setup

    response = _post_record(client, ids, comment="рост 68 см, вес 7.2 кг")

    assert response.status_code == 303
    with Session(engine) as session:
        record = session.scalars(select(HealthRecord).order_by(HealthRecord.id.desc())).first()
        # Решение ❓1 потока: разбирать нечего — сразу подтверждена.
        assert record.parse_status == "confirmed"
        assert record.files == []


def test_empty_record_rejected(client, db_setup, counts):
    _, ids = db_setup
    before = counts()

    response = _post_record(client, ids)

    assert response.status_code == 200  # re-render формы
    assert "Добавьте фото или заметку" in response.text
    assert counts() == before


def test_foreign_patient_is_404(client, db_setup, counts):
    _, ids = db_setup
    before = counts()

    response = _post_record(
        client, ids, files=[("скан.png", PNG, "image/png")], patient_key="stranger"
    )

    assert response.status_code == 404
    assert counts() == before


def test_renamed_exe_rejected_by_signature(client, db_setup, counts):
    _, ids = db_setup
    before = counts()

    # Имя и mime врут — сигнатура нет.
    response = _post_record(client, ids, files=[("скан.jpg", EXE, "image/jpeg")])

    assert response.status_code == 200
    assert "не поддерживается" in response.text
    assert counts() == before


def test_oversized_file_rejected(client, db_setup, counts):
    _, ids = db_setup
    before = counts()
    huge = PNG + b"\x00" * (20 * 1024 * 1024)

    response = _post_record(client, ids, files=[("скан.png", huge, "image/png")])

    assert response.status_code == 200
    assert "20 МБ" in response.text
    assert counts() == before


def test_failed_disk_write_leaves_nothing(client, db_setup, files_dir, counts, monkeypatch):
    """Ветка B6: сбой посреди записи второго файла — в БД пусто,
    на диске нет осиротевших файлов первой страницы."""
    _, ids = db_setup
    before = counts()
    dirs_before = {p.name for p in files_dir.iterdir()}

    original_write = Path.write_bytes
    calls = {"n": 0}

    def flaky_write(self, data):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("диск кончился")
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write)

    response = _post_record(
        client,
        ids,
        files=[("стр1.png", PNG, "image/png"), ("стр2.jpg", JPEG, "image/jpeg")],
    )

    assert response.status_code == 200
    assert "Не получилось сохранить" in response.text
    assert counts() == before
    assert {p.name for p in files_dir.iterdir()} == dirs_before


def test_flash_toast_shown_once(client, db_setup):
    _, ids = db_setup
    _post_record(client, ids, comment="заметка для тоста")

    first = client.get("/")
    assert "Запись сохранена" in first.text

    second = client.get("/")
    assert "Запись сохранена" not in second.text


def test_records_count_on_index_for_active_profile(client, db_setup):
    _, ids = db_setup
    _post_record(client, ids, comment="ещё заметка")

    # Счётчик — по активному профилю: записи создавались на дочь.
    client.post(f"/profile/{ids['child']}")
    response = client.get("/")

    # Пустое состояние не врёт: вместо него — счётчик записей.
    assert "записей:" in response.text.lower()
    assert 'class="empty"' not in response.text
