"""
Shared test fixtures.

Uses a single shared in-memory SQLite connection (StaticPool) so all
sessions within a test see the same tables and data.
"""

import os
import sys
import json
import pytest

# Ensure backend/ is on sys.path so imports like `from config import ...` work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force SQLite in-memory for all tests — must be set before any import touches the DB.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-secret-key-for-tests-only-1234567890"


@pytest.fixture(autouse=True)
def _reset_db():
    """
    Create a shared in-memory SQLite engine that all sessions share.
    Uses StaticPool so every get_session() call returns a session on the
    same underlying connection — this is the standard SQLAlchemy testing pattern.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import database.session as sess
    from database.models import Base

    # Create a single shared in-memory engine.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable WAL-like behavior: allow nested transactions.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Inject our test engine/session into database.session module.
    sess._engine = engine
    sess._SessionLocal = session_factory

    # Patch init_engine and db.init_db to be no-ops during this test.
    _orig_init = sess.init_engine

    def _noop_init():
        pass  # engine already set up

    sess.init_engine = _noop_init

    import db as db_mod
    _orig_init_db = db_mod.init_db
    db_mod.init_db = _noop_init

    yield

    # Restore originals.
    sess.init_engine = _orig_init
    db_mod.init_db = _orig_init_db

    # Tear down.
    Base.metadata.drop_all(engine)
    engine.dispose()
    sess._engine = None
    sess._SessionLocal = None


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config singleton between tests."""
    import config.loader as loader
    loader._config = None
    yield
    loader._config = None


@pytest.fixture()
def loaded_config():
    """Load config from shoppster.json and return it."""
    from config.loader import load_config
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "shoppster.json")
    return load_config(config_path)


@pytest.fixture()
def client(loaded_config):
    """FastAPI TestClient with database and config initialised."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


@pytest.fixture()
def admin_user():
    """Create an admin user and return (email, password) tuple."""
    from database.session import get_session
    from auth.security import hash_password
    from sqlalchemy import text
    from datetime import datetime

    email = "admin@test.com"
    password = "testpass1234"

    session = get_session()
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hash, :name, 'admin', true, :now)"""),
        {"email": email, "hash": hash_password(password), "name": "Test Admin", "now": datetime.utcnow()},
    )
    session.commit()
    session.close()
    return email, password


@pytest.fixture()
def viewer_user():
    """Create a viewer user and return (email, password) tuple."""
    from database.session import get_session
    from auth.security import hash_password
    from sqlalchemy import text
    from datetime import datetime

    email = "viewer@test.com"
    password = "viewerpass1234"

    session = get_session()
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hash, :name, 'viewer', true, :now)"""),
        {"email": email, "hash": hash_password(password), "name": "Test Viewer", "now": datetime.utcnow()},
    )
    session.commit()
    session.close()
    return email, password


@pytest.fixture()
def admin_token(client, admin_user):
    """Login as admin and return the Bearer token string."""
    email, password = admin_user
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture()
def viewer_token(client, viewer_user):
    """Login as viewer and return the Bearer token string."""
    email, password = viewer_user
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def auth_header(token: str) -> dict:
    """Helper to build Authorization header dict."""
    return {"Authorization": f"Bearer {token}"}
