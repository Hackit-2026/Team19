"""
app.py
------
タイムラインカレンダー Web版(フル機能版・ローカルサーバー)

要件定義書(01〜03)に記載した機能を優先度1〜4まですべて実装したもの
(ソーシャルログインのみ、ローカル環境では実際のOAuth連携ができないため対象外)。
メール送信は実SMTPの代わりに「開発用メールボックス」(/dev/mailbox)で再現している。
詳細はREADME参照。
"""

import os
from datetime import datetime, date, time, timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request, redirect, url_for, session, flash, g
from flask_wtf.csrf import CSRFProtect

import db
import calendar_utils as cu

EVENT_COLOR_OPTIONS = [
    "#3B82F6", "#22C55E", "#EF4444", "#F97316",
    "#EAB308", "#8B5CF6", "#EC4899", "#64748B",
]

def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


secret_key = os.environ.get("SECRET_KEY")
if not secret_key or secret_key == "replace-with-a-random-secret" or len(secret_key) < 32:
    raise RuntimeError("SECRET_KEY environment variable must contain at least 32 characters")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secret_key,
    DEV_MAILBOX_ENABLED=env_flag("DEV_MAILBOX_ENABLED"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("SESSION_COOKIE_SECURE"),
)
csrf = CSRFProtect(app)

# email_verified チェックを免除するエンドポイント(未認証ユーザーでもアクセスできる)
VERIFY_EXEMPT_ENDPOINTS = {
    "verify_notice", "verify_email_resend", "verify_email_confirm",
    "logout", "account_settings",
    "dev_mailbox", "dev_mailbox_item",
    "static", "login", "signup", "index",
    "password_reset_request", "password_reset_confirm",
}


# ---------------------------------------------------------------------------
# 共通: ログインユーザーの読み込み・認証必須デコレータ
# ---------------------------------------------------------------------------

@app.before_request
def load_logged_in_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = db.get_user_by_id(user_id)
        if g.user is None:
            session.clear()


@app.before_request
def require_email_verified():
    if g.user and not g.user["email_verified"] and request.endpoint not in VERIFY_EXEMPT_ENDPOINTS:
        return redirect(url_for("verify_notice"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def parse_date_param(value, fallback=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback or date.today()


def safe_next_url(target):
    """Return an internal redirect target, or the calendar URL if it is unsafe."""
    if target:
        parsed = urlsplit(target)
        if (
            not parsed.scheme
            and not parsed.netloc
            and target.startswith("/")
            and not target.startswith("//")
            and "\\" not in target
        ):
            return target
    return url_for("calendar_view")


@app.context_processor
def inject_badges():
    if g.user:
        return {
            "unread_notifications": db.unread_notification_count(g.user["id"]),
            "event_color_options": EVENT_COLOR_OPTIONS,
        }
    return {"event_color_options": EVENT_COLOR_OPTIONS}


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("calendar_view") if g.user else url_for("login"))


@app.route("/healthz")
def healthz():
    conn = db.get_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok"}


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("calendar_view"))

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        errors = []
        if not display_name:
            errors.append("表示名を入力してください")
        if not email or "@" not in email:
            errors.append("有効なメールアドレスを入力してください")
        if len(password) < 6:
            errors.append("パスワードは6文字以上にしてください")
        if password != password2:
            errors.append("パスワードが一致しません")
        if not errors and db.get_user_by_email(email):
            errors.append("このメールアドレスは既に登録されています")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("signup.html", display_name=display_name, email=email)

        user_id = db.create_user(display_name, email, password, email_verified=False)
        user = db.get_user_by_id(user_id)
        db.send_verification_email(user)
        session.clear()
        session["user_id"] = user_id
        flash("登録が完了しました。確認メールを送信しましたので、メールアドレスの確認を行ってください", "info")
        return redirect(url_for("verify_notice"))

    return render_template("signup.html", display_name="", email="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("calendar_view"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if user is None or not db.verify_password(user, password):
            flash("メールアドレスまたはパスワードが正しくありません", "error")
            return render_template("login.html", email=email)

        session.clear()
        session["user_id"] = user["id"]
        return redirect(safe_next_url(request.args.get("next")))

    return render_template("login.html", email="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# メールアドレス認証
# ---------------------------------------------------------------------------

@app.route("/verify-email/notice")
@login_required
def verify_notice():
    if g.user["email_verified"]:
        return redirect(url_for("calendar_view"))
    return render_template("verify_notice.html")


@app.route("/verify-email/resend", methods=["POST"])
@login_required
def verify_email_resend():
    if not g.user["email_verified"]:
        db.send_verification_email(g.user)
        flash("確認メールを再送信しました", "info")
    return redirect(url_for("verify_notice"))


@app.route("/verify-email/<token>")
def verify_email_confirm(token):
    user_id = db.verify_email_token(token)
    if user_id is None:
        flash("リンクが無効か、有効期限が切れています。再送信をお試しください", "error")
        return redirect(url_for("verify_notice") if g.user else url_for("login"))
    flash("メールアドレスの確認が完了しました", "info")
    if g.user and g.user["id"] == user_id:
        return redirect(url_for("calendar_view"))
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# パスワードリセット
# ---------------------------------------------------------------------------

@app.route("/password-reset", methods=["GET", "POST"])
def password_reset_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = db.get_user_by_email(email)
        if user:
            db.send_password_reset_email(user)
        # 存在有無に関わらず同じメッセージ(登録有無の推測を防ぐ)
        flash("パスワード再設定用のメールを送信しました(登録されている場合)", "info")
        return redirect(url_for("login"))
    return render_template("password_reset_request.html")


@app.route("/password-reset/<token>", methods=["GET", "POST"])
def password_reset_confirm(token):
    reset_row = db.get_password_reset(token)
    if not db.is_password_reset_valid(reset_row):
        flash("リンクが無効か、有効期限が切れています", "error")
        return redirect(url_for("password_reset_request"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if len(password) < 6:
            flash("パスワードは6文字以上にしてください", "error")
            return render_template("password_reset_confirm.html", token=token)
        if password != password2:
            flash("パスワードが一致しません", "error")
            return render_template("password_reset_confirm.html", token=token)
        db.use_password_reset(token, password)
        flash("パスワードを再設定しました。新しいパスワードでログインしてください", "info")
        return redirect(url_for("login"))

    return render_template("password_reset_confirm.html", token=token)


# ---------------------------------------------------------------------------
# アカウント設定(パスワード変更)
# ---------------------------------------------------------------------------

@app.route("/account", methods=["GET", "POST"])
@login_required
def account_settings():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        new_password2 = request.form.get("new_password2", "")

        if not db.verify_password(g.user, current_password):
            flash("現在のパスワードが正しくありません", "error")
        elif len(new_password) < 6:
            flash("新しいパスワードは6文字以上にしてください", "error")
        elif new_password != new_password2:
            flash("新しいパスワードが一致しません", "error")
        else:
            db.change_password(g.user["id"], new_password)
            flash("パスワードを変更しました", "info")
            return redirect(url_for("account_settings"))

    return render_template("account.html")


@app.route("/mypage")
@login_required
def mypage():
    return render_template("mypage.html")


# ---------------------------------------------------------------------------
# 開発用メールボックス(実SMTPの代わり)
# ---------------------------------------------------------------------------

@app.route("/dev/mailbox")
def dev_mailbox():
    if not app.config["DEV_MAILBOX_ENABLED"]:
        abort(404)
    email = request.args.get("email", "").strip() or None
    return render_template("dev_mailbox.html", mails=db.get_outbox(email=email), filter_email=email)


@app.route("/dev/mailbox/<int:mail_id>")
def dev_mailbox_item(mail_id):
    if not app.config["DEV_MAILBOX_ENABLED"]:
        abort(404)
    mail = db.get_outbox_item(mail_id)
    if mail is None:
        flash("メールが見つかりません", "error")
        return redirect(url_for("dev_mailbox"))
    return render_template("dev_mailbox_item.html", mail=mail)


# ---------------------------------------------------------------------------
# カレンダー(週表示・月表示)
# ---------------------------------------------------------------------------

def render_calendar(target_user, viewing_own, back_url=None):
    view = request.args.get("view", "week")
    ref_date = parse_date_param(request.args.get("date"), date.today())
    only_public = not viewing_own
    friend_progress = None if viewing_own else db.get_friend_public_progress(target_user["id"])

    if view == "month":
        weeks = cu.build_month_grid(target_user["id"], ref_date.year, ref_date.month, only_public=only_public)
        month_start = date(ref_date.year, ref_date.month, 1)
        prev_month_ref = (month_start - timedelta(days=1)).replace(day=1)
        next_month_first = date(ref_date.year + (ref_date.month == 12), (ref_date.month % 12) + 1, 1)
        month_total = db.total_seconds_for_range(
            target_user["id"],
            datetime.combine(month_start, time.min),
            datetime.combine(next_month_first, time.min),
        )
        return render_template(
            "calendar.html",
            view="month",
            weeks=weeks,
            ref_date=ref_date,
            month_label=f"{ref_date.year}年{ref_date.month}月",
            prev_url=url_for(request.endpoint, view="month", date=prev_month_ref.isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
            next_url=url_for(request.endpoint, view="month", date=next_month_first.isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
            today_url=url_for(request.endpoint, view="month", date=date.today().isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
            week_url=lambda d: url_for(request.endpoint, view="week", date=d.isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
            total_label=f"月合計 {cu.format_duration(month_total)}" if month_total else "記録なし",
            viewing_own=viewing_own,
            target_user=target_user,
            back_url=back_url,
            friend_progress=friend_progress,
            weekday_names=cu.WEEKDAY_NAMES,
            slot_label=cu.slot_label,
            category_class=cu.category_class,
            event_color_style=event_color_style,
            today=date.today(),
            switch_month_url=url_for(request.endpoint, view="month", date=ref_date.isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
            switch_week_url=url_for(request.endpoint, view="week", date=ref_date.isoformat(), **({"user_id": target_user["id"]} if not viewing_own else {})),
        )

    # week view (default)
    week_start = cu.week_start_of(ref_date)
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    # 30分の罫線は維持し、予定カードだけを15分単位で絶対配置する。
    week_events = cu.build_week_event_layout(target_user["id"], week_start, only_public=only_public)
    week_total = db.total_seconds_for_range(
        target_user["id"],
        datetime.combine(week_start, time.min),
        datetime.combine(week_start + timedelta(days=7), time.min),
    )
    now = datetime.now()
    current_slot = (now.hour * 60 + now.minute) // 30 if week_start <= now.date() < week_start + timedelta(days=7) else None

    kwargs = {} if viewing_own else {"user_id": target_user["id"]}
    return render_template(
        "calendar.html",
        view="week",
        week_events=week_events,
        week_days=week_days,
        ref_date=ref_date,
        prev_url=url_for(request.endpoint, view="week", date=(week_start - timedelta(days=7)).isoformat(), **kwargs),
        next_url=url_for(request.endpoint, view="week", date=(week_start + timedelta(days=7)).isoformat(), **kwargs),
        today_url=url_for(request.endpoint, view="week", date=date.today().isoformat(), **kwargs),
        switch_month_url=url_for(request.endpoint, view="month", date=ref_date.isoformat(), **kwargs),
        switch_week_url=url_for(request.endpoint, view="week", date=ref_date.isoformat(), **kwargs),
        total_label=f"週合計 {cu.format_duration(week_total)}" if week_total else "記録なし",
        viewing_own=viewing_own,
        target_user=target_user,
        back_url=back_url,
        friend_progress=friend_progress,
        current_slot=current_slot,
        today=date.today(),
        weekday_names=cu.WEEKDAY_NAMES,
        slots_per_day=cu.SLOTS_PER_DAY,
        slot_label=cu.slot_label,
        category_class=cu.category_class,
        event_color_style=event_color_style,
    )


@app.route("/calendar")
@login_required
def calendar_view():
    return render_calendar(g.user, viewing_own=True)


@app.route("/calendar/<int:user_id>")
@login_required
def friend_calendar_view(user_id):
    if user_id == g.user["id"]:
        return redirect(url_for("calendar_view"))
    target = db.get_user_by_id(user_id)
    if target is None or not db.are_friends(g.user["id"], user_id):
        flash("フレンドのカレンダーのみ閲覧できます", "error")
        return redirect(url_for("select_friend_calendar"))
    return render_calendar(target, viewing_own=False, back_url=url_for("calendar_view"))


# ---------------------------------------------------------------------------
# 予定の追加・編集・削除(重複警告・カテゴリ・公開範囲つき)
# ---------------------------------------------------------------------------

def event_color_style(custom_color):
    if custom_color not in EVENT_COLOR_OPTIONS:
        return ""
    red, green, blue = (int(custom_color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (red * 299 + green * 587 + blue * 114) / 1000
    text_color = "#1f2937" if luminance >= 160 else "#ffffff"
    return f"background-color: {custom_color}; border-color: {custom_color}; color: {text_color};"

def _parse_event_form():
    date_str = request.form.get("date", "")
    start_str = request.form.get("start_time", "")
    end_str = request.form.get("end_time", "")
    title = request.form.get("title", "").strip()
    memo = request.form.get("memo", "").strip()
    category = request.form.get("category", "").strip() or None
    custom_color = request.form.get("custom_color", "").strip().upper()
    if custom_color not in EVENT_COLOR_OPTIONS:
        custom_color = None
    visibility = request.form.get("visibility", "public")
    if visibility not in ("public", "private"):
        visibility = "public"

    errors = []
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_t = datetime.strptime(start_str, "%H:%M").time()
        end_t = datetime.strptime(end_str, "%H:%M").time()
        start_at = datetime.combine(d, start_t)
        end_at = datetime.combine(d, end_t)
        if end_t <= start_t:
            # 日をまたぐ入力(例: 23:00 -> 00:30)はデモでは翌日として扱う
            end_at = datetime.combine(d + timedelta(days=1), end_t)
    except ValueError:
        errors.append("日付・時刻の形式が正しくありません")
        return None, None, None, None, None, None, None, errors

    if not title:
        errors.append("内容を入力してください")

    return title, start_at, end_at, memo, category, visibility, custom_color, errors


@app.route("/events/new", methods=["GET", "POST"])
@login_required
def new_event():
    if request.method == "POST":
        title, start_at, end_at, memo, category, visibility, custom_color, errors = _parse_event_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("event_form.html", mode="new", form=request.form, presets=db.TASK_PRESETS, category_presets=db.CATEGORY_PRESETS)

        confirm = request.form.get("confirm") == "1"
        conflicts = db.find_conflicts(g.user["id"], start_at, end_at)
        if conflicts and not confirm:
            return render_template(
                "event_confirm.html",
                title=title, date=start_at.date().isoformat(),
                start_time=start_at.strftime("%H:%M"), end_time=end_at.strftime("%H:%M"),
                memo=memo, category=category or "", visibility=visibility, custom_color=custom_color or "",
                conflicts=conflicts, mode="new", event_id=None,
            )

        db.add_event(g.user["id"], title, start_at, end_at, memo=memo, source="manual", category=category, visibility=visibility, custom_color=custom_color)
        flash("予定を追加しました", "info")
        return redirect(url_for("calendar_view", view="week", date=start_at.date().isoformat()))

    prefill = {
        "date": date.today().isoformat(),
        "start_time": "09:00",
        "end_time": "09:30",
        "title": "",
        "memo": "",
        "category": "",
        "custom_color": "#3B82F6",
        "visibility": "public",
    }
    query_date = request.args.get("date")
    query_start = request.args.get("start")
    query_end = request.args.get("end")
    try:
        if query_date:
            prefill["date"] = date.fromisoformat(query_date).isoformat()
        if query_start:
            datetime.strptime(query_start, "%H:%M")
            prefill["start_time"] = query_start
        if query_end:
            datetime.strptime(query_end, "%H:%M")
            prefill["end_time"] = query_end
        if query_start and query_end and query_end <= query_start:
            raise ValueError
    except ValueError:
        flash("日付または時刻の形式が正しくありません", "error")
        prefill["start_time"] = "09:00"
        prefill["end_time"] = "09:30"
    return render_template("event_form.html", mode="new", form=prefill, presets=db.TASK_PRESETS, category_presets=db.CATEGORY_PRESETS)


@app.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
def edit_event(event_id):
    ev = db.get_event(event_id)
    if ev is None or ev["user_id"] != g.user["id"]:
        flash("予定が見つかりません", "error")
        return redirect(url_for("calendar_view"))

    if request.method == "POST":
        title, start_at, end_at, memo, category, visibility, custom_color, errors = _parse_event_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("event_form.html", mode="edit", form=request.form, presets=db.TASK_PRESETS, category_presets=db.CATEGORY_PRESETS, event_id=event_id)

        confirm = request.form.get("confirm") == "1"
        conflicts = db.find_conflicts(g.user["id"], start_at, end_at, exclude_id=event_id)
        if conflicts and not confirm:
            return render_template(
                "event_confirm.html",
                title=title, date=start_at.date().isoformat(),
                start_time=start_at.strftime("%H:%M"), end_time=end_at.strftime("%H:%M"),
                memo=memo, category=category or "", visibility=visibility, custom_color=custom_color or "",
                conflicts=conflicts, mode="edit", event_id=event_id,
            )

        db.update_event(event_id, title, start_at, end_at, memo=memo, category=category, visibility=visibility, custom_color=custom_color)
        flash("予定を更新しました", "info")
        return redirect(url_for("calendar_view", view="week", date=start_at.date().isoformat()))

    start_dt = db.parse_dt(ev["start_at"])
    end_dt = db.parse_dt(ev["end_at"])
    form = {
        "date": start_dt.date().isoformat(),
        "start_time": start_dt.strftime("%H:%M"),
        "end_time": end_dt.strftime("%H:%M"),
        "title": ev["title"],
        "memo": ev["memo"] or "",
        "category": ev["category"] or "",
        "custom_color": ev["custom_color"] or "",
        "visibility": ev["visibility"],
    }
    return render_template("event_form.html", mode="edit", form=form, presets=db.TASK_PRESETS, category_presets=db.CATEGORY_PRESETS, event_id=event_id)


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id):
    ev = db.get_event(event_id)
    if ev is None or ev["user_id"] != g.user["id"]:
        flash("予定が見つかりません", "error")
        return redirect(url_for("calendar_view"))
    ref = db.parse_dt(ev["start_at"]).date().isoformat()
    db.delete_event(event_id)
    flash("予定を削除しました", "info")
    return redirect(url_for("calendar_view", view="week", date=ref))


# ---------------------------------------------------------------------------
# タイマー(停止時の進捗表示つき)
# ---------------------------------------------------------------------------

@app.route("/timer", methods=["GET"])
@login_required
def timer_view():
    active = db.get_active_timer(g.user["id"])
    return render_template("timer.html", active=active, presets=db.TASK_PRESETS)


@app.route("/timer/start", methods=["POST"])
@login_required
def timer_start():
    if db.get_active_timer(g.user["id"]) is not None:
        flash("既にタイマーが動作中です", "error")
        return redirect(url_for("timer_view"))
    task = request.form.get("task", "").strip()
    if not task:
        flash("内容を選択または入力してください", "error")
        return redirect(url_for("timer_view"))
    db.start_timer(g.user["id"], task)
    return redirect(url_for("timer_view"))


@app.route("/timer/stop", methods=["POST"])
@login_required
def timer_stop():
    new_id = db.stop_timer(g.user["id"])
    if new_id is None:
        flash("計測中のタイマーがありません", "error")
        return redirect(url_for("timer_view"))
    return redirect(url_for("timer_stopped", event_id=new_id))


@app.route("/timer/stopped/<int:event_id>")
@login_required
def timer_stopped(event_id):
    ev = db.get_event(event_id)
    if ev is None or ev["user_id"] != g.user["id"]:
        return redirect(url_for("timer_view"))
    progress = db.compute_progress(g.user["id"])
    return render_template("timer_stopped.html", event=ev, progress=progress, duration=cu.format_duration(
        (db.parse_dt(ev["end_at"]) - db.parse_dt(ev["start_at"])).total_seconds()
    ))


# ---------------------------------------------------------------------------
# 進捗
# ---------------------------------------------------------------------------

@app.route("/goals", methods=["GET", "POST"])
@login_required
def goals_view():
    if request.method == "POST":
        period = request.form.get("period")
        if period not in ("week", "month"):
            flash("不正なリクエストです", "error")
            return redirect(url_for("goals_view"))
        rate_value = request.form.get("achievement_rate", "").strip()
        is_public = request.form.get("is_public") == "1"
        try:
            achievement_rate = int(rate_value)
            if achievement_rate < 0 or achievement_rate > 999:
                raise ValueError
        except ValueError:
            flash("達成率は0〜999の整数で入力してください", "error")
            return redirect(url_for("goals_view"))
        db.set_progress(g.user["id"], period, achievement_rate, is_public)
        flash(("週" if period == "week" else "月") + "の進捗を保存しました", "info")
        return redirect(url_for("goals_view"))

    progress = db.compute_progress(g.user["id"])
    return render_template("goals.html", progress=progress)


@app.route("/goals/<period>/delete", methods=["POST"])
@login_required
def goals_delete(period):
    if period in ("week", "month"):
        db.delete_goal(g.user["id"], period)
        flash("目標を削除しました", "info")
    return redirect(url_for("goals_view"))


# ---------------------------------------------------------------------------
# 名前付き目標の進捗
# ---------------------------------------------------------------------------

def _parse_progress_goal_form(form):
    title = form.get("title", "").strip()
    description = form.get("description", "").strip()
    deadline = form.get("deadline", "").strip()
    is_public = form.get("is_public") == "1"
    errors = []
    if not title:
        errors.append("目標名を入力してください")
    if deadline:
        try:
            date.fromisoformat(deadline)
        except ValueError:
            errors.append("期限は正しい日付で入力してください")
    return title, description, deadline or None, is_public, errors


@app.route("/progress")
@login_required
def progress_goals_view():
    return render_template("progress_list.html", goals=db.get_progress_goals(g.user["id"]))


@app.route("/progress/new", methods=["GET", "POST"])
@login_required
def progress_goal_new():
    if request.method == "POST":
        title, description, deadline, is_public, errors = _parse_progress_goal_form(request.form)
        try:
            rate = int(request.form.get("progress_rate", "0"))
            if not 0 <= rate <= 100:
                raise ValueError
        except ValueError:
            errors.append("初期進捗は0〜100の整数で入力してください")
            rate = 0
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("progress_form.html", mode="new", form=request.form, goal_id=None)
        goal_id = db.create_progress_goal(g.user["id"], title, description, rate, deadline, is_public)
        flash("目標を登録しました", "info")
        return redirect(url_for("progress_goal_detail", goal_id=goal_id))
    return render_template("progress_form.html", mode="new", form={"title": "", "description": "", "deadline": "", "progress_rate": 0, "is_public": False}, goal_id=None)


@app.route("/progress/<int:goal_id>")
@login_required
def progress_goal_detail(goal_id):
    goal = db.get_progress_goal(goal_id, g.user["id"])
    if goal is None:
        flash("目標が見つかりません", "error")
        return redirect(url_for("progress_goals_view"))
    return render_template("progress_detail.html", goal=goal, updates=db.get_progress_updates(goal_id, g.user["id"]))


@app.route("/progress/<int:goal_id>/update", methods=["POST"])
@login_required
def progress_goal_update_rate(goal_id):
    try:
        rate = int(request.form.get("progress_rate", ""))
        if not 0 <= rate <= 100:
            raise ValueError
    except ValueError:
        flash("進捗は0〜100の整数で入力してください", "error")
        return redirect(url_for("progress_goal_detail", goal_id=goal_id))
    if not db.update_progress_rate(goal_id, g.user["id"], rate, request.form.get("note", "").strip()):
        flash("目標が見つかりません", "error")
        return redirect(url_for("progress_goals_view"))
    flash("進捗を登録しました", "info")
    return redirect(url_for("progress_goal_detail", goal_id=goal_id))


@app.route("/progress/<int:goal_id>/edit", methods=["GET", "POST"])
@login_required
def progress_goal_edit(goal_id):
    goal = db.get_progress_goal(goal_id, g.user["id"])
    if goal is None:
        flash("目標が見つかりません", "error")
        return redirect(url_for("progress_goals_view"))
    if request.method == "POST":
        title, description, deadline, is_public, errors = _parse_progress_goal_form(request.form)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("progress_form.html", mode="edit", form=request.form, goal_id=goal_id)
        db.update_progress_goal(goal_id, g.user["id"], title, description, deadline, is_public)
        flash("目標を更新しました", "info")
        return redirect(url_for("progress_goal_detail", goal_id=goal_id))
    return render_template("progress_form.html", mode="edit", form=goal, goal_id=goal_id)


@app.route("/progress/<int:goal_id>/delete", methods=["POST"])
@login_required
def progress_goal_delete(goal_id):
    if db.delete_progress_goal(goal_id, g.user["id"]):
        flash("目標を削除しました", "info")
    else:
        flash("目標が見つかりません", "error")
    return redirect(url_for("progress_goals_view"))


@app.route("/progress/friend/<int:user_id>")
@login_required
def friend_progress_goals_view(user_id):
    if user_id == g.user["id"]:
        return redirect(url_for("progress_goals_view"))
    friend = db.get_user_by_id(user_id)
    if friend is None or not db.are_friends(g.user["id"], user_id):
        flash("フレンドの公開目標のみ閲覧できます", "error")
        return redirect(url_for("friends_view"))
    return render_template("friend_progress.html", friend=friend, goals=db.get_public_progress_goals(user_id))


# ---------------------------------------------------------------------------
# 集計(カテゴリ別)
# ---------------------------------------------------------------------------

@app.route("/reports")
@login_required
def reports_view():
    period = request.args.get("period", "week")
    if period not in ("week", "month"):
        period = "week"
    today = date.today()
    if period == "week":
        p_start = cu.week_start_of(today)
        p_end = p_start + timedelta(days=7)
        label = f"{p_start.isoformat()} 〜 {(p_end - timedelta(days=1)).isoformat()}"
    else:
        p_start = today.replace(day=1)
        p_end = date(p_start.year + (p_start.month == 12), (p_start.month % 12) + 1, 1)
        label = f"{p_start.year}年{p_start.month}月"

    items = db.category_totals_for_range(
        g.user["id"], datetime.combine(p_start, time.min), datetime.combine(p_end, time.min)
    )
    total = sum(i["seconds"] for i in items)
    for i in items:
        i["duration_label"] = cu.format_duration(i["seconds"])
        i["pct"] = round(i["seconds"] / total * 100) if total else 0

    return render_template("reports.html", period=period, label=label, items=items, total_label=cu.format_duration(total) if total else "記録なし")


# ---------------------------------------------------------------------------
# アプリ内通知
# ---------------------------------------------------------------------------

@app.route("/notifications")
@login_required
def notifications_view():
    items = db.get_notifications(g.user["id"])
    db.mark_notifications_read(g.user["id"])
    return render_template("notifications.html", items=items, received=db.get_received_requests(g.user["id"]))


# ---------------------------------------------------------------------------
# フレンド
# ---------------------------------------------------------------------------

@app.route("/friends", methods=["GET"])
@login_required
def friends_view():
    friends = db.get_friends(g.user["id"])
    for f in friends:
        f["progress"] = db.get_friend_public_progress(f["user_id"])
    return render_template(
        "friends.html",
        friends=friends,
    )


@app.route("/friends/request", methods=["POST"])
@login_required
def friends_request():
    friend_code = request.form.get("friend_code", "").strip().upper()
    if len(friend_code) != db.FRIEND_CODE_LENGTH:
        flash("8桁のフレンドコードを入力してください", "error")
        return redirect(url_for("friends_view"))

    result, addressee = db.send_friend_request(g.user["id"], friend_code)
    messages = {
        "ok": ("申請を送りました", "info"),
        "not_found": ("該当するユーザーが見つかりません", "error"),
        "self": ("自分自身には申請できません", "error"),
        "already_friends": ("既にフレンドです", "error"),
        "already_pending": ("既に申請中です", "error"),
    }
    msg, category = messages.get(result, ("エラーが発生しました", "error"))
    flash(msg, category)

    if result == "ok" and addressee:
        db.create_notification(
            addressee["id"], "friend_request",
            f"{g.user['display_name']} さんからフレンド申請が届いています", url_for("friends_view"),
        )
        db.send_mock_mail(
            addressee["email"],
            "【タイムラインカレンダー】フレンド申請が届いています",
            f"{addressee['display_name']} 様\n\n{g.user['display_name']} さんからフレンド申請が届いています。"
            f"アプリにログインして確認してください。\n",
        )
    return redirect(url_for("friends_view"))


@app.route("/friends/<int:friendship_id>/accept", methods=["POST"])
@login_required
def friends_accept(friendship_id):
    ok, requester_id = db.respond_to_request(friendship_id, g.user["id"], accept=True)
    if ok:
        flash("フレンドになりました", "info")
        requester = db.get_user_by_id(requester_id)
        if requester:
            db.create_notification(
                requester_id, "friend_accept",
                f"{g.user['display_name']} さんとフレンドになりました", url_for("friends_view"),
            )
            db.send_mock_mail(
                requester["email"],
                "【タイムラインカレンダー】フレンド申請が承認されました",
                f"{requester['display_name']} 様\n\n{g.user['display_name']} さんがフレンド申請を承認しました。\n",
            )
    else:
        flash("処理できませんでした", "error")
    return redirect(url_for("friends_view"))


@app.route("/friends/<int:friendship_id>/decline", methods=["POST"])
@login_required
def friends_decline(friendship_id):
    ok, requester_id = db.respond_to_request(friendship_id, g.user["id"], accept=False)
    if ok:
        flash("申請を拒否しました", "info")
        if requester_id:
            db.create_notification(
                requester_id, "friend_decline",
                f"{g.user['display_name']} さんへのフレンド申請は承認されませんでした", url_for("friends_view"),
            )
    else:
        flash("処理できませんでした", "error")
    return redirect(url_for("friends_view"))


@app.route("/friends/<int:friendship_id>/remove", methods=["POST"])
@login_required
def friends_remove(friendship_id):
    if db.cancel_or_remove_friendship(friendship_id, g.user["id"]):
        flash("フレンドを解除(または申請を取り消し)しました", "info")
    else:
        flash("処理できませんでした", "error")
    return redirect(url_for("friends_view"))


@app.route("/friends/select-calendar")
@login_required
def select_friend_calendar():
    return render_template("select_friend.html", friends=db.get_friends(g.user["id"]))


if __name__ == "__main__":
    db.init_db()
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=env_flag("FLASK_DEBUG"),
    )
