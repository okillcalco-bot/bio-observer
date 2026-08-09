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


def test_interval_must_be_positive(env, capsys):
    for bad in ("0", "-5", "abc"):
        with pytest.raises(SystemExit) as exc:
            main(["run", "--session", "ses_x", "--interval", bad],
                 client_factory=lambda: FakeDrive())
        assert exc.value.code == 2  # argparseの引数エラー
    capsys.readouterr()
