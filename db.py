"""
db.py
-----
フル機能版Webアプリのデータ層。SQLite + 標準sqlite3モジュールのみを使用する
(要件定義フェーズで作成した「02_データベース設計書.md」のテーブル定義をベースに、
Phase2で計画していた機能(目標共有・活動フィード・メール認証等)も含めて実装したもの)。

日時はすべて 'YYYY-MM-DD HH:MM:SS' 形式のTEXTとして保存する(文字列比較で
時系列ソートできる形式)。デモのため、タイムゾーンは扱わずローカル時刻のみ。

メール送信について: ローカルサーバーのため実際のSMTPは使わず、送信内容を
outbox テーブルに保存して「開発用メールボックス」画面(/dev/mailbox)で
確認できるようにしている(Mailtrap/MailHog等のローカル開発ツールと同じ考え方)。
"""

import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db")
DB_PATH = os.path.abspath(os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH))
DT_FMT = "%Y-%m-%d %H:%M:%S"

TASK_PRESETS = ["作業", "勉強", "休憩", "運動", "読書", "その他"]
CATEGORY_PRESETS = ["学習", "仕事", "運動", "趣味", "その他"]

EMAIL_VERIFICATION_TTL_HOURS = 24
PASSWORD_RESET_TTL_MINUTES = 60


def now_str():
    return datetime.now().strftime(DT_FMT)


def to_str(dt):
    return dt.strftime(DT_FMT)


def parse_dt(s):
    return datetime.strptime(s, DT_FMT)


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_column(conn, table, column, coldef):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_connection()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email_verified BOOLEAN NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            memo TEXT,
            source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('timer', 'manual')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_user_start ON events(user_id, start_at);

        CREATE TABLE IF NOT EXISTS active_timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            task TEXT NOT NULL,
            started_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS friendships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            addressee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (requester_id, addressee_id)
        );

        CREATE TABLE IF NOT EXISTS email_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            verified_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            period TEXT NOT NULL CHECK (period IN ('week', 'month')),
            target_minutes INTEGER NOT NULL,
            manual_rate INTEGER,
            is_public BOOLEAN NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (user_id, period)
        );

        CREATE TABLE IF NOT EXISTS progress_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            progress_rate INTEGER NOT NULL DEFAULT 0 CHECK (progress_rate BETWEEN 0 AND 100),
            deadline TEXT,
            is_public BOOLEAN NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_progress_goals_user ON progress_goals(user_id);

        CREATE TABLE IF NOT EXISTS progress_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL REFERENCES progress_goals(id) ON DELETE CASCADE,
            previous_rate INTEGER NOT NULL,
            new_rate INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_progress_updates_goal ON progress_updates(goal_id, created_at);

        CREATE TABLE IF NOT EXISTS feed_reads (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            last_read_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            link TEXT,
            is_read BOOLEAN NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    # 既存DBに対する後方互換マイグレーション(列追加)
    _ensure_column(conn, "events", "category", "TEXT")
    _ensure_column(conn, "events", "visibility", "TEXT NOT NULL DEFAULT 'public'")
    _ensure_column(conn, "events", "custom_color", "TEXT")
    _ensure_column(conn, "goals", "manual_rate", "INTEGER")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def create_user(display_name, email, password, email_verified=True):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (display_name, email, password_hash, email_verified, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (display_name, email.strip().lower(), generate_password_hash(password), 1 if email_verified else 0, now_str()),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_by_email(email):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_password(user, password):
    return check_password_hash(user["password_hash"], password)


def change_password(user_id, new_password):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_email_verified(user_id, verified):
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET email_verified = ? WHERE id = ?", (1 if verified else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def search_user_by_email(email):
    return get_user_by_email(email)


# ---------------------------------------------------------------------------
# 開発用メールボックス(実SMTPの代わり)
# ---------------------------------------------------------------------------

def send_mock_mail(to_email, subject, body):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO outbox (to_email, subject, body, created_at) VALUES (?, ?, ?, ?)",
            (to_email, subject, body, now_str()),
        )
        conn.commit()
    finally:
        conn.close()


def get_outbox(email=None, limit=50):
    conn = get_connection()
    try:
        if email:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE to_email = ? ORDER BY id DESC LIMIT ?",
                (email.strip().lower(), limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_outbox_item(mail_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM outbox WHERE id = ?", (mail_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# メールアドレス認証
# ---------------------------------------------------------------------------

def create_email_verification(user_id):
    token = secrets.token_urlsafe(24)
    expires_at = to_str(datetime.now() + timedelta(hours=EMAIL_VERIFICATION_TTL_HOURS))
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO email_verifications (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token, expires_at, now_str()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def verify_email_token(token):
    """成功したら user_id を、無効/期限切れなら None を返す"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM email_verifications WHERE token = ? AND verified_at IS NULL", (token,)
        ).fetchone()
        if row is None:
            return None
        if parse_dt(row["expires_at"]) < datetime.now():
            return None
        conn.execute("UPDATE email_verifications SET verified_at = ? WHERE id = ?", (now_str(), row["id"]))
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (row["user_id"],))
        conn.commit()
        return row["user_id"]
    finally:
        conn.close()


def send_verification_email(user):
    token = create_email_verification(user["id"])
    link = f"/verify-email/{token}"
    body = (
        f"{user['display_name']} 様\n\n"
        "タイムラインカレンダーへのご登録ありがとうございます。\n"
        "以下のリンクからメールアドレスの確認を完了してください(有効期限24時間)。\n\n"
        f"{link}\n"
    )
    send_mock_mail(user["email"], "【タイムラインカレンダー】メールアドレスの確認", body)
    return token


# ---------------------------------------------------------------------------
# パスワードリセット
# ---------------------------------------------------------------------------

def create_password_reset(user_id):
    token = secrets.token_urlsafe(24)
    expires_at = to_str(datetime.now() + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES))
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO password_resets (user_id, token, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token, expires_at, now_str()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def send_password_reset_email(user):
    token = create_password_reset(user["id"])
    link = f"/password-reset/{token}"
    body = (
        f"{user['display_name']} 様\n\n"
        "パスワード再設定のリクエストを受け付けました。\n"
        f"以下のリンクから新しいパスワードを設定してください(有効期限{PASSWORD_RESET_TTL_MINUTES}分)。\n\n"
        f"{link}\n\n"
        "心当たりがない場合は、このメールを無視してください。\n"
    )
    send_mock_mail(user["email"], "【タイムラインカレンダー】パスワード再設定", body)
    return token


def get_password_reset(token):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def is_password_reset_valid(reset_row):
    if reset_row is None or reset_row["used_at"] is not None:
        return False
    return parse_dt(reset_row["expires_at"]) >= datetime.now()


def use_password_reset(token, new_password):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
        if row is None or row["used_at"] is not None or parse_dt(row["expires_at"]) < datetime.now():
            return False
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), row["user_id"]),
        )
        conn.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (now_str(), row["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def get_events_range(user_id, range_start, range_end, only_public=False):
    """[range_start, range_end) と重なるイベントを取得(range_*はdatetime)"""
    conn = get_connection()
    try:
        query = "SELECT * FROM events WHERE user_id = ? AND start_at < ? AND end_at > ?"
        params = [user_id, to_str(range_end), to_str(range_start)]
        if only_public:
            query += " AND visibility = 'public'"
        query += " ORDER BY start_at"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_conflicts(user_id, start_at, end_at, exclude_id=None):
    """start_at〜end_at(datetime)と重なる既存イベントを返す"""
    conn = get_connection()
    try:
        query = "SELECT * FROM events WHERE user_id = ? AND start_at < ? AND end_at > ?"
        params = [user_id, to_str(end_at), to_str(start_at)]
        if exclude_id is not None:
            query += " AND id != ?"
            params.append(exclude_id)
        query += " ORDER BY start_at"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_event(user_id, title, start_at, end_at, memo="", source="manual", category=None, visibility="public", custom_color=None):
    conn = get_connection()
    try:
        custom_color = custom_color if custom_color and re.fullmatch(r"#[0-9a-fA-F]{6}", custom_color) else None
        ts = now_str()
        cur = conn.execute(
            "INSERT INTO events (user_id, title, start_at, end_at, memo, source, category, visibility, custom_color, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, title, to_str(start_at), to_str(end_at), memo, source, category or None, visibility, custom_color, ts, ts),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_event(event_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_event(event_id, title, start_at, end_at, memo="", category=None, visibility="public", custom_color=None):
    conn = get_connection()
    try:
        custom_color = custom_color if custom_color and re.fullmatch(r"#[0-9a-fA-F]{6}", custom_color) else None
        conn.execute(
            "UPDATE events SET title = ?, start_at = ?, end_at = ?, memo = ?, category = ?, visibility = ?, custom_color = ?, updated_at = ? WHERE id = ?",
            (title, to_str(start_at), to_str(end_at), memo, category or None, visibility, custom_color, now_str(), event_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_event(event_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
    finally:
        conn.close()


def total_seconds_for_range(user_id, range_start, range_end):
    total = 0
    for ev in get_events_range(user_id, range_start, range_end):
        s = max(parse_dt(ev["start_at"]), range_start)
        e = min(parse_dt(ev["end_at"]), range_end)
        total += max(0, int((e - s).total_seconds()))
    return total


def category_totals_for_range(user_id, range_start, range_end):
    """[{"category": str, "seconds": int}, ...] を合計時間の降順で返す(未分類は「未分類」)"""
    totals = {}
    for ev in get_events_range(user_id, range_start, range_end):
        s = max(parse_dt(ev["start_at"]), range_start)
        e = min(parse_dt(ev["end_at"]), range_end)
        seconds = max(0, int((e - s).total_seconds()))
        cat = ev["category"] or "未分類"
        totals[cat] = totals.get(cat, 0) + seconds
    items = [{"category": k, "seconds": v} for k, v in totals.items()]
    items.sort(key=lambda x: -x["seconds"])
    return items


# ---------------------------------------------------------------------------
# active_timers
# ---------------------------------------------------------------------------

def get_active_timer(user_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM active_timers WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def start_timer(user_id, task):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO active_timers (user_id, task, started_at) VALUES (?, ?, ?)",
            (user_id, task, now_str()),
        )
        conn.commit()
    finally:
        conn.close()


def stop_timer(user_id):
    """計測中タイマーを停止し、eventsに1件記録して作成したevent_idを返す。計測中でなければNone"""
    active = get_active_timer(user_id)
    if active is None:
        return None
    start_at = parse_dt(active["started_at"])
    end_at = datetime.now()
    if end_at <= start_at:
        end_at = start_at + timedelta(seconds=1)

    conn = get_connection()
    try:
        ts = now_str()
        cur = conn.execute(
            "INSERT INTO events (user_id, title, start_at, end_at, memo, source, category, visibility, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', 'timer', NULL, 'public', ?, ?)",
            (user_id, active["task"], to_str(start_at), to_str(end_at), ts, ts),
        )
        conn.execute("DELETE FROM active_timers WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# goals / progress(目標・達成率)
# ---------------------------------------------------------------------------

def get_goal(user_id, period):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM goals WHERE user_id = ? AND period = ?", (user_id, period)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_goals(user_id):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM goals WHERE user_id = ?", (user_id,)).fetchall()
        return {r["period"]: dict(r) for r in rows}
    finally:
        conn.close()


def set_goal(user_id, period, target_minutes, is_public):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM goals WHERE user_id = ? AND period = ?", (user_id, period)
        ).fetchone()
        ts = now_str()
        if existing:
            conn.execute(
                "UPDATE goals SET target_minutes = ?, is_public = ?, updated_at = ? WHERE id = ?",
                (target_minutes, 1 if is_public else 0, ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO goals (user_id, period, target_minutes, is_public, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, period, target_minutes, 1 if is_public else 0, ts, ts),
            )
        conn.commit()
    finally:
        conn.close()


def set_progress(user_id, period, achievement_rate, is_public):
    """週・月の手動達成率と公開設定を保存する。

    target_minutes は旧UIとの後方互換のため残し、新規行ではダミー値1を入れる。
    実際にかけた時間は events から自動集計する。
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM goals WHERE user_id = ? AND period = ?", (user_id, period)
        ).fetchone()
        ts = now_str()
        if existing:
            conn.execute(
                "UPDATE goals SET manual_rate = ?, is_public = ?, updated_at = ? WHERE id = ?",
                (achievement_rate, 1 if is_public else 0, ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO goals (user_id, period, target_minutes, manual_rate, is_public, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?)",
                (user_id, period, achievement_rate, 1 if is_public else 0, ts, ts),
            )
        conn.commit()
    finally:
        conn.close()


def delete_goal(user_id, period):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM goals WHERE user_id = ? AND period = ?", (user_id, period))
        conn.commit()
    finally:
        conn.close()


def _period_bounds(period, today=None):
    """importでcalendar_utilsに依存しないよう、weekはMon始まりで自前計算する"""
    today = today or datetime.now().date()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    else:  # month
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    return start, end


def compute_progress(user_id):
    """今日・週・月の記録時間と、手動または旧目標由来の達成率を返す"""
    from datetime import time as dtime

    today = datetime.now().date()
    today_start = datetime.combine(today, dtime.min)
    today_end = today_start + timedelta(days=1)
    today_minutes = total_seconds_for_range(user_id, today_start, today_end) // 60

    result = {"today_minutes": today_minutes}
    for period in ("week", "month"):
        p_start, p_end = _period_bounds(period, today)
        total_minutes = total_seconds_for_range(
            user_id, datetime.combine(p_start, dtime.min), datetime.combine(p_end, dtime.min)
        ) // 60
        goal = get_goal(user_id, period)
        target = goal["target_minutes"] if goal else None
        manual_rate = goal.get("manual_rate") if goal else None
        rate = manual_rate if manual_rate is not None else (round(total_minutes / target * 100) if target else 0)
        result[period] = {
            "total_minutes": total_minutes,
            "target_minutes": target,
            "achievement_rate": rate,
            "is_public": bool(goal["is_public"]) if goal else False,
            "has_progress": goal is not None,
            "is_manual": manual_rate is not None,
        }
    return result


def get_friend_public_progress(user_id):
    """公開設定(is_public)になっている期間だけを返す(相手のカレンダー閲覧時などに使用)"""
    full = compute_progress(user_id)
    public = {}
    for period in ("week", "month"):
        info = full[period]
        if info["is_public"] and info["has_progress"]:
            public[period] = {
                "total_minutes": info["total_minutes"],
                "target_minutes": info["target_minutes"],
                "achievement_rate": info["achievement_rate"],
            }
    return public


# ---------------------------------------------------------------------------
# named progress goals (名前付き目標)
# ---------------------------------------------------------------------------

def create_progress_goal(user_id, title, description="", progress_rate=0, deadline=None, is_public=False):
    if not 0 <= int(progress_rate) <= 100:
        raise ValueError("progress_rate must be between 0 and 100")
    conn = get_connection()
    try:
        ts = now_str()
        cur = conn.execute(
            "INSERT INTO progress_goals (user_id, title, description, progress_rate, deadline, is_public, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, title, description or None, int(progress_rate), deadline, 1 if is_public else 0, ts, ts),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_progress_goal(goal_id, user_id=None):
    conn = get_connection()
    try:
        sql = "SELECT * FROM progress_goals WHERE id = ?"
        params = [goal_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_progress_goals(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM progress_goals WHERE user_id = ? "
            "ORDER BY CASE WHEN progress_rate >= 100 THEN 1 ELSE 0 END, updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_public_progress_goals(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, user_id, title, progress_rate, deadline, updated_at "
            "FROM progress_goals WHERE user_id = ? AND is_public = 1 "
            "ORDER BY CASE WHEN progress_rate >= 100 THEN 1 ELSE 0 END, updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_progress_goal(goal_id, user_id, title, description, deadline, is_public):
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE progress_goals SET title = ?, description = ?, deadline = ?, is_public = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (title, description or None, deadline, 1 if is_public else 0, now_str(), goal_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_progress_rate(goal_id, user_id, new_rate, note=""):
    if not 0 <= int(new_rate) <= 100:
        raise ValueError("progress_rate must be between 0 and 100")
    conn = get_connection()
    try:
        goal = conn.execute(
            "SELECT progress_rate FROM progress_goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
        ).fetchone()
        if goal is None:
            return False
        new_rate = int(new_rate)
        if goal["progress_rate"] == new_rate:
            return True
        ts = now_str()
        conn.execute(
            "UPDATE progress_goals SET progress_rate = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (new_rate, ts, goal_id, user_id),
        )
        conn.execute(
            "INSERT INTO progress_updates (goal_id, previous_rate, new_rate, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (goal_id, goal["progress_rate"], new_rate, note or None, ts),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_progress_updates(goal_id, user_id):
    conn = get_connection()
    try:
        owner = conn.execute(
            "SELECT 1 FROM progress_goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
        ).fetchone()
        if owner is None:
            return []
        rows = conn.execute(
            "SELECT * FROM progress_updates WHERE goal_id = ? ORDER BY created_at DESC, id DESC", (goal_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_progress_goal(goal_id, user_id):
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM progress_goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 活動フィード
# ---------------------------------------------------------------------------

def get_feed(user_id, limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT e.id, e.user_id, u.display_name, e.title, e.start_at, e.end_at, e.created_at
            FROM events e
            JOIN users u ON u.id = e.user_id
            JOIN friendships f
              ON (f.requester_id = ? AND f.addressee_id = e.user_id)
              OR (f.addressee_id = ? AND f.requester_id = e.user_id)
            WHERE f.status = 'accepted' AND e.visibility = 'public'
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_feed_last_read(user_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT last_read_at FROM feed_reads WHERE user_id = ?", (user_id,)).fetchone()
        return row["last_read_at"] if row else None
    finally:
        conn.close()


def get_feed_unread_count(user_id):
    last_read = get_feed_last_read(user_id)
    feed = get_feed(user_id, limit=200)
    if last_read is None:
        return len(feed)
    return sum(1 for item in feed if item["created_at"] > last_read)


def mark_feed_read(user_id):
    conn = get_connection()
    try:
        existing = conn.execute("SELECT user_id FROM feed_reads WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            conn.execute("UPDATE feed_reads SET last_read_at = ? WHERE user_id = ?", (now_str(), user_id))
        else:
            conn.execute(
                "INSERT INTO feed_reads (user_id, last_read_at) VALUES (?, ?)", (user_id, now_str())
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# アプリ内通知
# ---------------------------------------------------------------------------

def create_notification(user_id, kind, message, link=None):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO notifications (user_id, kind, message, link, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, message, link, now_str()),
        )
        conn.commit()
    finally:
        conn.close()


def get_notifications(user_id, limit=30):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def unread_notification_count(user_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,)
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def mark_notifications_read(user_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# friendships
# ---------------------------------------------------------------------------

def send_friend_request(requester_id, addressee_email):
    addressee = get_user_by_email(addressee_email)
    if addressee is None:
        return "not_found", None
    if addressee["id"] == requester_id:
        return "self", None

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM friendships WHERE "
            "(requester_id = ? AND addressee_id = ?) OR (requester_id = ? AND addressee_id = ?)",
            (requester_id, addressee["id"], addressee["id"], requester_id),
        ).fetchone()
        if existing:
            if existing["status"] == "accepted":
                return "already_friends", None
            if existing["status"] == "pending":
                return "already_pending", None
            # declined -> 再申請を許可(既存行をpendingに戻す)
            conn.execute(
                "UPDATE friendships SET status='pending', requester_id=?, addressee_id=?, updated_at=? WHERE id=?",
                (requester_id, addressee["id"], now_str(), existing["id"]),
            )
            conn.commit()
            return "ok", addressee

        ts = now_str()
        conn.execute(
            "INSERT INTO friendships (requester_id, addressee_id, status, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (requester_id, addressee["id"], ts, ts),
        )
        conn.commit()
        return "ok", addressee
    finally:
        conn.close()


def get_received_requests(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT f.id AS friendship_id, u.id AS user_id, u.display_name, f.created_at "
            "FROM friendships f JOIN users u ON u.id = f.requester_id "
            "WHERE f.addressee_id = ? AND f.status = 'pending' ORDER BY f.created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_sent_requests(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT f.id AS friendship_id, u.id AS user_id, u.display_name, f.created_at "
            "FROM friendships f JOIN users u ON u.id = f.addressee_id "
            "WHERE f.requester_id = ? AND f.status = 'pending' ORDER BY f.created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_friends(user_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT f.id AS friendship_id,
                   CASE WHEN f.requester_id = ? THEN f.addressee_id ELSE f.requester_id END AS user_id
            FROM friendships f
            WHERE f.status = 'accepted' AND (f.requester_id = ? OR f.addressee_id = ?)
            """,
            (user_id, user_id, user_id),
        ).fetchall()
        friends = []
        for r in rows:
            u = get_user_by_id(r["user_id"])
            if u:
                friends.append({"friendship_id": r["friendship_id"], "user_id": u["id"], "display_name": u["display_name"]})
        return friends
    finally:
        conn.close()


def respond_to_request(friendship_id, user_id, accept):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM friendships WHERE id = ? AND addressee_id = ? AND status = 'pending'",
            (friendship_id, user_id),
        ).fetchone()
        if row is None:
            return False, None
        new_status = "accepted" if accept else "declined"
        conn.execute(
            "UPDATE friendships SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now_str(), friendship_id),
        )
        conn.commit()
        return True, row["requester_id"]
    finally:
        conn.close()


def cancel_or_remove_friendship(friendship_id, user_id):
    """自分が関与しているfriendshipを削除する(申請取り消し・フレンド解除の両方に使う)"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM friendships WHERE id = ? AND (requester_id = ? OR addressee_id = ?)",
            (friendship_id, user_id, user_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM friendships WHERE id = ?", (friendship_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def are_friends(user_a, user_b):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM friendships WHERE status = 'accepted' AND "
            "((requester_id = ? AND addressee_id = ?) OR (requester_id = ? AND addressee_id = ?)) LIMIT 1",
            (user_a, user_b, user_b, user_a),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
