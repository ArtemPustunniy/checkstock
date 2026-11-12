"""Тесты идут на настоящем PostgreSQL: блокировки и CHECK-ограничения — часть решения,
SQLite их не проверит. База checkstock_test создаётся сама и пересобирается миграциями."""

import os
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://checkstock:checkstock@localhost:5442/checkstock_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # до импорта app: приложение и миграции работают с тестовой базой
os.environ["DEMO_MODE"] = "true"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from tests.helpers import Api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _recreate_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    _recreate_database(TEST_DATABASE_URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")  # схему строят миграции — заодно проверяем и их
    yield
    from app.db import engine

    engine.dispose()


@pytest.fixture(autouse=True)
def demo_data(database):
    """Каждый тест начинает с данных из задания: 3 товара, ФФ «Север» и «Юг», остатков нет."""
    from app.db import SessionLocal
    from app.seed import reset

    with SessionLocal() as session, session.begin():
        reset(session)


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture
def api(client) -> Api:
    return Api(client)


@pytest.fixture
def stocked(api) -> Api:
    """Те же данные плюс тестовый приход из задания."""
    api.load_test_receipt()
    return api
