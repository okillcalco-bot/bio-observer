"""T-110 Google Drive自動取込のテスト。

実Drive APIは使わず、フェイククライアントで検証する(D-27。実Driveでの
E2EスモークテストはWindows解析PC上で実施)。メディアは合成のみ使用。
"""

import json
import subprocess

import pytest

from bio_observer.config import StorageConfig
from bio_observer.ingest import worker
from bio_observer.ingest.drive import DriveFileInfo, DriveIngestConfig
from bio_observer.ingest.worker import discover, process_pending, run_cycle


@pytest.fixture(scope="module")
def sample_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("ingest_media") / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=600:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path.read_bytes()


class FakeDrive:
    """受け箱・結果フォルダを辞書で再現するフェイクDriveクライアント。"""

    def __init__(self):
        self.files: dict[str, dict] = {}       # file_id -> {name,mime,content,modified,parent}
        self.folders: dict[str, dict] = {"inbox": {"name": "inbox", "parent": None}}
        self.download_failures: dict[str, int] = {}  # file_id -> 残り失敗回数
        self.truncate_download: set[str] = set()
        self._seq = 0

    def add_inbox_file(self, name: str, content: bytes, modified="2026-08-09T00:00:00Z"):
        self._seq += 1
        file_id = f"gdrv{self._seq:04d}"
        self.files[file_id] = {"name": name, "mime": "video/quicktime",
                               "content": content, "modified": modified,
                               "parent": "inbox"}
        return file_id

    def set_content(self, file_id: str, content: bytes):
        """アップロード進行中を再現:サイズ・modifiedTimeを変化させる。"""
        self.files[file_id]["content"] = content
        self.files[file_id]["modified"] += "!"

    def _info(self, file_id: str) -> DriveFileInfo:
        f = self.files[file_id]
        return DriveFileInfo(file_id=file_id, name=f["name"], mime_type=f["mime"],
                             size_bytes=len(f["content"]), modified_time=f["modified"])

    def list_files(self, folder_id):
        return [self._info(fid) for fid, f in self.files.items()
                if f["parent"] == folder_id]

    def get_file_info(self, file_id):
        return self._info(file_id)

    def download_file(self, file_id, dest):
        if self.download_failures.get(file_id, 0) > 0:
            self.download_failures[file_id] -= 1
            raise OSError("simulated network failure")
        content = self.files[file_id]["content"]
        if file_id in self.truncate_download:
            content = content[: len(content) // 2]
        dest.write_bytes(content)

    def ensure_folder(self, parent_id, name):
        for fid, f in self.folders.items():
            if f["parent"] == parent_id and f["name"] == name:
                return fid
        self._seq += 1
        folder_id = f"gfold{self._seq:04d}"
        self.folders[folder_id] = {"name": name, "parent": parent_id}
        return folder_id

    def upload_file(self, folder_id, source, name):
        # 同名があれば置換(冪等)。実装のGoogleDriveClientと同じ契約
        for fid, f in self.files.items():
            if f["parent"] == folder_id and f["name"] == name:
                f["content"] = source.read_bytes()
                return fid
        self._seq += 1
        file_id = f"gup{self._seq:04d}"
        self.files[file_id] = {"name": name, "mime": "application/octet-stream",
                               "content": source.read_bytes(),
                               "modified": "upload", "parent": folder_id}
        return file_id

    # --- テスト用ヘルパー ---
    def results_files(self, job_folder_name: str) -> dict[str, bytes]:
        results_root = next((fid for fid, f in self.folders.items()
                             if f["parent"] == "inbox" and f["name"] == "results"), None)
        job_folder = next((fid for fid, f in self.folders.items()
                           if f["parent"] == results_root and f["name"] == job_folder_name),
                          None)
        return {f["name"]: f["content"] for f in self.files.values()
                if f["parent"] == job_folder}


@pytest.fixture()
def storage(tmp_path) -> StorageConfig:
    root = tmp_path / "store"
    return StorageConfig(
        data_root=root, originals_dir=root / "originals", derived_dir=root / "derived",
        models_dir=root / "models", db_path=root / "db" / "t.sqlite3",
        logs_dir=root / "logs", ffmpeg="ffmpeg", ffprobe="ffprobe", tz="Asia/Tokyo",
    )


@pytest.fixture()
def cfg() -> DriveIngestConfig:
    # テストでは時間間隔なし(間隔の検証は test_stability_requires_time_interval)
    return DriveIngestConfig(inbox_folder_id="inbox", results_parent_folder_id="inbox",
                             max_retries=2, stability_confirmations=2,
                             stability_interval_seconds=0)


def _job(db, job_id):
    return db.execute("SELECT * FROM ingest_job WHERE id = ?", (job_id,)).fetchone()


def _events(db, job_id):
    return [r["to_status"] for r in db.execute(
        "SELECT to_status FROM ingest_event WHERE ingest_job_id = ? ORDER BY rowid",
        (job_id,))]


def test_discover_filters_and_deduplicates(db, seed, cfg):
    drive = FakeDrive()
    drive.add_inbox_file("IMG_0001.MOV", b"a")
    drive.add_inbox_file("notes.txt", b"b")          # 非対応形式
    created = discover(db, drive, cfg, seed["session"])
    assert len(created) == 1
    assert _job(db, created[0])["original_file_name"] == "IMG_0001.MOV"
    # 同じDrive File IDは再発見しない
    assert discover(db, drive, cfg, seed["session"]) == []


def test_end_to_end_smoke(db, seed, storage, cfg, sample_bytes):
    """発見→完了判定→DL→登録→結果返却のE2E(検出精度は対象外)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_3355.MOV", sample_bytes)
    summary1 = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary1.discovered == 1 and summary1.waiting == 1  # 1回目は安定確認待ち
    summary2 = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary2.completed == 1

    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "completed"
    assert job["media_asset_id"] is not None
    # 遷移が全てingest_eventへ追記されている
    events = _events(db, job["id"])
    assert events[:2] == ["waiting_for_upload", "downloading"] or \
        events[0] == "waiting_for_upload"
    assert events[-1] == "completed"
    # MediaAssetが登録され、保存先は不透明ID(元ファイル名を露出しない)
    media = db.execute("SELECT * FROM media_asset WHERE id = ?",
                       (job["media_asset_id"],)).fetchone()
    assert "IMG_3355" not in media["relative_path"]
    assert media["recording_start_certainty"] == "estimated"
    # 結果がresults/<job_id>/へ返却されている
    results = drive.results_files(job["results_folder_name"])
    assert set(results) == {"status.json", "summary.csv"}
    status = json.loads(results["status.json"])
    assert status["media_asset_id"] == job["media_asset_id"]
    assert status["sha256"] == media["sha256"]
    # 一時DLファイルが残っていない
    assert list((storage.data_root / "ingest_tmp").glob("ijob_*")) == []
    # Drive上の元動画は削除されていない
    assert any(f["name"] == "IMG_3355.MOV" and f["parent"] == "inbox"
               for f in drive.files.values())


def test_upload_in_progress_is_not_downloaded(db, seed, storage, cfg, sample_bytes):
    """サイズ・modifiedTimeが変化し続ける間はダウンロードしない(4時間動画対策)。"""
    drive = FakeDrive()
    file_id = drive.add_inbox_file("IMG_grow.MOV", sample_bytes[:100])
    run_cycle(db, drive, cfg, storage, seed["session"])
    total = len(sample_bytes)
    for fraction in (total // 4, total // 2, total):
        drive.set_content(file_id, sample_bytes[:fraction])  # アップロード進行中
        summary = run_cycle(db, drive, cfg, storage, seed["session"])
        assert summary.waiting == 1 and summary.completed == 0
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "waiting_for_upload"
    # 変化が止まれば次の連続確認で取得へ進む(この時点で完全なファイル)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


def test_duplicate_content_not_reanalyzed(db, seed, storage, cfg, sample_bytes):
    """別Drive Fileでも同一ハッシュなら二重登録しない(duplicate参照つきで完了)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_a.MOV", sample_bytes)
    drive.add_inbox_file("IMG_b.MOV", sample_bytes)  # 同一内容の再アップロード
    run_cycle(db, drive, cfg, storage, seed["session"])
    run_cycle(db, drive, cfg, storage, seed["session"])
    (media_count,) = db.execute("SELECT COUNT(*) FROM media_asset "
                                "WHERE note LIKE 'ingest:%'").fetchone()
    assert media_count == 1
    jobs = db.execute("SELECT * FROM ingest_job ORDER BY created_at").fetchall()
    assert [j["status"] for j in jobs] == ["completed", "completed"]
    dup = next(j for j in jobs if j["duplicate_of_media_asset_id"] is not None)
    assert dup["media_asset_id"] is None
    # 重複ジョブにも結果(status.json)は返却される
    assert "status.json" in drive.results_files(dup["results_folder_name"])


def test_download_failure_retries_then_succeeds(db, seed, storage, cfg, sample_bytes):
    drive = FakeDrive()
    file_id = drive.add_inbox_file("IMG_retry.MOV", sample_bytes)
    drive.download_failures[file_id] = 1  # 1回だけネットワーク失敗
    run_cycle(db, drive, cfg, storage, seed["session"])
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.retrying == 1
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and job["retry_count"] == 1
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


def test_size_mismatch_retries_and_eventually_fails(db, seed, storage, cfg, sample_bytes):
    """途中ダウンロード(サイズ不一致)は再試行し、上限超過でfailedになる。"""
    drive = FakeDrive()
    file_id = drive.add_inbox_file("IMG_trunc.MOV", sample_bytes)
    drive.truncate_download.add(file_id)  # 常に途中までしか取得できない
    run_cycle(db, drive, cfg, storage, seed["session"])
    for _ in range(cfg.max_retries + 1):
        run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "failed"
    assert "サイズ不一致" in job["error"]
    assert "failed" in _events(db, job["id"])
    # 原本(Drive)は無傷・部分ファイルも残っていない
    assert drive.files[file_id]["content"] == sample_bytes
    tmp = storage.data_root / "ingest_tmp"
    assert not tmp.exists() or list(tmp.glob("*.part")) == []


def test_insufficient_disk_space_goes_to_retry(db, seed, storage, cfg, sample_bytes,
                                               monkeypatch):
    drive = FakeDrive()
    drive.add_inbox_file("IMG_big.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])

    class FakeUsage:
        free = 10
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: FakeUsage)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.retrying == 1
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and "空き容量" in job["error"]


def test_resume_after_crash_between_states(db, seed, storage, cfg, sample_bytes):
    """downloaded状態+DL済みファイルからの再開(PC再起動を想定)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_resume.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    run_cycle_until_downloading = db.execute("SELECT id FROM ingest_job").fetchone()
    job_id = run_cycle_until_downloading["id"]
    # クラッシュ地点を再現:DL完了直後(downloaded)で停止した状態を作る
    tmp = storage.data_root / "ingest_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"{job_id}.mov").write_bytes(sample_bytes)
    db.execute("UPDATE ingest_job SET status = 'downloaded' WHERE id = ?", (job_id,))
    db.commit()
    summary = process_pending(db, drive, cfg, storage)
    assert summary.completed == 1
    assert _job(db, job_id)["status"] == "completed"


def test_analysis_hook_is_invoked(db, seed, storage, cfg, sample_bytes):
    drive = FakeDrive()
    drive.add_inbox_file("IMG_hook.MOV", sample_bytes)
    seen = []

    def hook(conn, job_row):
        seen.append(job_row["media_asset_id"])

    run_cycle(db, drive, cfg, storage, seed["session"], analysis_hook=hook)
    summary = run_cycle(db, drive, cfg, storage, seed["session"], analysis_hook=hook)
    assert summary.completed == 1
    assert len(seen) == 1 and seen[0] is not None
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    events = _events(db, job["id"])
    assert "analyzing" in events and "uploading_results" in events


def test_stability_requires_time_interval(db, seed, storage, sample_bytes):
    """最小時間間隔を空けない連続確認は数えない(4時間動画の途中取得防止)。"""
    cfg = DriveIngestConfig(inbox_folder_id="inbox", results_parent_folder_id="inbox",
                            max_retries=2, stability_confirmations=2,
                            stability_interval_seconds=60)
    drive = FakeDrive()
    drive.add_inbox_file("IMG_slow.MOV", sample_bytes)
    # 連続実行しても間隔不足のため確認回数が進まず、取得しない
    for _ in range(4):
        summary = run_cycle(db, drive, cfg, storage, seed["session"])
        assert summary.completed == 0 and summary.waiting == 1
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "waiting_for_upload"
    probe = json.loads(job["stable_probe_json"])
    assert probe["confirmations"] == 1
    # 基準観測を60秒以上前へ(実時間の経過を再現)すると次の確認で成立する
    probe["observed_at"] = "2026-08-09T00:00:00Z"
    db.execute("UPDATE ingest_job SET stable_probe_json = ? WHERE id = ?",
               (json.dumps(probe), job["id"]))
    db.commit()
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


def test_registration_failure_keeps_file_for_retry(db, seed, storage, cfg,
                                                   sample_bytes, monkeypatch):
    """登録の一時的失敗ではDL済みファイルを保持し、再試行が「ファイルなし」で詰まらない。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_regfail.MOV", sample_bytes)
    real_register = worker.register_media
    calls = {"n": 0}

    def flaky_register(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            from bio_observer.media_registry import ProbeError
            raise ProbeError("simulated transient probe failure")
        return real_register(*args, **kwargs)

    monkeypatch.setattr(worker, "register_media", flaky_register)
    run_cycle(db, drive, cfg, storage, seed["session"])
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.retrying == 1
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and job["resume_status"] == "downloaded"
    # DL済みファイルが保持されている(再DL不要で再試行できる)
    assert (storage.data_root / "ingest_tmp" / f"{job['id']}.mov").exists()
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1 and calls["n"] == 2


def test_downloaded_state_with_missing_file_redownloads(db, seed, storage, cfg,
                                                        sample_bytes):
    """downloaded状態でファイルが消えていても、再取得して完了できる(詰まらない)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_gone.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    job_id = db.execute("SELECT id FROM ingest_job").fetchone()["id"]
    # ファイルなしでdownloaded状態(クラッシュ・手動削除を再現)
    db.execute("UPDATE ingest_job SET status = 'downloaded' WHERE id = ?", (job_id,))
    db.commit()
    summary = process_pending(db, drive, cfg, storage)
    assert summary.completed == 1
    assert _job(db, job_id)["status"] == "completed"
    assert "downloading" in _events(db, job_id)[-6:]  # 再取得の遷移が記録されている


def test_duplicate_returns_results_even_after_crash_before_upload(
        db, seed, storage, cfg, sample_bytes, monkeypatch):
    """重複ジョブは結果返却前にcompletedにならず、返却失敗後も再開で結果が返る。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_o1.MOV", sample_bytes)
    drive.add_inbox_file("IMG_o2.MOV", sample_bytes)  # 重複
    real_upload = worker._upload_results
    fail_once = {"armed": False}

    def flaky_upload(conn, client, cfg_, storage_, job):
        if job["duplicate_of_media_asset_id"] and not fail_once["armed"]:
            fail_once["armed"] = True
            raise OSError("simulated crash during results upload")
        return real_upload(conn, client, cfg_, storage_, job)

    monkeypatch.setattr(worker, "_upload_results", flaky_upload)
    run_cycle(db, drive, cfg, storage, seed["session"])
    run_cycle(db, drive, cfg, storage, seed["session"])
    dup = db.execute("SELECT * FROM ingest_job WHERE duplicate_of_media_asset_id "
                     "IS NOT NULL").fetchone()
    # 返却失敗時点ではcompletedになっていない(=処理対象に残る)
    assert dup["status"] == "retry_required"
    assert dup["resume_status"] == "uploading_results"
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1
    dup = db.execute("SELECT * FROM ingest_job WHERE id = ?", (dup["id"],)).fetchone()
    assert dup["status"] == "completed"
    assert "status.json" in drive.results_files(dup["results_folder_name"])


def test_results_upload_is_idempotent(db, seed, storage, cfg, sample_bytes):
    """uploading_resultsの再実行で結果ファイルが増殖しない(同名置換)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_idem.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "completed"
    # 返却直前でクラッシュした状況を再現し、同じジョブをもう一度返却させる
    db.execute("UPDATE ingest_job SET status = 'uploading_results' WHERE id = ?",
               (job["id"],))
    db.commit()
    summary = process_pending(db, drive, cfg, storage)
    assert summary.completed == 1
    results = drive.results_files(job["results_folder_name"])
    assert set(results) == {"status.json", "summary.csv"}  # 増殖していない


def test_ingest_event_append_only(db, seed, cfg):
    drive = FakeDrive()
    drive.add_inbox_file("IMG_ev.MOV", b"x")
    created = discover(db, drive, cfg, seed["session"])
    event = db.execute("SELECT * FROM ingest_event WHERE ingest_job_id = ?",
                       (created[0],)).fetchone()
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE ingest_event SET message = 'x' WHERE id = ?", (event["id"],))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM ingest_event WHERE id = ?", (event["id"],))
