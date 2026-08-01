"""
calendar_utils.py
------------------
週表示・月表示のグリッドをDBのイベント一覧から組み立てるヘルパー。
tkinter版の slots_for_range と同じ考え方をWeb版(datetime1本持ち)向けに書き直したもの。
"""

from datetime import datetime, date, time, timedelta

import db

SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES  # 48
WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def slot_label(idx):
    h, m = divmod(idx * SLOT_MINUTES, 60)
    return f"{h:02d}:{m:02d}"


def week_start_of(d):
    """dを含む週の月曜日(date)を返す"""
    return d - timedelta(days=d.weekday())


def build_week_grid(user_id, week_start_date, only_public=False):
    """
    戻り値: grid[day_offset(0-6)][slot_idx(0-47)] = そのマスに重なるイベントのリスト
    """
    week_start_dt = datetime.combine(week_start_date, time.min)
    week_end_dt = week_start_dt + timedelta(days=7)
    events = db.get_events_range(user_id, week_start_dt, week_end_dt, only_public=only_public)
    parsed = [(db.parse_dt(e["start_at"]), db.parse_dt(e["end_at"]), e) for e in events]

    grid = []
    for day_offset in range(7):
        day_start = week_start_dt + timedelta(days=day_offset)
        day_slots = []
        for slot in range(SLOTS_PER_DAY):
            slot_start = day_start + timedelta(minutes=SLOT_MINUTES * slot)
            slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
            acts = [ev for (s, e, ev) in parsed if s < slot_end and e > slot_start]
            day_slots.append(acts)
        grid.append(day_slots)
    return grid


def build_month_grid(user_id, year, month, only_public=False):
    """
    戻り値: weeks(週のリスト)。各週は7要素のリストで、各要素は
    {"date": date, "in_month": bool, "events": [event,...]}
    """
    first_of_month = date(year, month, 1)
    grid_start = week_start_of(first_of_month)

    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_of_month = next_month_first - timedelta(days=1)
    grid_end = last_of_month + timedelta(days=(6 - last_of_month.weekday()))

    num_days = (grid_end - grid_start).days + 1
    num_weeks = num_days // 7

    range_start_dt = datetime.combine(grid_start, time.min)
    range_end_dt = datetime.combine(grid_end + timedelta(days=1), time.min)
    events = db.get_events_range(user_id, range_start_dt, range_end_dt, only_public=only_public)
    parsed = [(db.parse_dt(e["start_at"]), db.parse_dt(e["end_at"]), e) for e in events]

    weeks = []
    cur_date = grid_start
    for _ in range(num_weeks):
        week_cells = []
        for _ in range(7):
            day_start = datetime.combine(cur_date, time.min)
            day_end = day_start + timedelta(days=1)
            day_events = [ev for (s, e, ev) in parsed if s < day_end and e > day_start]
            day_events.sort(key=lambda ev: ev["start_at"])
            week_cells.append({"date": cur_date, "in_month": cur_date.month == month, "events": day_events})
            cur_date += timedelta(days=1)
        weeks.append(week_cells)
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
