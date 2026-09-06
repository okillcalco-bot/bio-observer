"""T-111 取込CLIのテスト。

実Drive・OAuth情報・位置情報は使わない:Fake Drive Client、テンポラリDB、
合成メディアのみ(SECURITY.md)。
"""

import subprocess

import pytest

from bio_observer.cli import main
from bio_observer.db import connect, schema_version
from bio_observer.ingest import worker
from test_drive_ingest import FakeDrive  # フェイクDriveクライアントを再利用


@pytest.fixture(scope="module")
def sample_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("cli_media") / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=700:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path.read_bytes()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """CLIが参照する環境変数を一時ディレクトリへ向ける(実IDに見えないダミー値)。"""
    data_root = tmp_path / "store"
    creds = tmp_path / "credentials.json"
    creds.write_text("{}", encoding="utf-8")  # ダミー(OAuth情報ではない)
    monkeypatch.setenv("BIO_OBSERVER_DATA_ROOT", str(data_root))
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID", "inbox")
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_RESULTS_PARENT_FOLDER_ID", "inbox")
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_STABILITY_CONFIRMATIONS", "2")
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_STABILITY_INTERVAL_SECONDS", "0")
    return data_root


def _setup_session(capsys) -> str:
    code = main(["setup", "--project", "テストP", "--site", "テスト地点A",
                 "--rounded-position", "dummy-mesh-0000",
                 "--station", "ST-1", "--survey-date", "2026-08-01"])
    assert code == 0
    out = capsys.readouterr().out
    return next(line.split(":")[1].split("(")[0].strip()
                for line in out.splitlines() if line.startswith("SurveySession"))


def test_migrate_command(env, capsys):
    assert main(["migrate"]) == 0
    assert "マイグレーション適用" in capsys.readouterr().out
    conn = connect(env / "db" / "bio_observer.sqlite3")
    assert schema_version(conn) >= 2
    conn.close()
    # 再実行は適用済み
    assert main(["migrate"]) == 0
    assert "適用済み" in capsys.readouterr().out


def test_setup_creates_and_reuses(env, capsys):
    session1 = _setup_session(capsys)
    assert session1.startswith("ses_")
    # 同じ引数での再実行は同じIDを再利用する(重複作成しない)
    session2 = _setup_session(capsys)
    assert session2 == session1


def test_check_config_ok_and_masks_folder_id(env, monkeypatch, capsys):
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID", "1AbCdEfGhIjKl")
    assert main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert "すべてOK" in out
    assert "1AbCdEfGhIjKl" not in out  # フォルダIDを全表示しない
    assert "1AbC…" in out


def test_check_config_reports_missing(env, monkeypatch, capsys):
    monkeypatch.delenv("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID")
    assert main(["check-config"]) == 1
    out = capsys.readouterr().out
    assert "[NG] BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID" in out


def test_run_once_end_to_end_with_fake_drive(env, capsys, sample_bytes):
    session = _setup_session(capsys)
    drive = FakeDrive()
    drive.add_inbox_file("IMG_cli.MOV", sample_bytes)
    factory = lambda: drive
    assert main(["run", "--session", session, "--once"], client_factory=factory) == 0
    assert main(["run", "--session", session, "--once"], client_factory=factory) == 0
    out = capsys.readouterr().out
    assert "完了: 1" in out
    # 結果が返却され、statusで確認できる
    assert main(["status"]) == 0
    status_out = capsys.readouterr().out
    assert "completed" in status_out and "IMG_cli.MOV" in status_out


def test_dry_run_changes_nothing(env, capsys, sample_bytes):
    session = _setup_session(capsys)
    drive = FakeDrive()
    drive.add_inbox_file("IMG_dry.MOV", sample_bytes)
    downloads = []
    original_download = drive.download_file
    drive.download_file = lambda fid, dest: downloads.append(fid) or original_download(fid, dest)

    assert main(["run", "--session", session, "--once", "--dry-run"],
                client_factory=lambda: drive) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "IMG_dry.MOV" in out and "new" in out
    assert downloads == []                      # ダウンロードしない
    assert drive.folders.keys() == {"inbox"}    # 結果フォルダも作らない(Drive無変更)
    conn = connect(env / "db" / "bio_observer.sqlite3")
    (jobs,) = conn.execute("SELECT COUNT(*) FROM ingest_job").fetchone()
    conn.close()
    assert jobs == 0                            # DBにもジョブを作らない


def test_run_unknown_session_rejected(env, capsys):
    main(["migrate"])
    capsys.readouterr()
    assert main(["run", "--session", "ses_nope", "--once"],
                client_factory=lambda: FakeDrive()) == 1
    assert "SurveySessionがありません" in capsys.readouterr().out


def test_single_instance_lock_prevents_double_start(env, capsys, sample_bytes):
    session = _setup_session(capsys)
    from bio_observer.config import StorageConfig
    storage = StorageConfig.load()
    held = worker.acquire_single_instance_lock(storage)  # 先行ワーカーを再現
    try:
        drive = FakeDrive()
        drive.add_inbox_file("IMG_lock.MOV", sample_bytes)
        code = main(["run", "--session", session, "--once"],
                    client_factory=lambda: drive)
        out = capsys.readouterr().out
        assert code == 1
        assert "既に起動しています" in out
    finally:
        held.close()
    # 解放後は起動できる
    assert main(["run", "--session", session, "--once"],
                client_factory=lambda: drive) == 0


def test_ctrl_c_stops_gracefully_and_releases_lock(env, capsys, monkeypatch,
                                                   sample_bytes):
    session = _setup_session(capsys)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "run_cycle", interrupted)
    code = main(["run", "--session", session, "--interval", "1"],
                client_factory=lambda: FakeDrive())
    out = capsys.readouterr().out
    assert code == 0
    assert "Ctrl+C" in out and "再開" in out
    # ロックが解放されている(次のワーカーを起動できる)
    from bio_observer.config import StorageConfig
    handle = worker.acquire_single_instance_lock(StorageConfig.load())
    handle.close()


def test_status_empty_message(env, capsys):
    main(["migrate"])
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "まだありません" in capsys.readouterr().out


def _db_file(env):
    return env / "db" / "bio_observer.sqlite3"


def test_dry_run_without_db_does_not_create_db(env, capsys):
    """DB未初期化でのdry-runはDBを作成せず案内して終了する(完全読み取り専用)。"""
    assert not _db_file(env).exists()
    code = main(["run", "--session", "ses_x", "--once", "--dry-run"],
                client_factory=lambda: FakeDrive())
    out = capsys.readouterr().out
    assert code == 1
    assert "migrate" in out
    assert not _db_file(env).exists()  # DBファイルが作られていない


def test_status_without_db_does_not_create_db(env, capsys):
    assert not _db_file(env).exists()
    assert main(["status"]) == 1
    assert "migrate" in capsys.readouterr().out
    assert not _db_file(env).exists()


def test_lock_acquired_before_db_and_oauth(env, capsys):
    """後発プロセスはロック拒否までにDB・OAuth(client生成)へ一切触れない。"""
    from bio_observer.config import StorageConfig
    storage = StorageConfig.load()
    held = worker.acquire_single_instance_lock(storage)  # 先行ワーカーを再現
    factory_calls = []

    def forbidden_factory():
        factory_calls.append(1)
        raise AssertionError("後発プロセスがOAuth clientへ触れた")

    try:
        assert not _db_file(env).exists()  # DB未初期化のまま二重起動させる
        code = main(["run", "--session", "ses_x", "--once"],
                    client_factory=forbidden_factory)
        out = capsys.readouterr().out
        assert code == 1 and "既に起動しています" in out
        assert factory_calls == []              # OAuth clientを生成していない
        assert not _db_file(env).exists()       # DBも作成・migrateしていない
    finally:
        held.close()


def test_inspect_time_is_read_only_and_shows_candidates(env, capsys, tmp_path, sample_bytes):
    """inspect-time は登録・DB・Driveへ触れず、候補評価のみ表示する。"""
    video = tmp_path / "clip_for_inspect.mp4"
    video.write_bytes(sample_bytes)
    code = main(["inspect-time", str(video),
                 "--origin-modified-time", "2026-08-09T11:35:51Z"])
    out = capsys.readouterr().out
    assert code == 0
    assert "media_metadata_creation_time" in out and "値なし" in out
    assert "origin_modified_time" in out and "採用値: 2026-08-09T11:35:51Z" in out
    assert "未設定(表記なしは不採用)" in out
    assert not _db_file(env).exists()  # DBを作らない


def test_run_reports_client_init_failure_without_traceback(env, capsys):
    """T-113:OAuth初期化失敗は1行の案内で exit 1(トレースバックで落ちない)。"""
    session = _setup_session(capsys)

    def broken_factory():
        raise FileNotFoundError("credentials.json not found")

    code = main(["run", "--session", session, "--once"], client_factory=broken_factory)
    out = capsys.readouterr().out
    assert code == 1 and "Driveクライアントの初期化に失敗" in out


def test_run_interval_survives_cycle_failure(env, capsys, monkeypatch):
    """T-113:継続実行は1サイクルの失敗(受け箱一覧の通信エラー等)で停止しない。"""
    session = _setup_session(capsys)
    calls = {"n": 0}

    def flaky_cycle(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("listing failed: folders/inbox")
        raise KeyboardInterrupt  # 2回目で利用者が停止

    monkeypatch.setattr(worker, "run_cycle", flaky_cycle)
    monkeypatch.setattr("bio_observer.cli.time.sleep", lambda s: None)
    code = main(["run", "--session", session, "--interval", "1"],
                client_factory=lambda: FakeDrive())
    out = capsys.readouterr().out
    assert code == 0 and calls["n"] == 2
    assert "サイクル失敗" in out and "再試行します" in out
    assert "folders/inbox" not in out and "inbo…" in out  # フォルダIDは伏せられる


def test_run_once_returns_1_on_cycle_failure(env, capsys, monkeypatch):
    session = _setup_session(capsys)
    monkeypatch.setattr(worker, "run_cycle",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert main(["run", "--session", session, "--once"],
                client_factory=lambda: FakeDrive()) == 1


def test_setup_rejects_precise_coordinates(env, capsys):
    """T-113:地点名・丸め位置に正確な座標と解釈できる値を入力できない(D-12)。"""
    # 値はすべて架空(実在地点の座標ではない)
    for args in (["--site", "地点 12.34567,123.45678"],
                 ["--rounded-position", "12.345"],
                 ["--station", "ST 12°01'23\""],
                 ["--site", "岬 12.34N 123.45E"]):
        base = ["setup", "--project", "P", "--site", "A", "--station", "ST-1",
                "--survey-date", "2026-08-01"]
        if args[0] in base:
            base[base.index(args[0]) + 1] = args[1]
        else:
            base = base + args
        code = main(base)
        out = capsys.readouterr().out
        assert code == 1 and "正確な座標" in out, args
    assert not _db_file(env).exists()  # 拒否時はDBへ触れない
    # 丸め表現(メッシュコード等の整数表記)は許可
    assert main(["setup", "--project", "P", "--site", "A", "--station", "ST-1",
                 "--survey-date", "2026-08-01", "--rounded-position", "5339-23"]) == 0


def test_status_masks_folder_id_in_stored_error(env, capsys, monkeypatch):
    """T-113再レビュー:旧版で未マスクのまま保存された error も status 表示時に伏せる。"""
    import sqlite3
    folder_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456"  # 架空
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID", folder_id)
    session = _setup_session(capsys)
    conn = sqlite3.connect(_db_file(env))
    conn.execute(
        "INSERT INTO ingest_job (id, source, drive_file_id, original_file_name, "
        "survey_session_id, status, retry_count, error, results_folder_name, "
        "created_at, updated_at) VALUES (?, 'google_drive', 'gdrv0001', 'IMG_x.MOV', ?, "
        "'retry_required', 1, ?, 'ijob_x', '2026-08-09T00:00:00Z', '2026-08-09T00:00:00Z')",
        ("ijob_" + "0" * 32, session,
         f"HttpError: <HttpError 500 when requesting .../files?q='{folder_id}'+in+parents>"))
    conn.commit()
    conn.close()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "HttpError" in out and folder_id not in out and "1AbC…" in out


def test_dry_run_failure_is_redacted_without_traceback(env, capsys, monkeypatch):
    """T-113再レビュー:dry-run でも未マスクの例外が外へ出ない(フォルダIDは伏せ字、exit 1)。"""
    folder_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456"  # 架空
    monkeypatch.setenv("BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID", folder_id)
    session = _setup_session(capsys)

    class Broken(FakeDrive):
        def list_files(self, folder_id):
            raise RuntimeError(f"<HttpError 500 when requesting .../files?q='{folder_id}'+in+parents>")

    code = main(["run", "--session", session, "--once", "--dry-run"],
                client_factory=lambda: Broken())
    out = capsys.readouterr().out
    assert code == 1 and "dry-run失敗" in out
    assert folder_id not in out and "1AbC…" in out


def test_run_stops_with_exit_2_on_auth_error(env, capsys, monkeypatch):
    """T-113再レビュー:認証・設定エラーは常駐を止めて案内する(無期限待機にしない)。"""
    session = _setup_session(capsys)
    monkeypatch.setattr(worker, "run_cycle",
                        lambda *a, **k: (_ for _ in ()).throw(
                            worker.WorkerFatalError("認証・設定エラー(人の対応が必要): RefreshError")))
    monkeypatch.setattr("bio_observer.cli.time.sleep", lambda s: None)
    code = main(["run", "--session", session, "--interval", "1"], client_factory=lambda: FakeDrive())
    out = capsys.readouterr().out
    assert code == 2 and "再認可" in out and "RefreshError" in out


def test_interval_must_be_positive(env, capsys):
    for bad in ("0", "-5", "abc"):
        with pytest.raises(SystemExit) as exc:
            main(["run", "--session", "ses_x", "--interval", bad],
                 client_factory=lambda: FakeDrive())
        assert exc.value.code == 2  # argparseの引数エラー
    capsys.readouterr()
