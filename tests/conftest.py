"""Shared fixtures. The server tests need a PostgreSQL the tests may create databases on:

    CF_TEST_DATABASE_URL=postgresql://cardforge:cardforge@localhost:5432/postgres python -m pytest

Each test session creates its own temporary database there (and drops it at the end).
Without CF_TEST_DATABASE_URL those tests are skipped; everything else runs as before.
"""

import os
import uuid

import pytest


@pytest.fixture(scope="session")
def pg_url():
    base = os.environ.get("CF_TEST_DATABASE_URL")
    if not base:
        pytest.skip("set CF_TEST_DATABASE_URL to a PostgreSQL URL (role with CREATEDB) to run the server tests")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import make_conninfo

    try:
        admin = psycopg.connect(base, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable at CF_TEST_DATABASE_URL: {exc}")
    name = f"cardforge_test_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(base, dbname=name)
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


MACHINE_TOKEN = "test-machine-token"


@pytest.fixture
def server(pg_url):
    """The CardForge server on an empty database (TestClient)."""
    import psycopg
    from fastapi.testclient import TestClient

    from cardforge_server.api import create_app
    from cardforge_server.config import ServerSettings

    settings = ServerSettings(database_url=pg_url, token_secret="test-secret-" + "x" * 32,
                              machine_tokens=f"other-token,{MACHINE_TOKEN}")
    with TestClient(create_app(settings)) as client:
        with psycopg.connect(pg_url, autocommit=True) as conn:
            conn.execute("TRUNCATE notifications, cards, runs, users RESTART IDENTITY CASCADE")
        yield client
