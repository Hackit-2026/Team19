# タイムラインカレンダー Web版(フル機能版・ローカルサーバー)

要件定義書(01_要件定義書_詳細版.md)に記載した優先度1〜4の機能を、**ソーシャルログインを除いてすべて**実装したバージョンです。
Flask + SQLiteで作られており、Docker ComposeまたはPythonで起動できます。

## 含まれる機能

### 優先度1(基本機能)
- 新規登録・ログイン・ログアウト
- カレンダー週表示・月表示(切り替え可能、今日・現在時刻ハイライト)
- タイマー(開始→停止で自動的にカレンダーへ記録。ブラウザを閉じても計測状態はサーバー側に保存されるので、再度開けば経過時間が復元されます)
- 予定の手動追加・編集・削除
- 予定の時間帯が重複する場合の警告画面(このまま追加する/既存を編集する/やめる)
- フレンド申請・承認・拒否・解除
- フレンドのカレンダー閲覧(読み取り専用、週/月表示)

### 優先度2(本番公開に必須だった機能)
- **メールアドレス認証**(登録後、確認が完了するまで主要機能はロックされます)
- **パスワードリセット**(ログイン画面から申請できます)
- **アカウント設定でのパスワード変更**(ログイン中)

### 優先度3(要望のあった価値機能)
- **目標設定・達成率共有**:「目標」画面で週/月の目標時間を設定。達成率をフレンドに公開するかどうかも選べます
- **タイマー停止時の進捗表示**: 停止直後に、今日の合計時間・週/月の合計時間と目標達成率を表示します

### 優先度4(拡張機能)
- **フレンド申請の通知**: アプリ内通知(「通知」画面、未読バッジ)と、開発用メールボックスへの通知メール
- **予定ごとの公開範囲設定**: 予定を「フレンドに公開」または「非公開(自分のみ)」に設定できます。非公開の予定はフレンドのカレンダーには表示されません
- **カテゴリ別集計**:「集計」画面で、予定に設定したカテゴリ(学習・仕事・運動・趣味など)ごとの合計時間を週/月単位で確認できます
- **モバイル対応**: スマホ幅のブラウザでも主要画面が崩れずに使えるようレスポンシブ対応しています

### 対象外にした機能
- **ソーシャルログイン(Google等)**: 実際のOAuth連携(外部の認証情報)が必要で、ローカル環境では動作しないため対象外にしています。

## メール送信について(重要)

実際にはメールを送信せず、メール認証・パスワードリセット・フレンド申請通知をDB内の開発用メールボックスへ記録します。`DEV_MAILBOX_ENABLED=true`を明示したローカル環境だけで`/dev/mailbox`を公開し、デフォルトおよび公開環境では404を返します。

本番運用する場合は、`db.send_mock_mail()` の呼び出し箇所を実際のSMTP送信(例: Flask-Mail、SendGrid等)に置き換えてください。

## セキュリティ面の注意

- `SECRET_KEY`は必須です。`.env`はGitへコミットせず、環境ごとに十分長いランダム値を設定してください
- POSTフォームはCSRFトークンで保護されています
- 公開環境では`DEV_MAILBOX_ENABLED=false`、HTTPS利用時は`SESSION_COOKIE_SECURE=true`にしてください
- ログイン試行回数の制限は未実装です
- メール送信を実SMTPに置き換える際は、送信先アドレスの検証やレート制限も合わせて検討してください

## 必要なもの

- Docker Desktop + Docker Compose、またはPython 3.13

## Dockerでの起動方法

`.env.example`を`.env`へコピーし、`SECRET_KEY`をランダム値へ変更してから起動します。

```bash
1. Docker Desktopを起動
Docker Desktopを起動して、PowerShellで確認します。
docker --version
docker compose version
両方のバージョンが表示されれば準備完了です。
2. 環境設定ファイルを作成
Copy-Item .env.example .env
秘密鍵として使うランダム文字列を生成します。
[guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
表示された文字列をコピーして、.envを開きます。
notepad .env
ローカルデモ用なら次のように設定します。
SECRET_KEY=ここに生成したランダム文字列
DEV_MAILBOX_ENABLED=true
SESSION_COOKIE_SECURE=false
PORT=5000
TZ=Asia/Tokyo
.envはGitへコミットしません。
3. Dockerを起動
docker compose up --build -d
docker compose ps
次のようにhealthyと表示されれば成功です。
Up ... (healthy)
4. 初回デモデータを作成
初回だけ実行します。
docker compose exec -T web python seed.py
このコマンドは既存DBをリセットするので、データを残したい場合は再実行しないでください。
5. ブラウザで開く
http://127.0.0.1:5000
デモアカウント：
demo1@example.com / demo1234
demo2@example.com / demo1234
demo3@example.com / demo1234
Dockerのログを確認
docker compose logs -f web
ログ表示だけを終了する場合はCtrl + Cです。コンテナは動き続けます。
Dockerを停止
docker compose down
通常のdownではSQLiteデータは残ります。
Dockerを再起動
docker compose up -d
コードや依存関係が変わった場合：
docker compose up --build -d
DBも完全に削除する場合
docker compose down -v
これはSQLiteのデータも削除するため、必要な場合だけ実行します。

ブラウザで`http://127.0.0.1:5000`を開いてください。SQLiteはDocker Volumeの`/data/demo.db`へ保存されるため、コンテナを作り直しても保持されます。

ログ確認と停止は以下です。

```bash
docker compose logs -f web
docker compose down
```

Volumeを含めてデータを消す場合に限り、`docker compose down -v`を使用してください。

## Pythonでの起動方法

```bash
Windows PowerShellで、初めて受け取った人がPython版を起動する手順です。Python 3.13がインストールされている前提です。
初回セットアップ
PowerShellを開き、プロジェクトへ移動します。
cd C:\配置した場所\Team19
Pythonを確認します。
python --version
仮想環境を作成します。
python -m venv .venv
仮想環境を有効化します。
.\.venv\Scripts\Activate.ps1
スクリプトの実行が無効というエラーが出た場合だけ、次を実行します。
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
先頭に(.venv)と表示されたら成功です。
依存関係をインストールします。
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
起動に必要な環境変数を設定します。
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:DEV_MAILBOX_ENABLED = "true"
$env:PORT = "5000"
初回のデモデータを作成します。
python seed.py
注意：seed.pyを再実行すると、既存のローカルDBがリセットされます。
アプリを起動します。
python app.py
ブラウザで開きます。
http://127.0.0.1:5000
```

Windows PowerShellでは仮想環境を`.venv\Scripts\Activate.ps1`で有効化し、環境変数を`$env:SECRET_KEY="..."`の形式で設定してください。

## デモアカウント

`seed.py` を実行すると、以下の3アカウントが最初から用意されます(いずれもメール確認済み状態でセットアップされるため、すぐにログインして使えます)。

| メールアドレス | パスワード | 表示名 | 状態 |
|---|---|---|---|
| demo1@example.com | demo1234 | ポンスケ | 今週・先週・来週にサンプルの予定あり。週10時間・月40時間の目標を公開設定済み |
| demo2@example.com | demo1234 | フレンド太郎 | ポンスケとフレンド済み。週8時間の目標を公開設定済み |
| demo3@example.com | demo1234 | けんじ | ポンスケへフレンド申請中(「フレンド」「通知」画面で確認できます) |

デモの流れのおすすめ:

1. `demo1@example.com` でログイン → カレンダー週表示・月表示を見比べる
2. 空いている時間帯の「＋」(マスにカーソルを合わせると表示されます)から予定を追加してみる(カテゴリ・公開範囲も設定できます)
3. 既存の予定と重なる時間帯に予定を追加し、重複警告画面を確認する
4. 「タイマー」から計測を開始 → 一度ページを離れてから戻り、経過時間が保持されていることを確認 → 停止して進捗サマリー(今日・週・月の合計と達成率)が表示されるのを見る
5. 「目標」画面で目標時間を変更してみる、フレンドへの公開設定を切り替えてみる
6. 「集計」画面でカテゴリ別の内訳を見る
7. 「フレンド」画面で けんじ からの申請を承認する → 「通知」画面に承認完了の通知が(相手側に)届く
8. 「フレンド」画面から フレンド太郎 の公開中の進捗を確認する
9. 一度ログアウトし、新規登録 → 「開発用メールボックス」で確認メールを開いてリンクをクリック → メール認証を完了する流れも試せます

もう一度まっさらな状態から試したい場合は `python3 seed.py` を再実行してください(既存のデータはリセットされます)。

## ファイル構成

```
Team19/
  Dockerfile         - アプリのコンテナイメージ
  compose.yaml       - ポート・環境変数・SQLite Volume
  docker-entrypoint.sh - DB初期化後にGunicornを起動
  app.py              - Flaskアプリ本体(ルーティング)
  db.py               - データ層(SQLite操作、疑似メール送信含む)
  calendar_utils.py   - 週表示/月表示グリッドの組み立てロジック
  seed.py             - デモ用データ投入スクリプト
  templates/          - 画面テンプレート(Jinja2)
  static/style.css    - スタイルシート(レスポンシブ対応含む)
  tests/              - pytestによる自動テスト
```

## 動作確認について

新規登録→メール認証必須ゲート→開発用メールボックスでの認証完了、パスワードリセット一連の流れ、カレンダー週/月表示切り替え、予定追加(カテゴリ・公開範囲つき)・重複警告、タイマー開始/停止と進捗表示、目標設定と達成率計算、フレンド申請〜通知〜承認、アカウント設定でのパスワード変更、モバイル幅でのレイアウト崩れがないことを含め、ヘッドレスブラウザによる自動操作で一通り動作確認済みです。
