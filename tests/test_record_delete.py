"""Тесты удаления записи (T6.1): soft delete, каскадная дисциплина,
страница подтверждения, неразличимость удалённого и чужого."""

import io
from datetime import date

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
from app.models import Account, Family, FamilyMember, HealthRecord, RecordFile
from app.services.security import hash_password

DELETE_TEST_DB = "medcard_test_delete"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    recreate_db(admin_conn, DELETE_TEST_DB)
    command.upgrade(alembic_config(db_url(DELETE_TEST_DB)), "head")
    engine = create_engine(db_url(DELETE_TEST_DB))

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
    drop_db(admin_conn, DELETE_TEST_DB)


@pytest.fixture(scope="module")
def app(db_setup, tmp_path_factory):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(DELETE_TEST_DB),
        files_dir=tmp_path_factory.mktemp("delete-files"),
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


def _create(client, db_setup, comment="", n_files=0, title=None) -> int:
    _, ids = db_setup
    files = [("files", (f"стр{i}.png", PNG, "image/png")) for i in range(n_files)]
    client.post(
        "/records",
        data={"patient_id": ids["member"], "comment": comment},
        files=files or None,
    )
    engine, _ = db_setup
    with Session(engine) as session:
        record_id = session.scalar(select(func.max(HealthRecord.id)))
        if title is not None:
            session.get(HealthRecord, record_id).title = title
            session.commit()
        return record_id


# ---------- Страница подтверждения ----------


def test_delete_page_renders_record_identity(client, db_setup):
    record_id = _create(client, db_setup, comment="заметка", title="Прививка АКДС")

    html = client.get(f"/records/{record_id}/delete").text

    assert "Удалить запись?" in html
    assert "Прививка АКДС" in html  # человек видит, ЧТО удаляет
    assert f'action="/records/{record_id}/delete"' in html  # POST-форма


def test_delete_page_design_contract(client, db_setup):
    """Страница-«лист» (❓1/❓6): тексты, красная кнопка, ноль primary."""
    record_id = _create(client, db_setup, comment="дизайн")

    html = client.get(f"/records/{record_id}/delete").text

    assert 'class="sheet-modal"' in html
    assert "Запись скроется из ленты и поиска." in html
    assert "Удалить запись" in html and "btn-danger" in html
    assert f'href="/records/{record_id}"' in html  # «Отмена» — назад к записи
    assert html.count("btn-primary") == 0


# ---------- Сам акт удаления и каскадная дисциплина ----------


def test_delete_hides_record_everywhere(client, db_setup):
    record_id = _create(client, db_setup, comment="удалим", n_files=1)

    response = client.post(f"/records/{record_id}/delete")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Лента (обе сортировки) больше не знает запись:
    assert f'href="/records/{record_id}"' not in client.get("/").text
    assert f'href="/records/{record_id}"' not in client.get("/?sort=event").text
    # Все URL записи — 404, неразличимо с несуществующей:
    assert client.get(f"/records/{record_id}").status_code == 404
    assert client.get(f"/records/{record_id}/edit").status_code == 404
    assert client.get(f"/records/{record_id}/delete").status_code == 404
    assert client.get(f"/records/{record_id}/files/1").status_code == 404


def test_delete_is_soft_and_attributed(client, db_setup, app):
    """Спека §5: физически не стирается; кто удалил — зафиксирован."""
    record_id = _create(client, db_setup, comment="улика", n_files=1)

    client.post(f"/records/{record_id}/delete")

    engine, _ = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)  # строка на месте
        assert record.deleted_at is not None
        assert record.deleted_by_account_id is not None
        file_row = session.scalar(select(RecordFile).where(RecordFile.record_id == record_id))
        assert file_row is not None  # строки файлов на месте
        assert (app.state.settings.files_dir / file_row.stored_path).is_file()  # и сам файл


def test_delete_shows_toast(client, db_setup):
    record_id = _create(client, db_setup, comment="с тостом")

    client.post(f"/records/{record_id}/delete")

    assert "Запись удалена" in client.get("/").text


def test_unconfirmed_record_is_deletable(client, db_setup):
    """B2: мусорный скан удаляется прямо с проверки — до подтверждения."""
    record_id = _create(client, db_setup, n_files=1)  # с файлом → не подтверждена

    assert client.get(f"/records/{record_id}/delete").status_code == 200
    assert client.post(f"/records/{record_id}/delete").status_code == 303
    assert client.get(f"/records/{record_id}").status_code == 404


# ---------- Негативные ----------


def test_foreign_record_delete_is_404(client, db_setup):
    _, ids = db_setup

    assert client.get(f"/records/{ids['foreign_record']}/delete").status_code == 404
    assert client.post(f"/records/{ids['foreign_record']}/delete").status_code == 404


def test_repeated_delete_is_404(client, db_setup):
    """B3: двойной сабмит/устаревшая вкладка — повтор неотличим от
    несуществующей записи."""
    record_id = _create(client, db_setup, comment="дважды")

    assert client.post(f"/records/{record_id}/delete").status_code == 303
    assert client.post(f"/records/{record_id}/delete").status_code == 404
