import os
from datetime import datetime, date
import re
import sqlite3
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
        db.get_user_by_id(friend_id)["friend_code"],
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
        db.get_user_by_id(friend)["friend_code"],
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


def test_new_event_query_prefill_and_invalid_values(client, app):
    user_id = db.create_user("Click", "click@example.com", "password", email_verified=True)
    with client.session_transaction() as session:
        session["user_id"] = user_id

    response = client.get("/events/new?date=2026-08-05&start=14:00&end=14:30")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="date" value="2026-08-05"' in html
    assert 'name="start_time" value="14:00"' in html
    assert 'name="end_time" value="14:30"' in html

    invalid = client.get("/events/new?date=invalid&start=14:30&end=14:00")
    assert "日付または時刻の形式が正しくありません" in invalid.get_data(as_text=True)


def test_calendar_is_not_available_to_other_users(client, app):
    owner = db.create_user("Owner", "calendar-owner@example.com", "password", email_verified=True)
    friend = db.create_user("Friend", "calendar-friend@example.com", "password", email_verified=True)
    db.send_friend_request(owner, db.get_user_by_id(friend)["friend_code"])
    friendship_id = db.get_received_requests(friend)[0]["friendship_id"]
    db.respond_to_request(friendship_id, friend, accept=True)

    with client.session_transaction() as session:
        session["user_id"] = owner
    own_week = client.get("/calendar?view=week&date=2026-08-05").get_data(as_text=True)
    own_month = client.get("/calendar?view=month&date=2026-08-05").get_data(as_text=True)
    assert "/events/new?date=2026-08-05" in own_week
    assert "/events/new?date=2026-08-05" in own_month

    with client.session_transaction() as session:
        session["user_id"] = friend
    assert client.get(f"/calendar/{owner}?view=month&date=2026-08-05").status_code == 404


def test_events_are_private_without_visibility_form_input(client, app):
    user_id = db.create_user("Private", "private@example.com", "password", email_verified=True)
    with client.session_transaction() as session:
        session["user_id"] = user_id

    form = client.get("/events/new")
    html = form.get_data(as_text=True)
    assert 'name="visibility"' not in html
    assert "フレンドに公開" not in html
    assert "非公開(自分のみ)" not in html

    response = client.post(
        "/events/new",
        data={
            "csrf_token": csrf_token(form),
            "date": "2026-08-05",
            "start_time": "10:00",
            "end_time": "10:30",
            "title": "本人専用予定",
            "memo": "",
            "category": "",
            "custom_color": "#3B82F6",
        },
    )
    assert response.status_code == 302
    event = db.get_events_range(user_id, datetime(2026, 8, 5), datetime(2026, 8, 6))[0]
    assert event["visibility"] == "private"

    edit = client.get(f"/events/{event['id']}/edit").get_data(as_text=True)
    assert 'name="visibility"' not in edit
    week = client.get("/calendar?view=week&date=2026-08-05").get_data(as_text=True)
    month = client.get("/calendar?view=month&date=2026-08-05").get_data(as_text=True)
    assert "🔒" not in week
    assert "🔒" not in month


def test_auto_category_progress_uses_planned_and_completed_minutes(app):
    user_id = db.create_user("Auto", "auto-progress@example.com", "password", email_verified=True)
    events = [
        db.add_event(user_id, "勉強1", datetime(2026, 8, 3, 13), datetime(2026, 8, 3, 15), category="勉強"),
        db.add_event(user_id, "勉強2", datetime(2026, 8, 6, 18), datetime(2026, 8, 6, 19, 30), category="勉強"),
        db.add_event(user_id, "勉強3", datetime(2026, 8, 10, 10), datetime(2026, 8, 10, 12, 30), category="勉強"),
    ]
    card = db.get_auto_category_progress(user_id, "month", date(2026, 8, 10))["cards"][0]
    assert card["planned_minutes"] == 360
    assert card["actual_minutes"] == 0

    assert db.complete_event(events[0], user_id, 120)
    assert db.complete_event(events[0], user_id, 120)
    card = db.get_auto_category_progress(user_id, "month", date(2026, 8, 10))["cards"][0]
    assert card["actual_minutes"] == 120
    assert card["rate"] == 33.3
    assert card["completed_count"] == 1

    assert db.uncomplete_event(events[0], user_id)
    assert db.get_auto_category_progress(user_id, "month", date(2026, 8, 10))["cards"][0]["actual_minutes"] == 0


def test_auto_progress_skips_existing_other_category(app):
    user_id = db.create_user("Category", "category-progress@example.com", "password", email_verified=True)
    db.add_event(user_id, "既存その他", datetime(2026, 8, 3, 10), datetime(2026, 8, 3, 11), category="その他")
    assert db.get_auto_category_progress(user_id, "month", date(2026, 8, 3))["cards"] == []


def test_calendar_actual_time_visual_ratio_and_overtime(client, app):
    user_id = db.create_user("Visual", "visual-progress@example.com", "password", email_verified=True)
    start = datetime(2026, 8, 5, 10)
    event_id = db.add_event(user_id, "実績表示", start, datetime(2026, 8, 5, 12), category="勉強")
    assert db.complete_event(event_id, user_id, 60)
    with client.session_transaction() as session:
        session["user_id"] = user_id
    week = client.get("/calendar?view=week&date=2026-08-05").get_data(as_text=True)
    month = client.get("/calendar?view=month&date=2026-08-05").get_data(as_text=True)
    assert "--actual-ratio: 50.0%" in week
    assert "event-actual-fill" in week
    assert "--actual-ratio: 50.0%" in month

    assert db.complete_event(event_id, user_id, 150)
    week = client.get("/calendar?view=week&date=2026-08-05").get_data(as_text=True)
    assert "--actual-ratio: 100%" in week
    assert "+30分" in week

    zero = {"start_at": "2026-08-05 10:00:00", "end_at": "2026-08-05 10:00:00", "is_completed": 1, "actual_minutes": 10}
    assert app_module.event_progress_visual(zero)["ratio"] == 0


def test_event_custom_color_is_limited_to_presets_and_rendered(client, app):
    user_id = db.create_user("Color", "color@example.com", "password", email_verified=True)
    start = datetime(2026, 8, 5, 10, 0)
    event_id = db.add_event(user_id, "色付き予定", start, datetime(2026, 8, 5, 10, 30), custom_color="#ef4444")
    invalid_id = db.add_event(user_id, "不正色", start, datetime(2026, 8, 5, 10, 30), custom_color="#ff5733")
    assert db.get_event(event_id)["custom_color"] == "#EF4444"
    assert db.get_event(invalid_id)["custom_color"] is None
    with client.session_transaction() as session:
        session["user_id"] = user_id
    week = client.get("/calendar?view=week&date=2026-08-05").get_data(as_text=True)
    month = client.get("/calendar?view=month&date=2026-08-05").get_data(as_text=True)
    assert "background-color: #EF4444" in week
    assert "background-color: #EF4444" in month


def test_event_color_form_uses_preset_radios(client, app):
    user_id = db.create_user("Palette", "palette@example.com", "password", email_verified=True)
    with client.session_transaction() as session:
        session["user_id"] = user_id
    form = client.get("/events/new").get_data(as_text=True)
    assert 'type="color"' not in form
    assert 'type="radio" name="custom_color" value="#3B82F6"' in form
    assert 'type="radio" name="custom_color" value="#EAB308"' in form


def test_friends_page_shows_progress_without_calendar_link(client, app):
    user_id = db.create_user("Owner", "friend-owner@example.com", "password", email_verified=True)
    friend_id = db.create_user("Friend", "friend-progress@example.com", "password", email_verified=True)
    db.send_friend_request(user_id, db.get_user_by_id(friend_id)["friend_code"])
    friendship_id = db.get_received_requests(friend_id)[0]["friendship_id"]
    db.respond_to_request(friendship_id, friend_id, accept=True)
    with client.session_transaction() as session:
        session["user_id"] = user_id
    page = client.get("/friends").get_data(as_text=True)
    assert "予定を見る" not in page
    assert f"/progress/friend/{friend_id}" in page


def test_navigation_hides_feed_and_feed_route_is_removed(client, app):
    user_id = db.create_user("Navigation", "navigation@example.com", "password", email_verified=True)
    with client.session_transaction() as session:
        session["user_id"] = user_id
    page = client.get("/calendar").get_data(as_text=True)
    assert ">フィード<" not in page
    assert "href=\"/feed\"" not in page
    assert ">集計<" not in page
    assert client.get("/feed").status_code == 404


def test_friend_request_is_managed_from_notifications(client, app):
    requester = db.create_user("Requester", "requester@example.com", "password", email_verified=True)
    recipient = db.create_user("Recipient", "recipient@example.com", "password", email_verified=True)
    with client.session_transaction() as session:
        session["user_id"] = requester
    token = csrf_token(client.get("/friends"))
    recipient_code = db.get_user_by_id(recipient)["friend_code"]
    client.post("/friends/request", data={"friend_code": recipient_code, "csrf_token": token})
    with client.session_transaction() as session:
        session["user_id"] = recipient
    notifications = client.get("/notifications").get_data(as_text=True)
    assert "フレンド申請" in notifications
    assert "承認" in notifications and "拒否" in notifications
    assert "受信した申請" not in client.get("/friends").get_data(as_text=True)


def test_users_receive_unique_eight_character_friend_codes(app):
    first_id = db.create_user("First", "first-code@example.com", "password")
    second_id = db.create_user("Second", "second-code@example.com", "password")

    first_code = db.get_user_by_id(first_id)["friend_code"]
    second_code = db.get_user_by_id(second_id)["friend_code"]

    assert re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}", first_code)
    assert re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}", second_code)
    assert first_code != second_code
    assert db.get_user_by_friend_code(first_code.lower())["id"] == first_id


def test_mypage_shows_name_and_friend_code(client, app):
    user_id = db.create_user("マイページ利用者", "mypage@example.com", "password", email_verified=True)
    user = db.get_user_by_id(user_id)
    with client.session_transaction() as session:
        session["user_id"] = user_id

    response = client.get("/mypage")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "マイページ利用者" in body
    assert user["friend_code"] in body
    assert "mypage@example.com" not in body


def test_friend_request_uses_friend_code_instead_of_email(client, app):
    requester_id = db.create_user("Requester", "code-requester@example.com", "password", email_verified=True)
    recipient_id = db.create_user("Recipient", "code-recipient@example.com", "password", email_verified=True)
    recipient = db.get_user_by_id(recipient_id)
    with client.session_transaction() as session:
        session["user_id"] = requester_id

    page = client.get("/friends")
    body = page.get_data(as_text=True)
    assert 'name="friend_code"' in body
    assert 'name="email"' not in body

    response = client.post(
        "/friends/request",
        data={
            "friend_code": recipient["friend_code"].lower(),
            "csrf_token": csrf_token(page),
        },
    )

    assert response.status_code == 302
    requests = db.get_received_requests(recipient_id)
    assert len(requests) == 1
    assert requests[0]["user_id"] == requester_id


def test_existing_users_receive_friend_codes_during_migration(tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL, "
        "email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, "
        "email_verified BOOLEAN NOT NULL DEFAULT 1, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO users (display_name, email, password_hash, email_verified, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Legacy", "legacy@example.com", "unused", 1, "2026-08-01 00:00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", str(legacy_db))
    db.init_db()

    migrated = db.get_user_by_email("legacy@example.com")
    assert re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}", migrated["friend_code"])
