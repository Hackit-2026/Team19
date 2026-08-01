"""
seed.py
-------
デモ用の初期データを投入する。実行するたびにDBをリセットして作り直す。

    python3 seed.py

デモアカウント:
    demo1@example.com / demo1234  (表示名: ポンスケ)
    demo2@example.com / demo1234  (表示名: フレンド太郎、ポンスケとフレンド済み)
    demo3@example.com / demo1234  (表示名: けんじ、ポンスケへフレンド申請中)
"""

from datetime import datetime, timedelta, time

import db


def dt_on(base_date, hour, minute=0, day_offset=0):
    d = base_date + timedelta(days=day_offset)
    return datetime.combine(d, time(hour=hour, minute=minute))


def main():
    db.init_db(reset=True)

    demo1 = db.create_user("ポンスケ", "demo1@example.com", "demo1234")
    demo2 = db.create_user("フレンド太郎", "demo2@example.com", "demo1234")
    demo3 = db.create_user("けんじ", "demo3@example.com", "demo1234")

    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())

    # --- ポンスケ(demo1)の予定: 今週分 ---
    db.add_event(demo1, "勉強", dt_on(monday, 9, 0, 0), dt_on(monday, 10, 30, 0), category="学習")
    db.add_event(demo1, "作業", dt_on(monday, 13, 0, 0), dt_on(monday, 15, 0, 0), category="仕事")
    db.add_event(demo1, "筋トレ", dt_on(monday, 7, 0, 1), dt_on(monday, 7, 30, 1), category="運動")
    db.add_event(demo1, "作業", dt_on(monday, 9, 0, 1), dt_on(monday, 12, 0, 1), category="仕事")
    db.add_event(demo1, "読書", dt_on(monday, 21, 0, 2), dt_on(monday, 22, 0, 2), category="趣味", visibility="private")
    db.add_event(demo1, "勉強", dt_on(monday, 8, 30, 3), dt_on(monday, 9, 30, 3), category="学習")
    db.add_event(demo1, "勉強", dt_on(monday, 19, 0, 3), dt_on(monday, 20, 0, 3), category="学習")
    db.add_event(demo1, "筋トレ", dt_on(monday, 7, 0, 4), dt_on(monday, 7, 30, 4), category="運動")
    db.add_event(demo1, "作業", dt_on(monday, 10, 0, 5), dt_on(monday, 11, 30, 5), category="仕事")
    db.add_event(demo1, "読書", dt_on(monday, 20, 0, 6), dt_on(monday, 21, 30, 6), category="趣味")

    # 先週・来週にもいくつか(月表示の見え方確認用)
    db.add_event(demo1, "作業", dt_on(monday, 10, 0, -3), dt_on(monday, 12, 0, -3))
    db.add_event(demo1, "勉強", dt_on(monday, 9, 0, -7), dt_on(monday, 10, 0, -7))
    db.add_event(demo1, "筋トレ", dt_on(monday, 7, 0, 9), dt_on(monday, 7, 30, 9))
    db.add_event(demo1, "作業", dt_on(monday, 13, 0, 11), dt_on(monday, 16, 0, 11))

    # --- フレンド太郎(demo2)の予定: 今週分(フレンドカレンダー閲覧デモ用) ---
    db.add_event(demo2, "作業", dt_on(monday, 9, 0, 1), dt_on(monday, 17, 0, 1))
    db.add_event(demo2, "読書", dt_on(monday, 8, 0, 3), dt_on(monday, 8, 30, 3))
    db.add_event(demo2, "筋トレ", dt_on(monday, 19, 0, 4), dt_on(monday, 20, 0, 4))
    db.add_event(demo2, "勉強", dt_on(monday, 9, 0, 5), dt_on(monday, 10, 0, 5))

    # --- フレンド関係 ---
    db.send_friend_request(demo1, "demo2@example.com")
    reqs = db.get_received_requests(demo2)
    db.respond_to_request(reqs[0]["friendship_id"], demo2, accept=True)

    # けんじ(demo3)からポンスケへ申請中(承認待ちの状態を体験できるようにする)
    db.send_friend_request(demo3, "demo1@example.com")
    db.create_notification(demo1, "friend_request", "けんじ さんからフレンド申請が届いています", "/friends")
    db.send_mock_mail(
        "demo1@example.com",
        "【タイムラインカレンダー】フレンド申請が届いています",
        "ポンスケ 様\n\nけんじ さんからフレンド申請が届いています。アプリにログインして確認してください。\n",
    )

    # --- 目標(週/月、フレンドに公開)。進捗共有・タイマー停止時の進捗表示のデモ用 ---
    db.set_goal(demo1, "week", 600, is_public=True)   # 10時間/週
    db.set_goal(demo1, "month", 2400, is_public=True)  # 40時間/月
    db.set_goal(demo2, "week", 480, is_public=True)   # 8時間/週

    print("シードデータを投入しました。")
    print("  demo1@example.com / demo1234 (ポンスケ)")
    print("  demo2@example.com / demo1234 (フレンド太郎: フレンド済み)")
    print("  demo3@example.com / demo1234 (けんじ: ポンスケへ申請中)")


if __name__ == "__main__":
    main()
