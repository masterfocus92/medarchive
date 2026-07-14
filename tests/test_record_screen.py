"""Тесты экрана записи/проверки (T4.4): доступ, черновик, подтверждение,
ретрай, статус-бейдж неподтверждённой записи в ленте (Э5)."""

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
from app.models import Account, ExtractionRun, Family, FamilyMember, HealthRecord
from app.services.security import hash_password

SCREEN_TEST_DB = "medcard_test_screen"

EMAIL = "operator@test.local"
PASSWORD = "correct-password-1"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


@pytest.fixture(scope="module")
def db_setup(admin_conn):
    recreate_db(admin_conn, SCREEN_TEST_DB)
    command.upgrade(alembic_config(db_url(SCREEN_TEST_DB)), "head")
    engine = create_engine(db_url(SCREEN_TEST_DB))

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
                child,
                foreign_record,
                Account(member=operator, email=EMAIL, password_hash=hash_password(PASSWORD)),
            ]
        )
        session.commit()
        ids = {"child": child.id, "foreign_record": foreign_record.id}

    yield engine, ids
    engine.dispose()
    drop_db(admin_conn, SCREEN_TEST_DB)


@pytest.fixture(scope="module")
def app(db_setup, tmp_path_factory):
    engine, _ = db_setup
    settings = Settings(
        _env_file=None,
        database_url=db_url(SCREEN_TEST_DB),
        files_dir=tmp_path_factory.mktemp("screen-files"),
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


def _create_note(client, db_setup, comment="заметка") -> int:
    _, ids = db_setup
    client.post("/records", data={"patient_id": ids["child"], "comment": comment})
    engine, _ = db_setup
    with Session(engine) as session:
        return session.scalar(select(func.max(HealthRecord.id)))


def _create_with_file(client, db_setup) -> int:
    _, ids = db_setup
    client.post(
        "/records",
        data={"patient_id": ids["child"], "comment": ""},
        files=[("files", ("скан.png", PNG, "image/png"))],
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


def test_own_record_screen_renders(client, db_setup):
    """Заметка подтверждена при создании — с Э5 её URL открывает карточку
    просмотра (ветвление t₂ потока), а не экран проверки."""
    record_id = _create_note(client, db_setup, comment="рост 68 см")

    response = client.get(f"/records/{record_id}")

    assert response.status_code == 200
    assert "рост 68 см" in response.text
    assert "подтверждено" in response.text
    # Экран проверки не показан: карточка read-only (❓8), формы нет.
    assert 'name="patient_id"' not in response.text


def test_foreign_and_missing_records_are_404(client, db_setup):
    _, ids = db_setup

    assert client.get(f"/records/{ids['foreign_record']}").status_code == 404
    assert client.get("/records/999999").status_code == 404


def test_draft_fields_are_prefilled(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    _set(
        db_setup,
        record_id,
        parse_status="parsed",
        title="Общий анализ крови",
        clinic="Клиника Здоровье",
    )

    html = client.get(f"/records/{record_id}").text

    assert 'value="Общий анализ крови"' in html
    assert 'value="Клиника Здоровье"' in html
    assert "разобрано" in html


def test_active_pipeline_page_refreshes_itself(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    _set(db_setup, record_id, parse_status="parsing")

    html = client.get(f"/records/{record_id}").text

    assert 'http-equiv="refresh"' in html
    assert "разбирается" in html


def test_terminal_page_does_not_refresh(client, db_setup):
    record_id = _create_note(client, db_setup)

    html = client.get(f"/records/{record_id}").text

    assert 'http-equiv="refresh"' not in html


def test_confirm_saves_fields_and_sets_confirmed_once(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    _set(db_setup, record_id, parse_status="parsed", title="Черновик AI")
    _, ids = db_setup

    response = client.post(
        f"/records/{record_id}/confirm",
        data={
            "title": "Анализ крови (поправлено)",
            "event_date": "2026-03-12",
            "clinic": "Клиника",
            "doctor": "Петрова",
            "record_type": "анализ",
            "content": "Гемоглобин 132",
            "comment": "всё ок",
            "patient_id": ids["child"],
        },
    )

    assert response.status_code == 303
    engine, _ = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        assert record.title == "Анализ крови (поправлено)"
        assert record.event_date == date(2026, 3, 12)
        assert record.confirmed_at is not None
        first_confirmed = record.confirmed_at

    # Повторное подтверждение правит поля, но момент подтверждения не сдвигает.
    client.post(
        f"/records/{record_id}/confirm",
        data={"title": "Ещё раз поправлено", "patient_id": ids["child"]},
    )
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        assert record.title == "Ещё раз поправлено"
        assert record.confirmed_at == first_confirmed


def test_confirm_rejects_invalid_date(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    _, ids = db_setup

    response = client.post(
        f"/records/{record_id}/confirm",
        data={"event_date": "не дата", "patient_id": ids["child"]},
    )

    assert response.status_code == 200
    assert "дат" in response.text.lower()  # текст ошибки говорит про дату


def test_reparse_allowed_from_failed_and_appends_run(client, db_setup):
    record_id = _create_with_file(client, db_setup)  # фон уже отработал → parse_failed
    engine, _ = db_setup
    with Session(engine) as session:
        runs_before = session.scalar(
            select(func.count())
            .select_from(ExtractionRun)
            .where(ExtractionRun.record_id == record_id)
        )

    response = client.post(f"/records/{record_id}/reparse")

    assert response.status_code == 303
    with Session(engine) as session:
        runs_after = session.scalar(
            select(func.count())
            .select_from(ExtractionRun)
            .where(ExtractionRun.record_id == record_id)
        )
    assert runs_after == runs_before + 1


def test_reparse_noop_when_not_allowed(client, db_setup):
    record_id = _create_note(client, db_setup)  # none/confirmed — ретраить нечего
    engine, _ = db_setup

    response = client.post(f"/records/{record_id}/reparse")

    assert response.status_code == 303
    with Session(engine) as session:
        runs = session.scalar(
            select(func.count())
            .select_from(ExtractionRun)
            .where(ExtractionRun.record_id == record_id)
        )
    assert runs == 0


def _feed_item(html: str, record_id: int) -> str:
    """Вырезать из ленты элемент конкретной записи: бейджи соседних записей
    (модульная БД накапливает их) не должны влиять на проверку."""
    start = html.index(f'href="/records/{record_id}"')
    return html[start : html.index("</a>", start)]


def test_unconfirmed_record_wears_badge_in_feed(client, db_setup):
    """Блок «Ждут проверки» упразднён (поток просмотра, ❓2): его роль
    выполняет статус-бейдж на элементе ленты."""
    record_id = _create_with_file(client, db_setup)
    _set(db_setup, record_id, parse_status="parsed", title="Ждущая запись")
    _, ids = db_setup

    # Запись создана на ребёнка — лента показывает только активный профиль.
    client.post(f"/profile/{ids['child']}")
    index = client.get("/").text
    assert "Ждут проверки" not in index
    assert "разобрано" in _feed_item(index, record_id)

    client.post(f"/records/{record_id}/confirm", data={"patient_id": ids["child"]})

    # Подтверждённая запись остаётся в ленте, но бейдж больше не носит.
    index = client.get("/").text
    assert "разобрано" not in _feed_item(index, record_id)


# ---------- T4.5: дизайн-контракт экрана проверки ----------


def test_screen_design_contract(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    _set(db_setup, record_id, parse_status="parsed", record_type="анализ")

    html = client.get(f"/records/{record_id}").text

    assert html.count("btn-primary") == 1  # одна первичная — «Сохранить»
    assert 'class="control data"' in html  # дата — моноширинный бланк (кит §3)
    assert 'class="chip"' in html  # тип записи — чип (первое использование)
    assert 'class="status-line"' in html  # статус — текстом, всегда виден


def test_suggested_patient_is_highlighted(client, db_setup):
    record_id = _create_with_file(client, db_setup)
    engine, ids = db_setup
    with Session(engine) as session:
        record = session.get(HealthRecord, record_id)
        operator_id = record.author.family_member_id
        record.patient_id = operator_id  # выбран оператор
        record.suggested_patient_id = ids["child"]  # AI считает — дочь
        record.parse_status = "parsed"
        session.commit()

    html = client.get(f"/records/{record_id}").text

    assert "AI считает" in html
    assert 'class="who suggested"' in html  # монограмма предложения подсвечена


def test_confirmed_at_set_by_note_creation_shows_no_retry(client, db_setup):
    record_id = _create_note(client, db_setup)

    html = client.get(f"/records/{record_id}").text

    assert "Разобрать ещё раз" not in html
