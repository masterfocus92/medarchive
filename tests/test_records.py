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


# ---------- T3.4: вёрстка экрана добавления ----------


def test_form_has_both_content_flows(client):
    """Поток К и поток Ф (flows §4а): камера и пикер — оба настоящие
    input[name=files]: без JS форма полностью работоспособна."""
    html = client.get("/records/new").text

    camera = html.split('id="camera-input"')[1].split(">")[0]
    assert 'capture="environment"' in camera
    assert 'name="files"' in camera

    picker = html.split('id="picker-input"')[1].split(">")[0]
    assert "multiple" in picker
    assert 'name="files"' in picker
    assert "application/pdf" in picker

    # JS-улучшение подключено; кнопки потоков скрыты до его загрузки.
    assert "/static/js/record-form.js" in html
    assert "Сфотографировать" in html
    assert "Выбрать из галереи или файл" in html


def test_form_design_contract(client):
    html = client.get("/records/new").text

    assert html.count("btn-primary") == 1  # одна первичная — «Сохранить»
    assert "Сохранить" in html
    assert "Отмена" in html
    assert 'class="control pencil"' in html  # заметка — карандаш
    assert html.count('name="patient_id"') >= 2  # выбор пациента — радио по членам
    assert "checked" in html  # активный профиль предвыбран (❓3)


def test_index_has_add_record_button_in_both_states(client, db_setup):
    _, ids = db_setup

    # Состояние со счётчиком (записи ребёнка создавались тестами выше).
    client.post(f"/profile/{ids['child']}")
    with_records = client.get("/").text
    assert 'href="/records/new"' in with_records

    # Пустое состояние (у оператора записей нет).
    operator_id = next(
        int(part.split('"')[0])
        for part in with_records.split('action="/profile/')[1:]
        if int(part.split('"')[0]) not in (ids["child"], ids["stranger"])
    )
    client.post(f"/profile/{operator_id}")
    empty_state = client.get("/").text
    assert 'class="empty"' in empty_state
    assert 'href="/records/new"' in empty_state


def test_toast_uses_kit_primitive(client, db_setup):
    _, ids = db_setup
    _post_record(client, ids, comment="заметка для примитива тоста")

    html = client.get("/").text

    assert 'class="toast"' in html
    assert 'class="dot"' in html


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


# ---------- T3.3: раздача файлов — только своей семье ----------


@pytest.fixture
def own_file_url(client, db_setup):
    """Создаёт запись с файлом от лица оператора, возвращает URL файла."""
    engine, ids = db_setup
    _post_record(client, ids, files=[("скан.png", PNG, "image/png")])
    with Session(engine) as session:
        record = session.scalars(select(HealthRecord).order_by(HealthRecord.id.desc())).first()
        return f"/records/{record.id}/files/1", record.id


def test_own_file_is_served(client, own_file_url):
    url, _ = own_file_url

    response = client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == PNG


def test_foreign_family_file_is_404(client, db_setup, files_dir):
    """Запись чужой семьи существует и файл лежит на диске — но для нас её нет."""
    engine, ids = db_setup

    with Session(engine) as session:
        stranger = session.get(FamilyMember, ids["stranger"])
        stranger_account = Account(member=stranger, email="stranger@test.local", password_hash="x")
        record = HealthRecord(author=stranger_account, patient=stranger, comment="чужая запись")
        session.add(record)
        session.flush()
        stored_path = f"{record.id}/01.png"
        (files_dir / str(record.id)).mkdir(parents=True, exist_ok=True)
        (files_dir / stored_path).write_bytes(PNG)
        session.add(
            RecordFile(
                record_id=record.id,
                position=1,
                stored_path=stored_path,
                original_name="чужое.png",
                mime_type="image/png",
                size_bytes=len(PNG),
            )
        )
        session.commit()
        foreign_id = record.id

    response = client.get(f"/records/{foreign_id}/files/1")

    assert response.status_code == 404


def test_nonexistent_file_is_404(client, own_file_url):
    url, record_id = own_file_url

    assert client.get(f"/records/{record_id}/files/99").status_code == 404
    assert client.get("/records/999999/files/1").status_code == 404


def test_file_requires_session(app, own_file_url):
    url, _ = own_file_url
    anonymous = TestClient(app, follow_redirects=False)

    response = anonymous.get(url)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_missing_file_on_disk_is_404_not_500(client, own_file_url, files_dir, db_setup):
    url, record_id = own_file_url
    engine, _ = db_setup
    with Session(engine) as session:
        row = session.scalar(select(RecordFile).where(RecordFile.record_id == record_id))
        (files_dir / row.stored_path).unlink()

    response = client.get(url)

    assert response.status_code == 404


def test_records_count_on_index_for_active_profile(client, db_setup):
    _, ids = db_setup
    _post_record(client, ids, comment="ещё заметка")

    # Счётчик — по активному профилю: записи создавались на дочь.
    client.post(f"/profile/{ids['child']}")
    response = client.get("/")

    # Пустое состояние не врёт: вместо него — счётчик записей.
    assert "записей:" in response.text.lower()
    assert 'class="empty"' not in response.text
