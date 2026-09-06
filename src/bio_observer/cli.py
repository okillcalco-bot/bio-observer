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
import re
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
from bio_observer.ingest.worker import WorkerAlreadyRunningError, WorkerFatalError

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
    """識別子のマスク表示(先頭4文字のみ。アクセス権を与えうる値を全表示しない)。

    保存経路(worker)と同じ規則を使い、表示と保存で伏せ方がずれないようにする。
    """
    return worker.mask_secret(value)


def _redact(exc: BaseException, cfg: DriveIngestConfig) -> str:
    """例外文言からDriveフォルダIDを伏せる(HttpErrorはリクエストURLを含むため)。

    ワーカーがDB・イベントへ保存する際と同じ規則(worker.redact_secrets)を使う。
    """
    return worker.redact_secrets(f"{type(exc).__name__}: {exc}", worker.all_secrets(cfg))


_SECRET_ENV_NAMES = ("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID",
                     "BIO_OBSERVER_DRIVE_RESULTS_PARENT_FOLDER_ID")


def _env_secrets() -> tuple[str, ...]:
    """表示前に伏せる設定値(status は DriveIngestConfig を必須としないため環境変数から)。"""
    return tuple(v for v in (os.environ.get(n) for n in _SECRET_ENV_NAMES) if v)


# 正確な座標に見える入力を拒否する(SECURITY.md / D-12。メッシュコード等の整数表記は許可)
_COORDINATE_PATTERNS = (
    re.compile(r"-?\d{1,3}\.\d{3,}"),                 # 小数3桁以上の度表記(例 35.123)
    re.compile(r"[°º]"),                              # 度記号(DMS表記)
    re.compile(r"\b\d{1,3}(\.\d+)?\s*[NSEW]\b", re.I),  # 35.6N / 139E 等
)


def _looks_like_precise_coordinate(value: str | None) -> bool:
    return bool(value) and any(p.search(value) for p in _COORDINATE_PATTERNS)


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
    for label, value in (("--project", args.project), ("--site", args.site),
                         ("--station", args.station),
                         ("--rounded-position", args.rounded_position)):
        if _looks_like_precise_coordinate(value):
            print(f"[NG] {label} に正確な座標と解釈できる値が含まれています。"
                  "地点名・丸め位置には座標を入れず、メッシュコード等の丸め表現を使ってください(D-12)")
            return 1
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
        try:
            client = client_factory()
            plans = worker.plan_inbox(conn, client, cfg)
        except Exception as exc:  # noqa: BLE001 — 例外文言(URL中のフォルダID)を伏せて案内
            print(f"[NG] dry-run失敗: {_redact(exc, cfg)}")
            return 1
        print("dry-run:受け箱の一覧のみ表示します(Drive・DBとも変更しません)")
        for plan in plans:
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
            try:
                client = client_factory()
            except Exception as exc:  # noqa: BLE001 — OAuth/設定不備を1行で案内
                print(f"[NG] Driveクライアントの初期化に失敗: {_redact(exc, cfg)}")
                print("     credentials/token のパスと初回認可(ブラウザ)を確認してください")
                return 1
            while True:
                try:
                    summary = worker.run_cycle(conn, client, cfg, storage, args.session)
                except WorkerFatalError as exc:
                    # 再認可・証明書・設定の問題は待っても直らない:常駐を止めて案内する
                    # (状態はDBへ保存済み。対処後の run で未完了ジョブから再開される)
                    print(f"{utc_now_iso()} [NG] {_redact(exc, cfg)}")
                    print("     token/credentials の再認可、証明書・プロキシ設定を確認し、"
                          "対処後に bio-observer run を再実行してください")
                    return 2
                except Exception as exc:  # noqa: BLE001 — 1サイクルの失敗で常駐を止めない
                    print(f"{utc_now_iso()} サイクル失敗: {_redact(exc, cfg)}")
                    if args.once:
                        return 1
                    print(f"  {args.interval}秒後に再試行します(状態はDBへ保存済み)")
                else:
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
        secrets = _env_secrets()
        for row in rows:
            media = row["media_asset_id"] or (
                f"duplicate→{row['duplicate_of_media_asset_id']}"
                if row["duplicate_of_media_asset_id"] else "-")
            # 保存済みの文言にも伏せ字を適用(旧版で保存された未マスクの値への防御)
            error = worker.redact_secrets(row["error"] or "", secrets).replace("\n", " ")
            if len(error) > 80:
                error = error[:80] + "…"
            print(f"{row['id']}  {row['status']:<18} retry={row['retry_count']} "
                  f"file={row['original_file_name']}  media={media}  "
                  f"updated={row['updated_at']}"
                  + (f"\n    最終エラー: {error}" if error else ""))
        return 0
    finally:
        conn.close()


# ---------------- inspect-time ----------------

def cmd_inspect_time(args) -> int:
    """撮影開始日時の候補評価を表示する(登録・DB・Driveへ一切触れない検証用)。"""
    from bio_observer.media_registry import (
        ProbeError, evaluate_recording_start_candidates, probe_media)

    storage = StorageConfig.load()
    source = Path(args.path)
    try:
        metadata = probe_media(source, ffprobe=storage.ffprobe)
    except ProbeError as exc:
        print(f"[NG] {exc}")
        return 1
    candidates = evaluate_recording_start_candidates(
        metadata, args.origin_modified_time, source.stat().st_mtime,
        naive_timezone=storage.media_naive_timezone,
        naive_timezone_origin="BIO_OBSERVER_MEDIA_NAIVE_TIMEZONE")
    print(f"ファイル: {source.name}")
    print(f"解釈条件(BIO_OBSERVER_MEDIA_NAIVE_TIMEZONE): "
          f"{storage.media_naive_timezone or '未設定(表記なしは不採用)'}")
    for c in candidates:
        mark = "採用" if c["adopted"] else "不採用"
        print(f"[{c['priority']}] {c['source']} ({c['location']}) → {mark}")
        print(f"    raw={c['raw_value']!r}  normalized={c['normalized_value']}  "
              f"timezone={c['timezone']}")
        print(f"    解釈: {c['interpretation']}")
        if c["rejection_reason"]:
            print(f"    不採用理由: {c['rejection_reason']}")
    adopted = next(c for c in candidates if c["adopted"])
    print(f"\n採用値: {adopted['normalized_value']}  basis={adopted['basis']}  "
          f"certainty=estimated  source={adopted['source']}")
    return 0


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

    inspect = sub.add_parser(
        "inspect-time",
        help="動画の撮影開始日時候補(creation_time/取込元時刻/ローカル時刻)の評価を表示"
             "(登録・DB・Driveへ触れない)")
    inspect.add_argument("path", help="ローカルの動画・音声ファイル")
    inspect.add_argument("--origin-modified-time", default=None,
                         help="取込元の更新時刻(DriveのmodifiedTime等。ISO-8601)")
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
    if args.command == "inspect-time":
        return cmd_inspect_time(args)
    raise AssertionError(f"unknown command: {args.command}")


def entry() -> None:
    sys.exit(main())
