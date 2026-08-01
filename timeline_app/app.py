"""
ストップウォッチ×カレンダー タイムラインアプリ (PC版プロトタイプ)
================================================================
* 0:00〜23:30 を30分刻みのタイムラインで表示
* タイマーで計測 → 自動でその日のタイムラインに記録
* タスクは「プリセットから選ぶ」か「自由入力する」かを1つのコンボボックスで両対応
  (ドロップダウンから選んでもいいし、直接好きな文字を入力してもいい)
* 過去の日付を選んで記録を後から編集・削除・手動追加できる

実行方法:
    python3 app.py
    (Windows/Mac 標準の Python には tkinter が同梱されています)
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

import db


DATE_FMT = "%Y-%m-%d"


def today_str():
    return datetime.now().strftime(DATE_FMT)


def parse_date(s):
    return datetime.strptime(s, DATE_FMT)


def valid_hhmm(s):
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        return 0 <= h <= 24 and 0 <= m < 60 and not (h == 24 and m != 0)
    except Exception:
        return False


def format_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m}分"
    if m:
        return f"{m}分{s}秒" if s else f"{m}分"
    return f"{s}秒"


# ---------------------------------------------------------------------------
# タスク入力欄: プリセット選択 + 自由入力を1つのウィジェットで両対応
# ---------------------------------------------------------------------------

def make_task_combobox(parent, initial=""):
    cb = ttk.Combobox(parent, values=db.TASK_PRESETS, state="normal", width=24)
    cb.set(initial)
    return cb


# ---------------------------------------------------------------------------
# メインウィンドウ
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = "月火水木金土日"


class TimelineApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("タイムライン - ストップウォッチ×カレンダー")
        self.geometry("900x760")
        self.minsize(640, 480)

        db.init_db()

        self.current_day = today_str()
        self.view_mode = "day"  # 'day' または 'week'
        self.timer_window = None

        self._build_top_bar()
        self._build_timeline_area()
        self._build_bottom_bar()

        self.refresh_timeline()
        # 現在時刻ハイライトを1分ごとに更新
        self.after(60_000, self._tick)

    # -- 上部: 日付ナビゲーション --------------------------------------

    def _build_top_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")

        self.prev_btn = ttk.Button(bar, text="◀ 前日", command=self.go_prev)
        self.prev_btn.pack(side="left")

        self.date_var = tk.StringVar()
        date_entry = ttk.Entry(bar, textvariable=self.date_var, width=16, justify="center")
        date_entry.pack(side="left", padx=6)
        date_entry.bind("<Return>", lambda e: self.go_to_typed_date())
        ttk.Button(bar, text="移動", command=self.go_to_typed_date).pack(side="left")

        self.next_btn = ttk.Button(bar, text="翌日 ▶", command=self.go_next)
        self.next_btn.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="今日", command=self.go_today).pack(side="left", padx=(12, 0))

        self.day_view_btn = ttk.Button(bar, text="日表示", command=lambda: self.set_view_mode("day"))
        self.day_view_btn.pack(side="left", padx=(16, 0))
        self.week_view_btn = ttk.Button(bar, text="週表示", command=lambda: self.set_view_mode("week"))
        self.week_view_btn.pack(side="left", padx=(4, 0))
        self.day_view_btn.state(["disabled"])  # 初期表示は日表示

        self.total_label = ttk.Label(bar, text="", anchor="e")
        self.total_label.pack(side="right")

    def set_view_mode(self, mode):
        if mode == self.view_mode:
            return
        self.view_mode = mode
        if mode == "week":
            self.prev_btn.config(text="◀ 前週")
            self.next_btn.config(text="翌週 ▶")
            self.day_view_btn.state(["!disabled"])
            self.week_view_btn.state(["disabled"])
        else:
            self.prev_btn.config(text="◀ 前日")
            self.next_btn.config(text="翌日 ▶")
            self.day_view_btn.state(["disabled"])
            self.week_view_btn.state(["!disabled"])
        self.refresh_timeline()

    def _set_date_var(self):
        d = parse_date(self.current_day)
        if self.view_mode == "week":
            week_start = d - timedelta(days=d.weekday())
            week_end = week_start + timedelta(days=6)
            self.date_var.set(f"{week_start.strftime(DATE_FMT)} 〜 {week_end.strftime(DATE_FMT)}")
        else:
            weekday = WEEKDAY_NAMES[d.weekday()]
            self.date_var.set(f"{self.current_day} ({weekday})")

    def go_prev(self):
        delta = 7 if self.view_mode == "week" else 1
        d = parse_date(self.current_day) - timedelta(days=delta)
        self.current_day = d.strftime(DATE_FMT)
        self.refresh_timeline()

    def go_next(self):
        delta = 7 if self.view_mode == "week" else 1
        d = parse_date(self.current_day) + timedelta(days=delta)
        self.current_day = d.strftime(DATE_FMT)
        self.refresh_timeline()

    def go_today(self):
        self.current_day = today_str()
        self.refresh_timeline()

    def go_to_typed_date(self):
        raw = self.date_var.get().split(" ")[0].strip()
        try:
            d = parse_date(raw)
            self.current_day = d.strftime(DATE_FMT)
            self.refresh_timeline()
        except ValueError:
            messagebox.showerror("日付エラー", "YYYY-MM-DD の形式で入力してください(例: 2026-07-31)")
            self._set_date_var()

    # -- 中央: タイムライン ----------------------------------------------

    def _build_timeline_area(self):
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.rows_frame = ttk.Frame(self.canvas)

        self.rows_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        # rows_frame の幅をキャンバスの表示幅に合わせる(週表示のグリッドが横いっぱいに広がるように)
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width)
        )

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)  # Windows/Mac
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))  # Linux
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _build_bottom_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="▶ タイマー開始", command=self.open_timer).pack(side="left")
        ttk.Button(bar, text="＋ 手動追加", command=lambda: self.open_manual_add()).pack(
            side="left", padx=(8, 0)
        )

    # -- タイムライン描画 --------------------------------------------------

    def refresh_timeline(self):
        self._set_date_var()

        for w in self.rows_frame.winfo_children():
            w.destroy()

        if self.view_mode == "week":
            self._render_week_view()
        else:
            self._render_day_view()

    def _render_day_view(self):
        timeline = db.build_timeline(self.current_day)
        now = datetime.now()
        current_slot = None
        if self.current_day == today_str():
            current_slot = db.slot_index_from_minutes(now.hour * 60 + now.minute)

        for idx in range(db.SLOTS_PER_DAY):
            acts = timeline[idx]
            row_bg = "#ffffff"
            if idx == current_slot:
                row_bg = "#fff3cd"
            elif acts:
                row_bg = "#dbeeff"

            row = tk.Frame(self.rows_frame, bg=row_bg, cursor="hand2")
            row.pack(fill="x", pady=1)

            time_lbl = tk.Label(
                row, text=db.slot_label(idx), width=6, anchor="w", bg=row_bg, font=("TkDefaultFont", 10, "bold")
            )
            time_lbl.pack(side="left", padx=(4, 8), pady=3)

            if acts:
                text = " / ".join(f"{a['task']}({format_duration(a['seconds'])})" for a in acts)
            else:
                text = ""
            content_lbl = tk.Label(row, text=text, anchor="w", bg=row_bg, justify="left")
            content_lbl.pack(side="left", fill="x", expand=True, pady=3)

            for widget in (row, time_lbl, content_lbl):
                widget.bind("<Button-1>", lambda e, i=idx: self.open_slot_dialog(i))

        total = db.total_seconds_for_day(self.current_day)
        self.total_label.config(text=f"合計 {format_duration(total)}" if total else "記録なし")

    def _render_week_view(self):
        anchor = parse_date(self.current_day)
        week_start = anchor - timedelta(days=anchor.weekday())
        week_dates = [week_start + timedelta(days=i) for i in range(7)]
        week_day_strs = [d.strftime(DATE_FMT) for d in week_dates]
        today = today_str()

        self.rows_frame.grid_columnconfigure(0, weight=0, minsize=44)
        for c in range(1, 8):
            self.rows_frame.grid_columnconfigure(c, weight=1, uniform="day_col")

        # ヘッダー行: 日付+曜日。クリックすると日表示でその日を開く
        tk.Label(self.rows_frame, text="", bg="#eeeeee").grid(row=0, column=0, sticky="nsew")
        for c, d in enumerate(week_dates):
            ds = d.strftime(DATE_FMT)
            header_bg = "#fff3cd" if ds == today else "#eeeeee"
            label_text = f"{d.month}/{d.day}({WEEKDAY_NAMES[d.weekday()]})"
            hdr = tk.Label(
                self.rows_frame, text=label_text, bg=header_bg, cursor="hand2",
                font=("TkDefaultFont", 9, "bold"),
            )
            hdr.grid(row=0, column=c + 1, sticky="nsew", padx=1, pady=1)
            hdr.bind("<Button-1>", lambda e, day=ds: self._jump_to_day(day))

        now = datetime.now()
        current_slot = db.slot_index_from_minutes(now.hour * 60 + now.minute)
        timelines = {ds: db.build_timeline(ds) for ds in week_day_strs}

        for idx in range(db.SLOTS_PER_DAY):
            time_lbl = tk.Label(
                self.rows_frame, text=db.slot_label(idx), bg="#f7f7f7", font=("TkDefaultFont", 8)
            )
            time_lbl.grid(row=idx + 1, column=0, sticky="nsew")

            for c, ds in enumerate(week_day_strs):
                acts = timelines[ds][idx]
                cell_bg = "#ffffff"
                if ds == today and idx == current_slot:
                    cell_bg = "#fff3cd"
                elif acts:
                    cell_bg = "#dbeeff"
                text = "\n".join(a["task"] for a in acts) if acts else ""
                cell = tk.Label(
                    self.rows_frame, text=text, bg=cell_bg, anchor="w", justify="left",
                    font=("TkDefaultFont", 8), wraplength=110, cursor="hand2",
                )
                cell.grid(row=idx + 1, column=c + 1, sticky="nsew", padx=1, pady=1)
                cell.bind("<Button-1>", lambda e, day=ds, i=idx: self.open_slot_dialog_for(day, i))

        total = sum(db.total_seconds_for_day(ds) for ds in week_day_strs)
        self.total_label.config(text=f"週合計 {format_duration(total)}" if total else "記録なし")

    def _jump_to_day(self, day):
        self.current_day = day
        self.set_view_mode("day")

    def _tick(self):
        # 表示中の日付が今日を含む場合、現在時刻ハイライトを更新するため再描画
        if self.current_day == today_str() or self.view_mode == "week":
            self.refresh_timeline()
        self.after(60_000, self._tick)

    # -- スロットクリック時のダイアログ ------------------------------------

    def open_slot_dialog(self, slot_index):
        self.open_slot_dialog_for(self.current_day, slot_index)

    def open_slot_dialog_for(self, day, slot_index):
        SlotDialog(self, day, slot_index)

    def open_manual_add(self, start_slot=None):
        ManualAddDialog(self, self.current_day, start_slot)

    def open_timer(self):
        if self.timer_window is not None and self.timer_window.winfo_exists():
            self.timer_window.lift()
            return
        self.timer_window = TimerWindow(self)


# ---------------------------------------------------------------------------
# スロットの記録一覧(編集・削除の入口)
# ---------------------------------------------------------------------------

class SlotDialog(tk.Toplevel):
    def __init__(self, app: TimelineApp, day, slot_index):
        super().__init__(app)
        self.app = app
        self.day = day
        self.slot_index = slot_index
        self.title(f"{day} {db.slot_label(slot_index).strip()} の記録")
        self.geometry("380x320")
        self.transient(app)

        ttk.Label(
            self, text=f"{day}  {db.slot_label(slot_index).strip()}〜{db.slot_label(slot_index+1).strip() if slot_index+1 < db.SLOTS_PER_DAY else '24:00'}",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(pady=(10, 4))

        self.list_frame = ttk.Frame(self)
        self.list_frame.pack(fill="both", expand=True, padx=10)

        self._render_list()

        ttk.Button(
            self, text="＋ この時間帯に追加",
            command=lambda: (self.destroy(), app.open_manual_add(start_slot=slot_index)),
        ).pack(pady=10)

    def _render_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        acts = db.get_slot_activities(self.day, self.slot_index)
        if not acts:
            ttk.Label(self.list_frame, text="この時間帯の記録はまだありません").pack(pady=20)
            return

        for a in acts:
            row = ttk.Frame(self.list_frame)
            row.pack(fill="x", pady=4)
            text = f"{a['task']}  {a['start_time']}〜{a['end_time']}  ({format_duration(a['seconds'])})"
            ttk.Label(row, text=text, wraplength=200, justify="left").pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="編集", width=6, command=lambda a=a: self._edit(a)).pack(side="left")
            ttk.Button(row, text="削除", width=6, command=lambda a=a: self._delete(a)).pack(side="left")

    def _edit(self, activity):
        EditDialog(self.app, activity, on_saved=self._refresh_all)

    def _delete(self, activity):
        if messagebox.askyesno("削除確認", f"「{activity['task']}」の記録を削除しますか？"):
            db.delete_activity(activity["id"])
            self._refresh_all()

    def _refresh_all(self):
        self.app.refresh_timeline()
        self._render_list()


# ---------------------------------------------------------------------------
# 編集ダイアログ (時刻はHH:MM自由入力 = 分単位で正確に直せる)
# ---------------------------------------------------------------------------

class EditDialog(tk.Toplevel):
    def __init__(self, app: TimelineApp, activity, on_saved=None):
        super().__init__(app)
        self.app = app
        self.activity = activity
        self.on_saved = on_saved
        self.title("記録を編集")
        self.geometry("320x220")
        self.transient(app)
        self.grab_set()

        pad = {"padx": 10, "pady": 6}

        ttk.Label(self, text="内容(選択 または 自由入力)").pack(anchor="w", **pad)
        self.task_cb = make_task_combobox(self, activity["task"])
        self.task_cb.pack(fill="x", padx=10)

        time_frame = ttk.Frame(self)
        time_frame.pack(fill="x", **pad)
        ttk.Label(time_frame, text="開始 HH:MM").grid(row=0, column=0, sticky="w")
        ttk.Label(time_frame, text="終了 HH:MM").grid(row=0, column=1, sticky="w", padx=(20, 0))

        self.start_var = tk.StringVar(value=activity["start_time"])
        self.end_var = tk.StringVar(value=activity["end_time"])
        ttk.Entry(time_frame, textvariable=self.start_var, width=8).grid(row=1, column=0, sticky="w")
        ttk.Entry(time_frame, textvariable=self.end_var, width=8).grid(row=1, column=1, sticky="w", padx=(20, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=16)
        ttk.Button(btns, text="保存", command=self._save).pack(side="left", padx=10)
        ttk.Button(btns, text="削除", command=self._delete).pack(side="left")
        ttk.Button(btns, text="キャンセル", command=self.destroy).pack(side="right", padx=10)

    def _save(self):
        task = self.task_cb.get().strip()
        start = self.start_var.get().strip()
        end = self.end_var.get().strip()

        if not task:
            messagebox.showerror("入力エラー", "内容を入力してください")
            return
        if not (valid_hhmm(start) and valid_hhmm(end)):
            messagebox.showerror("入力エラー", "時刻は HH:MM 形式で入力してください(例: 09:30)")
            return
        if db.hhmm_to_minutes(end) <= db.hhmm_to_minutes(start):
            messagebox.showerror("入力エラー", "終了時刻は開始時刻より後にしてください")
            return

        db.update_activity(self.activity["id"], task=task, start_time=start, end_time=end)
        self.destroy()
        if self.on_saved:
            self.on_saved()
        else:
            self.app.refresh_timeline()

    def _delete(self):
        if messagebox.askyesno("削除確認", f"「{self.activity['task']}」の記録を削除しますか？"):
            db.delete_activity(self.activity["id"])
            self.destroy()
            if self.on_saved:
                self.on_saved()
            else:
                self.app.refresh_timeline()


# ---------------------------------------------------------------------------
# 手動追加ダイアログ (タイマーを使わず、時間帯を選んで追加)
# ---------------------------------------------------------------------------

class ManualAddDialog(tk.Toplevel):
    def __init__(self, app: TimelineApp, day, start_slot=None):
        super().__init__(app)
        self.app = app
        self.title("手動で記録を追加")
        self.geometry("320x300")
        self.transient(app)
        self.grab_set()

        pad = {"padx": 10, "pady": 6}

        ttk.Label(self, text="日付(他の日にも追加できます)").pack(anchor="w", **pad)
        date_frame = ttk.Frame(self)
        date_frame.pack(fill="x", padx=10)

        self.day_var = tk.StringVar(value=day)
        ttk.Button(date_frame, text="◀", width=3, command=lambda: self._shift_day(-1)).pack(side="left")
        date_entry = ttk.Entry(date_frame, textvariable=self.day_var, width=12, justify="center")
        date_entry.pack(side="left", padx=4)
        ttk.Button(date_frame, text="▶", width=3, command=lambda: self._shift_day(1)).pack(side="left")
        ttk.Button(date_frame, text="今日", command=self._set_today).pack(side="left", padx=(6, 0))

        ttk.Label(self, text="内容(選択 または 自由入力)").pack(anchor="w", **pad)
        self.task_cb = make_task_combobox(self)
        self.task_cb.pack(fill="x", padx=10)

        time_frame = ttk.Frame(self)
        time_frame.pack(fill="x", **pad)
        ttk.Label(time_frame, text="開始").grid(row=0, column=0)
        ttk.Label(time_frame, text="終了").grid(row=0, column=1, padx=(20, 0))

        start_labels = [db.slot_start_hhmm(i) for i in range(db.SLOTS_PER_DAY)]
        end_labels = [db.minutes_to_hhmm(i * db.SLOT_MINUTES) for i in range(1, db.SLOTS_PER_DAY + 1)]

        default_start_idx = start_slot if start_slot is not None else db.slot_index_from_minutes(
            datetime.now().hour * 60 + datetime.now().minute
        )
        # end_labels[k] は「スロットkの終了時刻」なので、既定では
        # 選択中の開始スロットと同じ30分ブロックの終わりをデフォルトにする
        default_end_idx = min(default_start_idx, len(end_labels) - 1)

        self.start_cb = ttk.Combobox(time_frame, values=start_labels, state="readonly", width=8)
        self.start_cb.current(default_start_idx)
        self.start_cb.grid(row=1, column=0)

        self.end_cb = ttk.Combobox(time_frame, values=end_labels, state="readonly", width=8)
        self.end_cb.current(default_end_idx)
        self.end_cb.grid(row=1, column=1, padx=(20, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=16)
        ttk.Button(btns, text="追加", command=self._save).pack(side="left", padx=10)
        ttk.Button(btns, text="キャンセル", command=self.destroy).pack(side="right", padx=10)

    def _shift_day(self, delta):
        try:
            d = parse_date(self.day_var.get().strip())
        except ValueError:
            d = parse_date(today_str())
        d = d + timedelta(days=delta)
        self.day_var.set(d.strftime(DATE_FMT))

    def _set_today(self):
        self.day_var.set(today_str())

    def _save(self):
        task = self.task_cb.get().strip()
        day = self.day_var.get().strip()
        start = self.start_cb.get()
        end = self.end_cb.get()

        if not task:
            messagebox.showerror("入力エラー", "内容を入力してください")
            return
        try:
            parse_date(day)
        except ValueError:
            messagebox.showerror("入力エラー", "日付は YYYY-MM-DD の形式で入力してください(例: 2026-08-05)")
            return
        if db.hhmm_to_minutes(end) <= db.hhmm_to_minutes(start):
            messagebox.showerror("入力エラー", "終了時刻は開始時刻より後を選んでください")
            return

        db.add_activity(day, start, end, task, source="manual")
        self.destroy()
        # 追加した日付に移動して、その場で結果を確認できるようにする
        self.app.current_day = day
        self.app.refresh_timeline()


# ---------------------------------------------------------------------------
# タイマー (開始/停止 → 自動でタイムラインに記録)
# ---------------------------------------------------------------------------

class TimerWindow(tk.Toplevel):
    def __init__(self, app: TimelineApp):
        super().__init__(app)
        self.app = app
        self.title("タイマー")
        self.geometry("300x220")
        self.resizable(False, False)

        self.running = False
        self.start_dt = None
        self._after_id = None

        pad = {"padx": 10, "pady": 6}

        ttk.Label(self, text="内容(選択 または 自由入力)").pack(anchor="w", **pad)
        self.task_cb = make_task_combobox(self)
        self.task_cb.pack(fill="x", padx=10)

        self.elapsed_label = ttk.Label(self, text="00:00:00", font=("TkDefaultFont", 24, "bold"))
        self.elapsed_label.pack(pady=16)

        self.toggle_btn = ttk.Button(self, text="▶ 開始", command=self.toggle)
        self.toggle_btn.pack(pady=4)

        self.status_label = ttk.Label(self, text="準備完了", foreground="#666")
        self.status_label.pack(pady=(4, 0))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def toggle(self):
        if not self.running:
            self._start()
        else:
            self._stop()

    def _start(self):
        task = self.task_cb.get().strip()
        if not task:
            messagebox.showerror("入力エラー", "内容を選択または入力してください")
            return
        self.task_cb.configure(state="disabled")
        self.running = True
        self.start_dt = datetime.now()
        self.toggle_btn.config(text="■ 停止")
        self.status_label.config(text=f"「{task}」を計測中…")
        self._update_elapsed()

    def _update_elapsed(self):
        if not self.running:
            return
        elapsed = int((datetime.now() - self.start_dt).total_seconds())
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.elapsed_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self._after_id = self.after(1000, self._update_elapsed)

    def _stop(self):
        self.running = False
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

        end_dt = datetime.now()
        seconds = max(1, int((end_dt - self.start_dt).total_seconds()))
        task = self.task_cb.get().strip()
        day = self.start_dt.strftime(DATE_FMT)
        start_hhmm = self.start_dt.strftime("%H:%M")
        end_hhmm = end_dt.strftime("%H:%M")
        # 同日内で開始・終了が同分に丸まった場合(1分未満の計測)も
        # add_activity 側で最低1分は占有させるので安全
        db.add_activity(day, start_hhmm, end_hhmm, task, seconds=seconds, source="timer")

        self.status_label.config(text=f"「{task}」を {format_duration(seconds)} 記録しました")
        self.toggle_btn.config(text="▶ 開始")
        self.task_cb.configure(state="normal")

        if self.app.current_day in (day, end_dt.strftime(DATE_FMT)):
            self.app.refresh_timeline()

    def _on_close(self):
        if self.running:
            if messagebox.askyesno("確認", "タイマーが動作中です。停止して記録を保存しますか？"):
                self._stop()
            else:
                if self._after_id:
                    self.after_cancel(self._after_id)
        self.app.timer_window = None
        self.destroy()


if __name__ == "__main__":
    app = TimelineApp()
    app.mainloop()
