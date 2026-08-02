"""カレンダー表示用のグリッド・表示位置ヘルパー。"""

from datetime import datetime, date, time, timedelta

import db

SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES  # 48。罫線と時刻ラベルは30分単位のまま。
WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def category_class(category):
    """DBのカテゴリ名を表示専用の安全なCSSクラス名へ変換する。"""
    normalized = " ".join(str(category or "").strip().split()).lower()
    if not normalized:
        return "default"

    # 既存プリセット（学習・仕事・運動）と表記揺れも含めて吸収する。
    keyword_classes = (
        (("筋トレ", "運動", "workout", "training"), "workout"),
        (("勉強", "学習", "study"), "study"),
        (("作業", "開発", "仕事", "work", "development"), "work"),
        (("授業", "講義", "class", "lecture"), "class"),
        (("アルバイト", "バイト", "parttime", "part-time"), "parttime"),
        (("休憩", "休み", "break", "rest"), "break"),
    )
    for keywords, css_class in keyword_classes:
        if any(keyword in normalized for keyword in keywords):
            return css_class
    return "other"


def slot_label(idx):
    h, m = divmod(idx * SLOT_MINUTES, 60)
    return f"{h:02d}:{m:02d}"


def week_start_of(d):
    return d - timedelta(days=d.weekday())


def round_duration_to_15(minutes):
    """表示用の長さだけを、最も近い15分へ丸める。"""
    if minutes <= 0:
        return 0
    return max(15, ((minutes + 7) // 15) * 15)


def round_datetime_to_15(value):
    """表示用に日時を最も近い15分へ丸める（日付またぎ対応）。"""
    day_start = datetime.combine(value.date(), time.min)
    minutes = value.hour * 60 + value.minute
    rounded_minutes = ((minutes + 7) // 15) * 15
    return day_start + timedelta(minutes=rounded_minutes)


def _duration_minutes_rounded(start_at, end_at):
    # 秒を含むタイマー記録でも、1分未満が表示上0分にならないよう切り上げる。
    seconds = (end_at - start_at).total_seconds()
    if seconds <= 0:
        return 0
    minutes = max(1, int((seconds + 59) // 60))
    return round_duration_to_15(minutes)


def build_week_event_layout(user_id, week_start_date, only_public=False):
    """週表示で絶対配置するイベント断片を日別に返す。

    戻り値: events[day_offset] = [{event, display_top, display_duration}, ...]
    display_top / display_duration は午前0時からの表示用分数。DBの日時は変更しない。
    """
    week_start_dt = datetime.combine(week_start_date, time.min)
    week_end_dt = week_start_dt + timedelta(days=7)
    events = db.get_events_range(user_id, week_start_dt, week_end_dt, only_public=only_public)
    layout = [[] for _ in range(7)]

    for event in events:
        start_at = db.parse_dt(event["start_at"])
        end_at = db.parse_dt(event["end_at"])
        duration = _duration_minutes_rounded(start_at, end_at)
        if not duration:
            continue

        display_start = round_datetime_to_15(start_at)
        display_end = display_start + timedelta(minutes=duration)
        first_day = max(0, (display_start.date() - week_start_date).days)
        last_day = min(6, (display_end - timedelta(microseconds=1)).date().toordinal() - week_start_date.toordinal())

        for day_offset in range(first_day, last_day + 1):
            day_start = week_start_dt + timedelta(days=day_offset)
            day_end = day_start + timedelta(days=1)
            fragment_start = max(display_start, day_start)
            fragment_end = min(display_end, day_end)
            if fragment_end <= fragment_start:
                continue
            layout[day_offset].append({
                "event": event,
                "display_top": int((fragment_start - day_start).total_seconds() // 60),
                "display_duration": int((fragment_end - fragment_start).total_seconds() // 60),
            })

    for day_events in layout:
        day_events.sort(key=lambda item: (item["display_top"], item["event"]["start_at"]))
    return layout


def build_week_grid(user_id, week_start_date, only_public=False):
    """旧週グリッド互換用。30分セルに重なるイベント一覧を返す。"""
    week_start_dt = datetime.combine(week_start_date, time.min)
    week_end_dt = week_start_dt + timedelta(days=7)
    events = db.get_events_range(user_id, week_start_dt, week_end_dt, only_public=only_public)
    parsed = [(db.parse_dt(e["start_at"]), db.parse_dt(e["end_at"]), e) for e in events]
    grid = []
    for day_offset in range(7):
        day_start = week_start_dt + timedelta(days=day_offset)
        grid.append([
            [ev for s, e, ev in parsed if s < day_start + timedelta(minutes=SLOT_MINUTES * (slot + 1)) and e > day_start + timedelta(minutes=SLOT_MINUTES * slot)]
            for slot in range(SLOTS_PER_DAY)
        ])
    return grid


def build_month_grid(user_id, year, month, only_public=False):
    first_of_month = date(year, month, 1)
    grid_start = week_start_of(first_of_month)
    next_month_first = date(year + (month == 12), (month % 12) + 1, 1)
    last_of_month = next_month_first - timedelta(days=1)
    grid_end = last_of_month + timedelta(days=6 - last_of_month.weekday())
    events = db.get_events_range(user_id, datetime.combine(grid_start, time.min), datetime.combine(grid_end + timedelta(days=1), time.min), only_public=only_public)
    parsed = [(db.parse_dt(e["start_at"]), db.parse_dt(e["end_at"]), e) for e in events]
    weeks, cur_date = [], grid_start
    for _ in range(((grid_end - grid_start).days + 1) // 7):
        week = []
        for _ in range(7):
            day_start = datetime.combine(cur_date, time.min)
            day_end = day_start + timedelta(days=1)
            day_events = [ev for s, e, ev in parsed if s < day_end and e > day_start]
            day_events.sort(key=lambda ev: ev["start_at"])
            week.append({"date": cur_date, "in_month": cur_date.month == month, "events": day_events})
            cur_date += timedelta(days=1)
        weeks.append(week)
    return weeks


def format_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m}分"
    if m:
        return f"{m}分{s}秒" if s else f"{m}分"
    return f"{s}秒"
