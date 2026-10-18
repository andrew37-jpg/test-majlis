"""Destructive fixture setup is allowed ONLY on an explicitly supplied test DB."""

import os
from urllib.parse import urlsplit

import psycopg
import pytest
from pydantic import SecretStr

from majlis.config import Settings
from majlis.database import Database


def validate_test_urls(runtime: str, migration: str) -> tuple[str, str]:
    if not runtime or not migration:
        raise RuntimeError(
            "Задайте TEST_DATABASE_URL и TEST_MIGRATION_DATABASE_URL для отдельной базы."
        )
    a, b = urlsplit(runtime), urlsplit(migration)
    if not a.path.endswith("_test") or (a.hostname, a.port, a.path) != (b.hostname, b.port, b.path):
        raise RuntimeError("Обе роли должны указывать на ОДНУ отдельную *_test базу.")
    if not a.username or not b.username or a.username == b.username:
        raise RuntimeError("Тестовые runtime и мигратор должны быть разными ролями.")
    return runtime, migration


@pytest.fixture(scope="session")
def test_database_urls():
    return validate_test_urls(
        os.getenv("TEST_DATABASE_URL", ""), os.getenv("TEST_MIGRATION_DATABASE_URL", "")
    )


@pytest.fixture
def cfg(monkeypatch, test_database_urls):
    runtime, migration = test_database_urls
    value = Settings()
    value.environment = "test"
    value.public_requests_per_minute = 240
    value.database_url = SecretStr(runtime)
    value.migration_database_url = SecretStr(migration)
    value.bot_token = SecretStr("123456789:" + ("a" * 40))
    value.manager_ids = "9001"
    value.payment_hosts = "payments.example"
    value.integration_secret = SecretStr("b" * 40)
    value.instance_id = "test-instance"
    value.health_token = SecretStr("h" * 40)
    for module in ("config", "database", "api", "bot", "http_boundary"):
        monkeypatch.setattr("majlis." + module + ".settings", lambda: value)
    return value


@pytest.fixture
def db(cfg):
    database = Database()
    database.open()
    try:
        database.migrate()
        with psycopg.connect(cfg.migration_database_url.get_secret_value()) as conn:
            conn.execute(
                "TRUNCATE audit_log,outbox,events,orders,integration_events,bot_state,rate_limits,users RESTART IDENTITY CASCADE"
            )
        yield database
    finally:
        database.close()


@pytest.fixture
def client(db, cfg):
    from fastapi.testclient import TestClient

    from majlis.api import app

    with TestClient(app) as client:
        yield client
