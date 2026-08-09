"""bio-observer CLI(T-111):DB初期化・調査コンテキスト登録・Drive取込・状態確認。

Windows解析PCでT-110の実Drive E2Eを、Pythonコードを書かずに実行するための
コマンド群。実行手順は docs/WINDOWS_E2E.md を参照。

セキュリティ(SECURITY.md):
- 秘密情報(OAuth情報)・正確な座標を表示・ログ保存しない
- Drive受け箱フォルダIDはマスク表示する
- 出力はコンソールのみ(本CLIはログファイルを作成しない)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from bio_observer.config import StorageConfig
from bio_observer.db import connect, migrate, schema_version
from bio_observer.db.ids import new_id, utc_now_iso
from bio_observer.envcheck import check_command
from bio_observer.ingest.drive import DriveIngestConfig
from bio_observer.ingest import worker
from bio_observer.ingest.worker import WorkerAlreadyRunningError

# OAuth認可より前に検査できる必須設定(check-config / run 起動時)
_REQUIRED_ENV = (
    "BIO_OBSERVER_DATA_ROOT",
    "BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID",
    "BIO_OBSERVER_DRIVE_CREDENTIALS_FILE",
    "BIO_OBSERVER_DRIVE_TOKEN_FILE",
)


def _configure_windows_console() -> None:
    """Windowsコンソール(cp932等)でも日本語出力が壊れないようUTF-8へ。"""
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _mask(value: str) -> str:
    """識別子のマスク表示(先頭4文字のみ。アクセス権を与えうる値を全表示しない)。"""
    return f"{value[:4]}…(設定済み)" if len(value) > 4 else "(設定済み)"


def _open_db(storage: StorageConfig) -> sqlite3.Connection:
    conn = connect(storage.db_path)
    migrate(conn)
    return conn


def _connect_readonly(db_path) -> sqlite3.Connection:
    """読み取り専用でDBへ接続する(DBファイルの新規作成・変更を行わない)。

    dry-run・statusなどの一覧確認モード用。DBが未初期化なら FileNotFoundError。
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------- migrate ----------------

def cmd_migrate(_args) -> int:
    storage = StorageConfig.load()
    conn = connect(storage.db_path)
    applied = migrate(conn)
    version = schema_version(conn)
    conn.close()
    if applied:
        print(f"マイグレーション適用: {applied} → 現在のスキーマ版: {version}")
    else:
        print(f"適用済み(現在のスキーマ版: {version})")
    return 0


# ---------------- setup ----------------

def _get_or_create(conn, table: str, where: dict, defaults: dict, prefix: str
                   ) -> tuple[str, bool]:
    cond = " AND ".join(f"{k} = ?" for k in where)
    row = conn.execute(f"SELECT id FROM {table} WHERE {cond}",
                       list(where.values())).fetchone()
    if row:
        return row["id"], False
    now = utc_now_iso()
    cols = {"id": new_id(prefix), **where, **defaults,
            "created_at": now, "updated_at": now}
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values()))
    conn.commit()
    return cols["id"], True


def cmd_setup(args) -> int:
    storage = StorageConfig.load()
    conn = _open_db(storage)
    try:
        project_id, p_new = _get_or_create(
            conn, "project", {"name": args.project}, {"status": "active"}, "prj")
        site_id, s_new = _get_or_create(
            conn, "site", {"project_id": project_id, "name": args.site},
            {"rounded_position": args.rounded_position,
             "rounding_level": args.rounding_level}, "site")
        station_id, st_new = _get_or_create(
            conn, "station", {"site_id": site_id, "name": args.station},
            {"equipment_type": args.equipment_type}, "stn")
        session_row = conn.execute(
            "SELECT id FROM survey_session WHERE station_id = ? AND survey_date = ?",
            (station_id, args.survey_date)).fetchone()
        if session_row:
            session_id, ses_new = session_row["id"], False
        else:
            session_id, ses_new = _get_or_create(
                conn, "survey_session",
                {"station_id": station_id, "survey_date": args.survey_date},
                {"surveyor": args.surveyor}, "ses")
        mark = {True: "作成", False: "再利用"}
        print(f"Project      : {project_id}({mark[p_new]})")
        print(f"Site         : {site_id}({mark[s_new]})")
        print(f"Station      : {station_id}({mark[st_new]})")
        print(f"SurveySession: {session_id}({mark[ses_new]})")
        print(f"\n取込の実行: bio-observer run --session {session_id} --once")
        return 0
    finally:
        conn.close()


# ---------------- check-config ----------------

def cmd_check_config(_args) -> int:
    storage = StorageConfig.load()  # .env を読み込む
    checks: list[tuple[bool, str]] = []

    for name in _REQUIRED_ENV:
        value = os.environ.get(name, "")
        if not value:
            checks.append((False, f"{name}: 未設定(.env を確認)"))
        elif name == "BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID":
            checks.append((True, f"{name}: {_mask(value)}"))
        elif name.endswith("_FILE"):
            exists = Path(value).is_file()
            required = name.endswith("CREDENTIALS_FILE")
            ok = exists or not required  # tokenは初回認可時に作られるため未存在でも可
            note = "ファイルあり" if exists else (
                "ファイルなし(要配置)" if required else "未作成(初回認可時に作成されます)")
            checks.append((ok, f"{name}: {note}"))
        else:
            checks.append((True, f"{name}: 設定済み"))

    try:
        storage.data_root.mkdir(parents=True, exist_ok=True)
        probe = storage.data_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append((True, f"DATA_ROOT: 書き込み可({storage.data_root})"))
    except OSError as exc:
        checks.append((False, f"DATA_ROOT: 書き込み不可({exc})"))

    for tool in (storage.ffmpeg, storage.ffprobe):
        checks.append(check_command(tool))

    try:
        conn = connect(storage.db_path)
        checks.append((True, f"DB: 接続OK(スキーマ版 {schema_version(conn)}。"
                             "未初期化なら bio-observer migrate を実行)"))
        conn.close()
    except Exception as exc:
        checks.append((False, f"DB: 接続不可({exc})"))

    all_ok = True
    for ok, message in checks:
        print(f"[{'OK' if ok else 'NG'}] {message}")
        all_ok = all_ok and ok
    print("設定検査: " + ("すべてOK(OAuth認可は初回のrun実行時にブラウザで行われます)"
                          if all_ok else "NGあり(上記を修正してください)"))
    return 0 if all_ok else 1


# ---------------- run ----------------

def _default_client_factory():
    from bio_observer.ingest.drive import GoogleDriveClient
    return GoogleDriveClient()


def _print_summary(summary) -> None:
    print(f"  発見: {summary.discovered} / 完了: {summary.completed} / "
          f"完了待ち: {summary.waiting} / 再試行予約: {summary.retrying} / "
          f"失敗: {summary.failed}")


def _cmd_run_dry(args, storage: StorageConfig, cfg: DriveIngestConfig,
                 client_factory) -> int:
    """dry-run:読み取り専用の一覧確認。Drive・DBとも一切変更しない。

    DB作成・マイグレーションも行わない(未初期化なら案内して終了)。
    """
    try:
        conn = _connect_readonly(storage.db_path)
    except FileNotFoundError:
        print("[NG] DBが未初期化です(dry-runはDBを作成しません)。"
              "先に bio-observer migrate / setup を実行してください")
        return 1
    try:
        session = conn.execute("SELECT id FROM survey_session WHERE id = ?",
                               (args.session,)).fetchone()
        if session is None:
            print(f"[NG] SurveySessionがありません: {args.session}"
                  "(bio-observer setup で作成してください)")
            return 1
        client = client_factory()
        print("dry-run:受け箱の一覧のみ表示します(Drive・DBとも変更しません)")
        for plan in worker.plan_inbox(conn, client, cfg):
            size = plan["size_bytes"] if plan["size_bytes"] is not None else "?"
            print(f"  {plan['name']}  size={size}  → {plan['action']}")
        return 0
    except sqlite3.OperationalError as exc:
        print(f"[NG] DBスキーマが未適用または古い可能性があります({exc})。"
              "bio-observer migrate を実行してください")
        return 1
    finally:
        conn.close()


def cmd_run(args, client_factory) -> int:
    storage = StorageConfig.load()  # .env を読み込む
    missing = [n for n in _REQUIRED_ENV if not os.environ.get(n)]
    if missing:
        for name in missing:
            print(f"[NG] {name}: 未設定(.env を確認。bio-observer check-config で検査できます)")
        return 1
    cfg = DriveIngestConfig.load()

    if args.dry_run:
        return _cmd_run_dry(args, storage, cfg, client_factory)

    # 設定確認の直後・DB/OAuthへ触れる前に排他ロックを取得する
    # (二重起動した後発プロセスがDB・tokenへ一切触れないことを保証)
    try:
        lock = worker.acquire_single_instance_lock(storage)
    except WorkerAlreadyRunningError as exc:
        print(f"[NG] {exc}")
        return 1
    try:
        conn = _open_db(storage)
        try:
            session = conn.execute("SELECT id FROM survey_session WHERE id = ?",
                                   (args.session,)).fetchone()
            if session is None:
                print(f"[NG] SurveySessionがありません: {args.session}"
                      "(bio-observer setup で作成してください)")
                return 1
            client = client_factory()
            while True:
                summary = worker.run_cycle(conn, client, cfg, storage, args.session)
                print(f"{utc_now_iso()} サイクル完了")
                _print_summary(summary)
                if args.once:
                    return 0
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n停止しました(Ctrl+C)。状態はDBへ保存済みのため、"
                  "次回の run で未完了ジョブから再開されます。")
            return 0
        finally:
            conn.close()
    finally:
        lock.close()


# ---------------- status ----------------

def cmd_status(args) -> int:
    storage = StorageConfig.load()
    try:
        conn = _connect_readonly(storage.db_path)  # 一覧確認はDBを作成・変更しない
    except FileNotFoundError:
        print("[NG] DBが未初期化です。先に bio-observer migrate を実行してください")
        return 1
    try:
        try:
            rows = conn.execute(
                "SELECT id, original_file_name, status, retry_count, error, "
                "media_asset_id, duplicate_of_media_asset_id, updated_at "
                "FROM ingest_job ORDER BY created_at DESC LIMIT ?",
                (args.limit,)).fetchall()
        except sqlite3.OperationalError as exc:
            print(f"[NG] DBスキーマが未適用または古い可能性があります({exc})。"
                  "bio-observer migrate を実行してください")
            return 1
        if not rows:
            print("IngestJobはまだありません(bio-observer run で取込を開始してください)")
            return 0
        for row in rows:
            media = row["media_asset_id"] or (
                f"duplicate→{row['duplicate_of_media_asset_id']}"
                if row["duplicate_of_media_asset_id"] else "-")
            error = (row["error"] or "").replace("\n", " ")
            if len(error) > 80:
                error = error[:80] + "…"
            print(f"{row['id']}  {row['status']:<18} retry={row['retry_count']} "
                  f"file={row['original_file_name']}  media={media}  "
                  f"updated={row['updated_at']}"
                  + (f"\n    最終エラー: {error}" if error else ""))
        return 0
    finally:
        conn.close()


# ---------------- entry ----------------

def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数を指定してください: {value!r}")
    if number < 1:
        raise argparse.ArgumentTypeError(
            f"1以上の整数を指定してください(API連打防止): {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bio-observer",
        description="bio-observer 取込CLI(設定は .env で行う。docs/WINDOWS_E2E.md 参照)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="DBを初期化・最新スキーマへマイグレーション")

    setup = sub.add_parser(
        "setup", help="Project/Site/Station/SurveySessionを登録(同名は再利用)")
    setup.add_argument("--project", required=True, help="プロジェクト名")
    setup.add_argument("--site", required=True,
                       help="地点の表示名(営巣地が特定できる名称・正確な座標を含めない)")
    setup.add_argument("--rounded-position", default=None,
                       help="丸め済み位置表現(メッシュコード等。正確な座標は不可=D-12)")
    setup.add_argument("--rounding-level", default=None, help="適用した丸め粒度")
    setup.add_argument("--station", required=True, help="設置点名(例: ST-1)")
    setup.add_argument("--equipment-type", default="camera",
                       choices=["camera", "recorder", "combined"], help="機材種別")
    setup.add_argument("--survey-date", required=True, help="調査日(YYYY-MM-DD)")
    setup.add_argument("--surveyor", default=None, help="調査者名")

    sub.add_parser("check-config",
                   help="OAuth認可の前に .env・実行環境を検査(Driveへは接続しない)")

    run = sub.add_parser("run", help="Google Drive取込ワーカーを実行")
    run.add_argument("--session", required=True, help="SurveySession ID(setupの出力)")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="1サイクルのみ実行")
    mode.add_argument("--interval", type=_positive_int, default=300,
                      help="継続実行の間隔秒(1以上の整数。既定300。Ctrl+Cで安全に停止)")
    run.add_argument("--dry-run", action="store_true",
                     help="受け箱の一覧と処理予定のみ表示(Drive・DBとも変更しない)")

    status = sub.add_parser("status", help="IngestJobの一覧・状態・最終エラーを表示")
    status.add_argument("--limit", type=int, default=20, help="表示件数(既定20)")
    return parser


def main(argv: list[str] | None = None, *, client_factory=None) -> int:
    _configure_windows_console()
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        return cmd_migrate(args)
    if args.command == "setup":
        return cmd_setup(args)
    if args.command == "check-config":
        return cmd_check_config(args)
    if args.command == "run":
        return cmd_run(args, client_factory or _default_client_factory)
    if args.command == "status":
        return cmd_status(args)
    raise AssertionError(f"unknown command: {args.command}")


def entry() -> None:
    sys.exit(main())
