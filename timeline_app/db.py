"""
db.py
-----
ストップウォッチ×カレンダー(タイムライン)アプリのデータ層。
tkinter に依存しないので、単体でロジックをテストできる。

データモデル:
    activities テーブル
        id         INTEGER PRIMARY KEY AUTOINCREMENT
        day        TEXT  'YYYY-MM-DD'
        start_time TEXT  'HH:MM' (24時間表記, 00:00〜23:59)
        end_time   TEXT  'HH:MM' (24時間表記, 00:01〜24:00. 24:00は「その日の終わり」を表す)
        task       TEXT  やったこと
        seconds    INTEGER 実際にかかった秒数(タイマー計測時は実測値、手動追加時は差分から計算)
        source     TEXT  'timer' または 'manual'

30分刻みのタイムライン(0:00〜23:30、48枠)は start_time/end_time から
動的に計算するので、レコードは「重複なし・1アクティビティ1行」で保持する。
"""

import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timeline.db")

SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES  # 48

TASK_PRESETS = ["勉強", "筋トレ", "作業", "読書"]


def get_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=DB_PATH):
    conn = get_connection(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            task TEXT NOT NULL,
            seconds INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_day ON activities(day)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 時刻 <-> 分 <-> 30分スロット の変換ユーティリティ
# ---------------------------------------------------------------------------

def hhmm_to_minutes(hhmm):
    """'HH:MM' -> 0〜1440 の分(24:00 は 1440)"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def minutes_to_hhmm(minutes):
    """0〜1440 の分 -> 'HH:MM'(1440 は '24:00')"""
    minutes = max(0, min(1440, int(minutes)))
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def slot_index_from_minutes(minutes):
    """分 -> 0〜47 のスロット番号"""
    minutes = max(0, min(1439, int(minutes)))
    return minutes // SLOT_MINUTES


def slot_label(index):
    """スロット番号 -> '9:00' のような表示用ラベル"""
    total_minutes = index * SLOT_MINUTES
    h, m = divmod(total_minutes, 60)
    return f"{h:2d}:{m:02d}"


def slot_start_hhmm(index):
    total_minutes = index * SLOT_MINUTES
    return minutes_to_hhmm(total_minutes)


def slots_for_range(start_hhmm, end_hhmm):
    """start〜end (同日内, end > start) が重なる30分スロット番号のリストを返す"""
    start_m = hhmm_to_minutes(start_hhmm)
    end_m = hhmm_to_minutes(end_hhmm)
    if end_m <= start_m:
        end_m = start_m + 1  # 最低1分は占有させる(ゼロ長対策)
    first = slot_index_from_minutes(start_m)
    last = slot_index_from_minutes(max(start_m, end_m - 1))
    return list(range(first, last + 1))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_activity(day, start_time, end_time, task, seconds=None, source="manual", db_path=DB_PATH):
    """
    1件のアクティビティを追加する。day を跨ぐ場合(end_time <= start_time)は
    自動的に「当日の23:59まで」と「翌日の0:00から」の2件に分割して保存する。
    戻り値: 作成された activity の id のリスト
    """
    if seconds is None:
        seconds = max(0, hhmm_to_minutes(end_time) - hhmm_to_minutes(start_time)) * 60

    start_m = hhmm_to_minutes(start_time)
    end_m = hhmm_to_minutes(end_time)

    conn = get_connection(db_path)
    created_ids = []
    try:
        if end_m >= start_m:
            # 同日内(タイマーが1分未満で終わり start/end の分表記が
            # 一致するケースも含む)。実際の秒数は seconds 引数をそのまま使う。
            cur = conn.execute(
                "INSERT INTO activities (day, start_time, end_time, task, seconds, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (day, start_time, end_time, task, seconds, source),
            )
            created_ids.append(cur.lastrowid)
        else:
            # 日をまたぐケース: 当日分 + 翌日分に分割
            day1_seconds = (1440 - start_m) * 60
            day2_seconds = max(0, seconds - day1_seconds)
            cur = conn.execute(
                "INSERT INTO activities (day, start_time, end_time, task, seconds, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (day, start_time, "24:00", task, day1_seconds, source),
            )
            created_ids.append(cur.lastrowid)

            next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            cur = conn.execute(
                "INSERT INTO activities (day, start_time, end_time, task, seconds, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (next_day, "00:00", end_time, task, day2_seconds, source),
            )
            created_ids.append(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return created_ids


def get_activities_for_day(day, db_path=DB_PATH):
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM activities WHERE day = ? ORDER BY start_time", (day,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_activity(activity_id, db_path=DB_PATH):
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_activity(activity_id, task=None, start_time=None, end_time=None, db_path=DB_PATH):
    current = get_activity(activity_id, db_path=db_path)
    if current is None:
        raise ValueError(f"activity {activity_id} not found")

    new_task = task if task is not None else current["task"]
    new_start = start_time if start_time is not None else current["start_time"]
    new_end = end_time if end_time is not None else current["end_time"]
    if new_start == current["start_time"] and new_end == current["end_time"]:
        # 時刻が変わっていなければ、タイマー計測の秒単位の精度をそのまま保持する
        new_seconds = current["seconds"]
    else:
        new_seconds = max(0, hhmm_to_minutes(new_end) - hhmm_to_minutes(new_start)) * 60

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE activities SET task = ?, start_time = ?, end_time = ?, seconds = ? WHERE id = ?",
            (new_task, new_start, new_end, new_seconds, activity_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_activity(activity_id, db_path=DB_PATH):
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
        conn.commit()
    finally:
        conn.close()


def get_slot_activities(day, slot_index, db_path=DB_PATH):
    """指定した日・スロットに重なっているアクティビティ一覧"""
    activities = get_activities_for_day(day, db_path=db_path)
    result = []
    for a in activities:
        if slot_index in slots_for_range(a["start_time"], a["end_time"]):
            result.append(a)
    return result


def build_timeline(day, db_path=DB_PATH):
    """{slot_index: [activity, ...]} の辞書を返す(48枠すべて)"""
    timeline = {i: [] for i in range(SLOTS_PER_DAY)}
    for a in get_activities_for_day(day, db_path=db_path):
        for idx in slots_for_range(a["start_time"], a["end_time"]):
            if 0 <= idx < SLOTS_PER_DAY:
                timeline[idx].append(a)
    return timeline


def total_seconds_for_day(day, db_path=DB_PATH):
    return sum(a["seconds"] for a in get_activities_for_day(day, db_path=db_path))
