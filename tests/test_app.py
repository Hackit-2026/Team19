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
    match = re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    "secret",
    [
        None,
        "too-short",
        "replace-with-a-random-secret",
    ],
)
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
    assert (
        "SECRET_KEY environment variable must contain at least 32 characters"
        in result.stderr
    )


def test_public_pages_and_healthcheck(client):
    assert client.get("/login").status_code == 200
    assert client.get("/signup").status_code == 200
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_post_without_csrf_token_is_rejected(client):
    response = client.post(
        "/login",
        data={
            "email": "demo@example.com",
            "password": "password",
        },
    )

    assert response.status_code == 400


def test_development_mailbox_is_disabled_by_default(client):
    assert client.get("/dev/mailbox").status_code == 404
    assert client.get("/dev/mailbox/1").status_code == 404


def test_external_login_redirect_is_rejected(client):
    db.create_user(
        "Demo",
        "demo@example.com",
        "password",
        email_verified=True,
    )

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
        assert (
            app_module.safe_next_url("/calendar?view=month")
            == "/calendar?view=month"
        )


def test_database_survives_new_connections(app):
    user_id = db.create_user(
        "Persistent",
        "persistent@example.com",
        "password",
    )

    assert user_id is not None
    assert db.get_user_by_email("persistent@example.com")["id"] == user_id


def test_manual_progress_is_saved_and_returned(app):
    user_id = db.create_user(
        "Progress",
        "progress@example.com",
        "password",
    )

    db.set_progress(user_id, "week", 67, True)

    saved = db.get_goal(user_id, "week")
    progress = db.compute_progress(user_id)

    assert saved["manual_rate"] == 67
    assert saved["is_public"] == 1
    assert progress["week"]["achievement_rate"] == 67
    assert progress["week"]["has_progress"] is True


def test_progress_page_accepts_manual_rate(client):
    user_id = db.create_user(
        "Progress",
        "progress-page@example.com",
        "password",
        email_verified=True,
    )

    token = csrf_token(client.get("/login"))

    client.post(
        "/login",
        data={
            "email": "progress-page@example.com",
            "password": "password",
            "csrf_token": token,
        },
    )

    page = client.get("/goals")

    assert page.status_code == 200
    assert "現在の達成率" in page.get_data(as_text=True)

    token = csrf_token(page)

    response = client.post(
        "/goals",
        data={
            "period": "month",
            "achievement_rate": "125",
            "is_public": "1",
            "csrf_token": token,
        },
    )

    assert response.status_code == 302
    assert db.get_goal(user_id, "month")["manual_rate"] == 125


def test_friends_page_shows_public_progress_and_spent_time(client):
    viewer_id = db.create_user(
        "Viewer",
        "viewer@example.com",
        "password",
        email_verified=True,
    )

    friend_id = db.create_user(
        "Friend",
        "friend@example.com",
        "password",
        email_verified=True,
    )

    status, _ = db.send_friend_request(
        viewer_id,
        "friend@example.com",
    )
    assert status == "ok"

    request_item = db.get_received_requests(friend_id)[0]

    accepted, _ = db.respond_to_request(
        request_item["friendship_id"],
        friend_id,
        accept=True,
    )
    assert accepted is True

    db.set_progress(
        friend_id,
        "week",
        67,
        True,
    )

    datetime_module = __import__("datetime")
    now = datetime_module.datetime.now()
    start = now.replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )

    db.add_event(
        friend_id,
        "Shared work",
        start,
        start + datetime_module.timedelta(minutes=90),
    )

    token = csrf_token(client.get("/login"))

    client.post(
        "/login",
        data={
            "email": "viewer@example.com",
            "password": "password",
            "csrf_token": token,
        },
    )

    page = client.get("/friends")
    body = page.get_data(as_text=True)

    assert page.status_code == 200
    assert "公開中の進捗" in body
    assert "67%" in body
    assert "費やした時間" in body
    assert "1時間30分" in body


def test_named_progress_goal_and_update_history(app):
    user_id = db.create_user(
        "Named",
        "named@example.com",
        "password",
    )

    first_id = db.create_progress_goal(
        user_id,
        "アプリ完成",
        progress_rate=0,
    )

    second_id = db.create_progress_goal(
        user_id,
        "レポート",
        progress_rate=100,
    )

    assert [
        goal["id"]
        for goal in db.get_progress_goals(user_id)
    ] == [
        first_id,
        second_id,
    ]

    assert db.update_progress_rate(
        first_id,
        user_id,
        40,
        "画面を作成",
    ) is True

    assert db.update_progress_rate(
        first_id,
        user_id,
        40,
        "同じ割合",
    ) is True

    updates = db.get_progress_updates(
        first_id,
        user_id,
    )

    assert len(updates) == 1
    assert updates[0]["previous_rate"] == 0
    assert updates[0]["new_rate"] == 40

    with pytest.raises(ValueError):
        db.update_progress_rate(
            first_id,
            user_id,
            101,
        )


def test_named_progress_rate_accepts_one_percent_steps(app):
    user_id = db.create_user(
        "OnePercent",
        "one-percent@example.com",
        "password",
    )

    goal_id = db.create_progress_goal(
        user_id,
        "細かな進捗",
        progress_rate=0,
    )

    for rate in (1, 37, 99):
        assert db.update_progress_rate(
            goal_id,
            user_id,
            rate,
        ) is True

    assert [
        update["new_rate"]
        for update in db.get_progress_updates(goal_id, user_id)
    ] == [
        99,
        37,
        1,
    ]

    quick_goal_id = db.create_progress_goal(
        user_id,
        "クイック操作",
        progress_rate=70,
    )

    assert db.update_progress_rate(
        quick_goal_id,
        user_id,
        75,
    ) is True

    assert db.update_progress_rate(
        quick_goal_id,
        user_id,
        85,
    ) is True

    assert (
        db.get_progress_goal(
            quick_goal_id,
            user_id,
        )["progress_rate"]
        == 85
    )

    with pytest.raises(ValueError):
        db.update_progress_rate(
            goal_id,
            user_id,
            -1,
        )


def test_named_progress_goal_owner_and_public_access(client, app):
    owner = db.create_user(
        "Owner",
        "owner-progress@example.com",
        "password",
    )

    friend = db.create_user(
        "Friend",
        "friend-progress@example.com",
        "password",
    )

    stranger = db.create_user(
        "Stranger",
        "stranger-progress@example.com",
        "password",
    )

    public_goal = db.create_progress_goal(
        owner,
        "公開目標",
        progress_rate=70,
        is_public=True,
    )

    private_goal = db.create_progress_goal(
        owner,
        "非公開目標",
        progress_rate=20,
        is_public=False,
    )

    assert db.update_progress_goal(
        public_goal,
        stranger,
        "不正",
        "",
        None,
        False,
    ) is False

    assert db.delete_progress_goal(
        private_goal,
        stranger,
    ) is False

    db.send_friend_request(
        owner,
        "friend-progress@example.com",
    )

    friendship_id = db.get_received_requests(friend)[0]["friendship_id"]

    db.respond_to_request(
        friendship_id,
        friend,
        accept=True,
    )

    with client.session_transaction() as session:
        session["user_id"] = friend

    page = client.get(
        f"/progress/friend/{owner}"
    )
    text = page.get_data(as_text=True)

    assert page.status_code == 200
    assert "公開目標" in text
    assert "非公開目標" not in text

    with client.session_transaction() as session:
        session["user_id"] = stranger

    assert (
        client.get(
            f"/progress/friend/{owner}"
        ).status_code
        == 302
    )