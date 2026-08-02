import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import app as app_module
import db
import calendar_utils as cu


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (None, "default"),
        ("  筋トレ ", "workout"),
        ("学習", "study"),
        ("開発", "work"),
        ("授業", "class"),
        ("アルバイト", "parttime"),
        ("休憩", "break"),
        ("趣味", "other"),
    ],
)
def test_category_class(category, expected):
    assert cu.category_class(category) == expected


def csrf_token(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize("secret", [None, "too-short", "replace-with-a-random-secret"])
def test_missing_or_weak_secret_key_is_rejected(secret):
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)
    if secret is not None:
        env["SECRET_KEY"] = secret

    result = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SECRET_KEY environment variable must contain at least 32 characters" in result.stderr


def test_public_pages_and_healthcheck(client):
    assert client.get("/login").status_code == 200
    assert client.get("/signup").status_code == 200
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_post_without_csrf_token_is_rejected(client):
    response = client.post(
        "/login",
        data={"email": "demo@example.com", "password": "password"},
    )
    assert response.status_code == 400


def test_development_mailbox_is_disabled_by_default(client):
    assert client.get("/dev/mailbox").status_code == 404
    assert client.get("/dev/mailbox/1").status_code == 404


def test_external_login_redirect_is_rejected(client):
    db.create_user("Demo", "demo@example.com", "password", email_verified=True)
    token = csrf_token(client.get("/login"))

    response = client.post(
        "/login?next=https://evil.example/phishing",
        data={
            "email": "demo@example.com",
            "password": "password",
            "csrf_token": token,
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/calendar"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/phishing",
        "//evil.example/phishing",
        "/\\evil.example/phishing",
        "calendar",
    ],
)
def test_unsafe_next_url_variants_are_rejected(app, target):
    with app.test_request_context():
        assert app_module.safe_next_url(target) == "/calendar"


def test_internal_next_url_is_allowed(app):
    with app.test_request_context():
        assert app_module.safe_next_url("/calendar?view=month") == "/calendar?view=month"


def test_database_survives_new_connections(app):
    user_id = db.create_user("Persistent", "persistent@example.com", "password")
    assert user_id is not None
    assert db.get_user_by_email("persistent@example.com")["id"] == user_id


def test_manual_progress_is_saved_and_returned(app):
    user_id = db.create_user("Progress", "progress@example.com", "password")

    db.set_progress(user_id, "week", 67, True)

    saved = db.get_goal(user_id, "week")
    progress = db.compute_progress(user_id)
    assert saved["manual_rate"] == 67
    assert saved["is_public"] == 1
    assert progress["week"]["achievement_rate"] == 67
    assert progress["week"]["has_progress"] is True


def test_progress_page_accepts_manual_rate(client):
    user_id = db.create_user("Progress", "progress-page@example.com", "password", email_verified=True)
    token = csrf_token(client.get("/login"))
    client.post(
        "/login",
        data={"email": "progress-page@example.com", "password": "password", "csrf_token": token},
    )

    page = client.get("/progress")
    assert page.status_code == 200
    assert "現在の達成率" in page.get_data(as_text=True)

    token = csrf_token(page)
    response = client.post(
        "/progress",
        data={"period": "month", "achievement_rate": "125", "is_public": "1", "csrf_token": token},
    )

    assert response.status_code == 302
    assert db.get_goal(user_id, "month")["manual_rate"] == 125
