import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-123456789")
os.environ.setdefault("DEV_MAILBOX_ENABLED", "false")

import app as app_module
import db


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db(reset=True)
    app_module.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key-for-pytest-only-123456789",
        DEV_MAILBOX_ENABLED=False,
        WTF_CSRF_ENABLED=True,
    )
    yield app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()
