"""Тесты карточки записи (T5.3/T5.4): ветвление подтверждена/нет,
страницы вертикальным потоком, доступность файлов (ветка B7)."""

import io
import logging
from datetime import UTC, date, datetime

import pytest
from conftest import alembic_config, db_url, drop_db, recreate_db
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Account, Family, FamilyMember, HealthRecord
from app.services.security import hash_password

VIEW_TEST_DB = "medcard_test_view"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    recreate_db(admin_conn, VIEW_TEST_DB)
    command.upgrade(alembic_config(db_url(VIEW_TEST_DB)), "head")
    engine = create_engine(db_url(VIEW_TEST_DB))

    with Session(engine) as session:
        family = Family()
        operator = FamilyMember(
            family=family,
            last_name="Тестов",
            first_name="Оператор",
            birth_date=date(1990, 1, 1),
            sex="male",
        )
        stranger_family = Family()
        stranger = FamilyMember(
            family=stranger_family,
            last_name="Чужаков",
            first_name="Пётр",
            birth_date=date(1985, 1, 1),
            sex="male",
        )
        stranger_account = Account(member=stranger, email="s@test.local", password_hash="x")
        foreign_record = HealthRecord(author=stranger_account, patient=stranger, comment="чужое")
        session.add_all(
            [
                foreign_record,
                Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD)),
            ]
        )
        session.commit()
        ids = {"member": operator.id, "foreign_record": foreign_record.id}

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, VIEW_TEST_DB)


@pytest.fixture(scope="module")
def app(db_setup, tmp_path_factory):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(VIEW_TEST_DB),
        files_dir=tmp_path_factory.mktemp("view-files"),
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


def _create(client, db_setup, comment="", n_files=0) -> int:
    """Запись через реальный сервис (файлы ложатся на диск как в бою)."""
    _, ids = db_setup
    files = [("files", (f"стр{i}.png", PNG, "image/png")) for i in range(n_files)]
    client.post(
        "/records",
        data={"patient_id": ids["member"], "comment": comment},
        files=files or None,
    )
    engine, _ = db_setup
    with Session(engine) as session:
        return session.scalar(select(func.max(HealthRecord.id)))


def _set(db_setup, record_id, **values):
    engine, _ = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        for key, value in values.items():
            setattr(record, key, value)
        session.commit()


def _confirm(db_setup, record_id):
    _set(db_setup, record_id, confirmed_at=datetime.now(UTC))


# ---------- T5.3: ветвление подтверждена / не подтверждена ----------


def test_confirmed_record_opens_card(client, db_setup):
    # Заметка без файлов подтверждается при создании (ADR-012).
    record_id = _create(client, db_setup, comment="рост 68 см")

    html = client.get(f"/records/{record_id}").text

    # Карточка просмотра, не экран проверки: формы подтверждения нет.
    assert f'action="/records/{record_id}/confirm"' not in html
    assert "подтверждено" in html  # статус — текстом (DESIGN.MD §5)
    assert "рост 68 см" in html


def test_unconfirmed_record_opens_review_screen(client, db_setup):
    record_id = _create(client, db_setup, n_files=1)
    _set(db_setup, record_id, parse_status="parsed", confirmed_at=None)

    html = client.get(f"/records/{record_id}").text

    assert f'action="/records/{record_id}/confirm"' in html


def test_foreign_and_deleted_records_are_404(client, db_setup):
    _, ids = db_setup
    deleted_id = _create(client, db_setup, comment="удалим")
    _set(db_setup, deleted_id, deleted_at=datetime.now(UTC))

    assert client.get(f"/records/{ids['foreign_record']}").status_code == 404
    assert client.get(f"/records/{deleted_id}").status_code == 404


# ---------- T5.3/T5.4: страницы ----------


def test_card_shows_pages_in_order(client, db_setup):
    record_id = _create(client, db_setup, n_files=2)
    _confirm(db_setup, record_id)

    html = client.get(f"/records/{record_id}").text

    first = html.index(f"/records/{record_id}/files/1")
    second = html.index(f"/records/{record_id}/files/2")
    assert first < second
    assert "стр. 1 из 2" in html  # подпись-счётчик (ветка B3)


def test_missing_file_shows_plate_not_500(client, db_setup, app, caplog):
    """Ветка B7: файл пропал с диска — плашка и warning-лог, не битая
    картинка и не 500."""
    record_id = _create(client, db_setup, n_files=1)
    _confirm(db_setup, record_id)

    engine, _ = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        (app.state.settings.files_dir / record.files[0].stored_path).unlink()

    with caplog.at_level(logging.WARNING):
        response = client.get(f"/records/{record_id}")

    assert response.status_code == 200
    assert "файл недоступен" in response.text
    assert any("отсутствует на диске" in message for message in caplog.messages)


def test_pdf_page_is_plate_not_img(client, db_setup):
    """Ветка B4/❓6: PDF не рендерится превью — плашка «открыть»."""
    record_id = _create(client, db_setup, n_files=1)
    _confirm(db_setup, record_id)

    engine, _ = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        record.files[0].mime_type = "application/pdf"
        session.commit()

    html = client.get(f"/records/{record_id}").text

    assert "PDF" in html
    # Превью-<img> для PDF не существует — только ссылка-плашка.
    assert f'<img src="/records/{record_id}/files/1"' not in html


# ---------- T5.4: вёрстка карточки ----------


def test_card_full_composition(client, db_setup):
    """Композиция record: факты — чернила (rec-content), заметка — карандаш
    (pencil-note) — различимы классами кита без подписей."""
    record_id = _create(client, db_setup, n_files=1)
    _set(
        db_setup,
        record_id,
        confirmed_at=datetime.now(UTC),
        title="Общий анализ крови",
        record_type="анализ",
        event_date=date(2026, 6, 10),
        clinic="Инвитро",
        doctor="Петрова",
        content="Гемоглобин в норме",
        comment="пересдать через месяц",
    )

    html = client.get(f"/records/{record_id}").text

    assert 'class="rec-title"' in html and "Общий анализ крови" in html
    assert 'class="chip"' in html  # тип — чип
    assert "10.06.2026" in html  # дата события, mono в rec-date
    assert "Инвитро" in html and "Петрова" in html
    assert 'class="rec-content"' in html and "Гемоглобин в норме" in html
    assert 'class="pencil-note"' in html and "пересдать через месяц" in html
    # Кит v2: маркер «подтверждено» — бейдж done в шапке (rec-datewrap),
    # статус-строка внизу упразднена.
    assert 'class="rec-datewrap"' in html
    assert 'class="badge done"' in html and "подтверждено" in html
    assert 'class="status-line"' not in html
    assert html.count("btn-primary") == 0  # карточка read-only (❓8)


def test_note_card_has_no_pages_block(client, db_setup):
    """Ветка B5: запись без файлов — карточка без блока страниц."""
    record_id = _create(client, db_setup, comment="просто заметка")

    html = client.get(f"/records/{record_id}").text

    assert 'class="page-flow"' not in html


def test_pages_live_inside_record_body(client, db_setup):
    """Кит v2: страницы — вертикальный поток внутри rec-body, карточка —
    единый «лист» (утверждённый поток просмотра)."""
    record_id = _create(client, db_setup, n_files=1)
    _confirm(db_setup, record_id)

    html = client.get(f"/records/{record_id}").text

    body_start = html.index('class="rec-body"')
    flow_start = html.index('class="page-flow"')
    article_end = html.index("</article>")
    assert body_start < flow_start < article_end


def test_photo_page_links_and_download(client, db_setup):
    """❓4/❓5: тап по фото — оригинал в новой вкладке; скачивание —
    атрибутом download без отдельного endpoint."""
    record_id = _create(client, db_setup, n_files=1)
    _confirm(db_setup, record_id)

    html = client.get(f"/records/{record_id}").text

    url = f"/records/{record_id}/files/1"
    assert f'<img src="{url}"' in html and 'loading="lazy"' in html
    assert f'href="{url}" target="_blank"' in html  # оригинал — новая вкладка
    assert f'href="{url}" download' in html  # скачать
    assert 'href="/"' in html  # назад в ленту
