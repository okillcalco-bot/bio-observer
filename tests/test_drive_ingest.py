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


def test_ingest_uses_drive_modified_time_when_no_creation_time(
        db, seed, storage, cfg, sample_bytes):
    """T-112:creation_timeのない動画は Drive の modifiedTime を撮影開始日時の推定に使う
    (ダウンロード時刻ではない)。採用根拠はイベントと status.json に記録される。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_time.MOV", sample_bytes,
                         modified="2026-07-29T08:05:00.000Z")
    run_cycle(db, drive, cfg, storage, seed["session"])
    run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    media = db.execute("SELECT * FROM media_asset WHERE id = ?",
                       (job["media_asset_id"],)).fetchone()
    assert media["recording_started_at"] == "2026-07-29T08:05:00Z"
    assert media["recording_start_basis"] == "file_time"
    assert media["recording_start_certainty"] == "estimated"
    registered = db.execute(
        "SELECT detail_json FROM ingest_event WHERE ingest_job_id = ? "
        "AND to_status = 'registered'", (job["id"],)).fetchone()
    assert json.loads(registered["detail_json"])["recording_start_source"] == \
        "origin_modified_time"
    status = json.loads(drive.results_files(job["results_folder_name"])["status.json"])
    assert status["recording_started_at"] == "2026-07-29T08:05:00Z"
    assert status["recording_start_source"] == "origin_modified_time"
    # 各候補の評価記録(raw/normalized/timezone/解釈/採否/不採用理由)が返却される
    candidates = status["recording_start_candidates"]
    assert [c["source"] for c in candidates] == [
        "media_metadata_creation_time", "origin_modified_time", "local_file_mtime"]
    assert candidates[0]["adopted"] is False and candidates[0]["rejection_reason"] == "値なし"
    assert candidates[1]["adopted"] is True
    assert candidates[1]["raw_value"] == "2026-07-29T08:05:00.000Z"
    assert candidates[2]["adopted"] is False and candidates[2]["rejection_reason"]


def test_non_oserror_exception_is_isolated_per_job(db, seed, storage, cfg, sample_bytes):
    """T-113:Drive API等の任意例外(OSError以外)でも継続実行が止まらず再試行対象になる。"""
    drive = FakeDrive()
    bad = drive.add_inbox_file("IMG_bad.MOV", sample_bytes)
    drive.add_inbox_file("IMG_ok.MOV", sample_bytes + b"\x00")  # 別内容(ハッシュが異なる)
    original_download = drive.download_file

    class FakeHttpError(Exception):  # googleapiclient.errors.HttpError はOSErrorではない
        pass

    def download(file_id, dest):
        if file_id == bad:
            # resp を持たない任意例外(分類は permanent=通常の再試行)。文言中の 500 は分類に使われない
            raise FakeHttpError("unexpected api failure while requesting .../files/%s" % bad)
        return original_download(file_id, dest)

    drive.download_file = download
    run_cycle(db, drive, cfg, storage, seed["session"])
    summary = run_cycle(db, drive, cfg, storage, seed["session"])  # 例外が伝播しない
    assert summary.retrying == 1 and summary.completed == 1
    statuses = {j["original_file_name"]: j["status"]
                for j in db.execute("SELECT original_file_name, status FROM ingest_job")}
    assert statuses == {"IMG_bad.MOV": "retry_required", "IMG_ok.MOV": "completed"}
    assert "FakeHttpError" in db.execute(
        "SELECT error FROM ingest_job WHERE original_file_name = 'IMG_bad.MOV'").fetchone()[0]


def test_polling_error_does_not_consume_retries(db, seed, storage, cfg, sample_bytes):
    """T-113:完了待ち段階の通信エラーは再試行回数を消費せず待機を継続する。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_poll.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    original_info = drive.get_file_info
    # 通信断は具体的な型で判定される(素の OSError は通信断とは見なさない)
    drive.get_file_info = lambda fid: (_ for _ in ()).throw(ConnectionResetError("network down"))
    for _ in range(cfg.max_retries + 2):  # 上限を超える回数の失敗
        summary = run_cycle(db, drive, cfg, storage, seed["session"])
        assert summary.waiting == 1 and summary.failed == 0 and summary.retrying == 0
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "waiting_for_upload" and job["retry_count"] == 0
    assert "network down" in job["error"]
    assert _events(db, job["id"]).count("waiting_for_upload") >= cfg.max_retries + 2
    # 通信が回復すれば通常どおり完了する
    drive.get_file_info = original_info
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


def test_stability_probe_without_observed_at_is_tolerated(db, seed, storage, cfg, sample_bytes):
    """T-113(補助):observed_at のない probe(旧形式・手動編集)で KeyError にならない。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_probe.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    job_id = db.execute("SELECT id FROM ingest_job").fetchone()["id"]
    db.execute("UPDATE ingest_job SET stable_probe_json = ? WHERE id = ?",
               (json.dumps({"size": len(sample_bytes), "modified": "2026-08-09T00:00:00Z",
                            "confirmations": 1}), job_id))
    db.commit()
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.failed == 0  # 例外にならず数え直し(次サイクルで成立)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


_REALISTIC_FOLDER_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456"  # 架空(実IDではない)


def _cfg_with_real_looking_ids() -> DriveIngestConfig:
    return DriveIngestConfig(inbox_folder_id=_REALISTIC_FOLDER_ID,
                             results_parent_folder_id=_REALISTIC_FOLDER_ID,
                             max_retries=2, stability_confirmations=2,
                             stability_interval_seconds=0)


def _all_persisted_text(db) -> str:
    """ingest_job.error と ingest_event の message/detail をまとめて返す(漏えい検査用)。"""
    parts = [r[0] or "" for r in db.execute("SELECT error FROM ingest_job")]
    parts += [f"{r[0] or ''} {r[1] or ''}" for r in
              db.execute("SELECT message, detail_json FROM ingest_event")]
    return "\n".join(parts)


def test_persisted_errors_do_not_contain_folder_ids(db, seed, storage, sample_bytes):
    """T-113再レビュー:例外文言に含まれるフォルダIDは DB(ingest_job.error)・
    IngestEvent へ保存する前に伏せられる(CLI表示だけでなく保存経路も安全化)。"""
    cfg2 = _cfg_with_real_looking_ids()
    drive = FakeDrive()
    fid = drive.add_inbox_file("IMG_leak.MOV", sample_bytes)
    drive.files[fid]["parent"] = cfg2.inbox_folder_id
    url = f"https://www.googleapis.com/drive/v3/files/{fid}?q='{cfg2.inbox_folder_id}'+in+parents"
    # 完了待ち段階(通信エラー扱い)とダウンロード段階(再試行)の両方で保存文言を検査
    drive.get_file_info = lambda _fid: (_ for _ in ()).throw(
        ConnectionError(f"<HttpError 503 when requesting {url} returned 'Backend Error'>"))
    run_cycle(db, drive, cfg2, storage, seed["session"])
    run_cycle(db, drive, cfg2, storage, seed["session"])
    drive.get_file_info = lambda _fid: drive._info(_fid)
    drive.download_file = lambda _fid, _dest: (_ for _ in ()).throw(
        OSError(f"<HttpError 500 when requesting {url}?alt=media>"))
    run_cycle(db, drive, cfg2, storage, seed["session"])
    run_cycle(db, drive, cfg2, storage, seed["session"])
    persisted = _all_persisted_text(db)
    assert "HttpError" in persisted                       # 内容は残る
    assert cfg2.inbox_folder_id not in persisted          # フォルダIDは残らない
    assert "1AbC…(設定済み)" in persisted                  # 先頭4文字のマスクに置換
    job = db.execute("SELECT error, status FROM ingest_job").fetchone()
    assert cfg2.inbox_folder_id not in job["error"] and job["status"] == "retry_required"


def test_data_anomaly_in_waiting_consumes_retries(db, seed, storage, cfg, sample_bytes):
    """T-113再レビュー:完了待ち段階でも内部データ異常(通信断ではない例外)は
    再試行回数を消費し、上限で failed になる(永久待機にしない)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_anomaly.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    drive.get_file_info = lambda _fid: (_ for _ in ()).throw(ValueError("unexpected payload"))
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.retrying == 1 and summary.waiting == 0
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and job["retry_count"] == 1
    assert job["resume_status"] == "waiting_for_upload"
    for _ in range(cfg.max_retries):
        summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.failed == 1
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "failed" and job["retry_count"] == cfg.max_retries + 1
    assert "ValueError" in job["error"]


def test_http_status_decides_transient_in_waiting(db, seed, storage, cfg, sample_bytes):
    """T-113再レビュー:HttpError 相当は HTTP ステータスで区別する。
    5xx/429 は通信障害(待機継続・再試行消費なし)、404/403 は待っても直らないため再試行消費。"""
    class FakeResp:
        def __init__(self, status):
            self.status = status

    class FakeHttpError(Exception):  # googleapiclient.errors.HttpError と同じ属性形状
        def __init__(self, status):
            super().__init__(f"<HttpError {status}>")
            self.resp = FakeResp(status)

    drive = FakeDrive()
    drive.add_inbox_file("IMG_http.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    drive.get_file_info = lambda _fid: (_ for _ in ()).throw(FakeHttpError(503))
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT status, retry_count FROM ingest_job").fetchone()
    assert summary.waiting == 1 and tuple(job) == ("waiting_for_upload", 0)
    drive.get_file_info = lambda _fid: (_ for _ in ()).throw(FakeHttpError(404))
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT status, retry_count FROM ingest_job").fetchone()
    assert summary.retrying == 1 and tuple(job) == ("retry_required", 1)


@pytest.mark.parametrize("probe_json, reason_part", [
    ("{not json", "解釈できない"),
    ('["a", "b"]', "想定形式でない"),
    (json.dumps({"size": 1, "modified": "x", "confirmations": 1,
                 "observed_at": "garbage"}), "observed_at / confirmations"),
    (json.dumps({"size": 1, "modified": "x", "confirmations": "2",
                 "observed_at": "2026-08-09T00:00:00Z"}), "observed_at / confirmations"),
    (json.dumps({"size": 1, "modified": "x", "confirmations": 1,
                 "observed_at": "2026-08-09T00:00:00"}), "observed_at / confirmations"),  # naive
])
def test_corrupted_probe_is_reinitialized_not_stuck(db, seed, storage, cfg, sample_bytes,
                                                    probe_json, reason_part):
    """T-113再レビュー:壊れた観測情報(JSON・観測日時・確認回数)は例外にも永久待機にもせず、
    初期化して再確認する(IngestEvent に理由を記録)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_probe.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    job_id = db.execute("SELECT id FROM ingest_job").fetchone()["id"]
    db.execute("UPDATE ingest_job SET stable_probe_json = ? WHERE id = ?", (probe_json, job_id))
    db.commit()
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    job = _job(db, job_id)
    assert summary.failed == 0 and summary.retrying == 0 and summary.waiting == 1
    assert job["status"] == "waiting_for_upload" and job["retry_count"] == 0
    probe = json.loads(job["stable_probe_json"])
    assert probe["confirmations"] == 1 and probe["observed_at"].endswith("Z")
    messages = [r[0] for r in db.execute(
        "SELECT message FROM ingest_event WHERE ingest_job_id = ? ORDER BY rowid", (job_id,))]
    assert any(m and "観測情報を初期化" in m and reason_part in m for m in messages)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])  # 数え直して完了
    assert summary.completed == 1


def _make_http_error(status: int, reason: str | None = None, uri: str = "https://www.googleapis.com/drive/v3/files"):
    """googleapiclient.errors.HttpError の実物を組み立てる(drive extra が必要)。"""
    errors_mod = pytest.importorskip("googleapiclient.errors")
    httplib2 = pytest.importorskip("httplib2")
    resp = httplib2.Response({"status": status, "reason": "x"})
    body = {"error": {"code": status, "message": reason or "error"}}
    if reason:
        body["error"]["errors"] = [{"domain": "usageLimits", "reason": reason, "message": reason}]
    return errors_mod.HttpError(resp, json.dumps(body).encode(), uri=uri)


def test_classify_error_standard_exceptions():
    """T-113再レビュー:分類はモジュール名の一括判定ではなく、具体的な例外型で行う(標準例外)。"""
    import errno
    import http.client
    import socket
    import ssl
    from bio_observer.ingest.errors import AUTH, PERMANENT, TRANSIENT, classify_error

    # 通信断(具体的な型 / errno)
    for exc in (ConnectionResetError("reset"), TimeoutError("t"), socket.gaierror(8, "dns"),
                ssl.SSLEOFError("eof"), http.client.RemoteDisconnected("closed"),
                OSError(errno.EHOSTUNREACH, "unreachable")):
        assert classify_error(exc) == TRANSIENT, exc
    # 認証・設定(人の対応が必要):証明書検証失敗は SSLError(=OSError)でも待たない
    assert classify_error(ssl.SSLCertVerificationError(1, "certificate verify failed")) == AUTH
    # 内部データ異常・ローカルI/O・素の OSError は permanent(通常の再試行→failed)
    for exc in (ValueError("bad"), KeyError("k"), json.JSONDecodeError("m", "d", 0),
                FileNotFoundError("missing"), PermissionError("denied"),
                OSError(errno.ENOSPC, "no space"), OSError("plain")):
        assert classify_error(exc) == PERMANENT, exc

    # HttpError 互換(resp.status)の応答本文が想定外の形でも分類器は落ちない
    class Resp:
        status = 403

    class OddHttpError(Exception):
        resp = Resp()
        content = b'{"error": {"errors": {"reason": "x"}, "details": "y", "status": 5}}'

    assert classify_error(OddHttpError("<HttpError 403>")) == PERMANENT


def test_classify_error_google_library_exceptions():
    """T-113再レビュー:google-auth / httplib2 / HttpError の実物で分類表を確認(drive extra 必要)。"""
    from bio_observer.ingest.errors import AUTH, PERMANENT, RATE_LIMITED, TRANSIENT, classify_error
    gauth = pytest.importorskip("google.auth.exceptions")
    httplib2 = pytest.importorskip("httplib2")
    assert classify_error(gauth.RefreshError("invalid_grant: Token has been expired or revoked")) == AUTH
    assert classify_error(gauth.DefaultCredentialsError("no creds")) == AUTH
    # トークンサーバ側の一時障害(500/503・temporarily_unavailable)は retryable=True で返る:
    # 再認可を誤案内してワーカーを止めず、通信断として待つ
    assert classify_error(gauth.RefreshError("temporarily_unavailable", retryable=True)) == TRANSIENT
    assert classify_error(gauth.TransportError("connection aborted")) == TRANSIENT
    assert classify_error(httplib2.ServerNotFoundError("Unable to find the server")) == TRANSIENT
    # HttpError:ステータス+reason
    assert classify_error(_make_http_error(403, "userRateLimitExceeded")) == RATE_LIMITED
    assert classify_error(_make_http_error(403, "rateLimitExceeded")) == RATE_LIMITED
    assert classify_error(_make_http_error(429, "rateLimitExceeded")) == RATE_LIMITED
    assert classify_error(_make_http_error(403, "insufficientFilePermissions")) == PERMANENT
    assert classify_error(_make_http_error(403, "storageQuotaExceeded")) == PERMANENT
    assert classify_error(_make_http_error(404, "notFound")) == PERMANENT
    assert classify_error(_make_http_error(401, "authError")) == AUTH
    assert classify_error(_make_http_error(503, "backendError")) == TRANSIENT
    assert classify_error(_make_http_error(500)) == TRANSIENT


def test_rate_limit_403_does_not_consume_retries_and_resumes(db, seed, storage, cfg, sample_bytes):
    """T-113再レビュー:403 rateLimitExceeded / userRateLimitExceeded は再試行回数を消費せず、
    制限解除後に同じ段階から再開して完了する(権限不足の403は通常どおり再試行消費)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_rate.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])  # 発見・安定確認1回目
    # 2サイクル目以降、ダウンロード段階でレート制限が続く(上限回数を超えても failed にならない)
    original_download = drive.download_file
    drive.download_file = lambda fid, dest: (_ for _ in ()).throw(
        _make_http_error(403, "userRateLimitExceeded"))
    for _ in range(cfg.max_retries + 3):
        summary = run_cycle(db, drive, cfg, storage, seed["session"])
        assert summary.failed == 0
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and job["retry_count"] == 0
    assert job["resume_status"] == "downloading"
    assert "userRateLimitExceeded" in job["error"]
    messages = [r[0] for r in db.execute("SELECT message FROM ingest_event ORDER BY rowid")]
    assert any(m and "レート制限" in m and "消費せず" in m for m in messages)
    # 制限解除 → 同じ段階から再開して完了
    drive.download_file = original_download
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1
    # 権限不足の 403 は待っても直らない:通常の再試行消費(同じフェイク上で別ファイル)
    perm_fid = drive.add_inbox_file("IMG_perm.MOV", sample_bytes + b"\x01")
    run_cycle(db, drive, cfg, storage, seed["session"])  # 発見
    original_info = drive.get_file_info
    drive.get_file_info = lambda fid: (_ for _ in ()).throw(
        _make_http_error(403, "insufficientFilePermissions")) if fid == perm_fid else original_info(fid)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    perm = db.execute("SELECT status, retry_count FROM ingest_job "
                      "WHERE original_file_name = 'IMG_perm.MOV'").fetchone()
    assert summary.retrying == 1 and tuple(perm) == ("retry_required", 1)


def test_auth_error_stops_cycle_without_consuming_retries(db, seed, storage, cfg, sample_bytes):
    """T-113再レビュー:再認可が必要な認証失敗・証明書検証失敗は「通信断」として無期限待機せず、
    ジョブを変えずに WorkerFatalError でサイクルを止める(人の対応を促す)。"""
    import ssl
    gauth = pytest.importorskip("google.auth.exceptions")
    drive = FakeDrive()
    drive.add_inbox_file("IMG_auth.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    for exc in (gauth.RefreshError("invalid_grant: Token has been expired or revoked"),
                ssl.SSLCertVerificationError(1, "certificate verify failed"),
                _make_http_error(401, "authError")):
        drive.get_file_info = lambda fid, exc=exc: (_ for _ in ()).throw(exc)
        with pytest.raises(worker.WorkerFatalError) as info:
            run_cycle(db, drive, cfg, storage, seed["session"])
        assert "認証・設定エラー" in str(info.value)
        job = db.execute("SELECT status, retry_count, error FROM ingest_job").fetchone()
        assert job["status"] == "waiting_for_upload" and job["retry_count"] == 0
        assert job["error"]  # 原因は記録される
    messages = [r[0] for r in db.execute("SELECT message FROM ingest_event ORDER BY rowid")]
    assert sum(1 for m in messages if m and "ワーカー停止" in m) == 3
    # 対処後(再認可)は通常どおり再開して完了する
    drive.get_file_info = lambda fid: drive._info(fid)
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    assert summary.completed == 1


class _RealisticFolderDrive(FakeDrive):
    """ensure_folder が実IDに似た長いフォルダIDを返し、返却時に URL 付きのエラーを出すフェイク。"""

    def __init__(self):
        super().__init__()
        self.fail_upload = True

    def ensure_folder(self, parent_id, name):
        fid = super().ensure_folder(parent_id, name)
        realistic = f"1Res{name}FolderIdXyZ0123456789abcdefgh"[:33]
        if fid != realistic:
            self.folders[realistic] = self.folders.pop(fid)
        return realistic

    def upload_file(self, folder_id, source, name):
        if self.fail_upload:
            # HttpError 互換の形(resp.status=500)。drive extra なしでも検証できるよう実物は使わない
            class Resp:
                status = 500

            class FakeHttpError(Exception):
                resp = Resp()

            raise FakeHttpError(
                "<HttpError 500 when requesting https://www.googleapis.com/upload/drive/v3/files"
                f"?parents={folder_id} returned 'Backend Error'>")
        return super().upload_file(folder_id, source, name)


def test_result_folder_ids_learned_at_runtime_are_masked(db, seed, storage, sample_bytes):
    """T-113再レビュー:設定値だけでなく、処理中に取得した結果フォルダID(results/・results/<job_id>/)
    も DB・イベントへ保存する前に伏せられる。"""
    cfg2 = _cfg_with_real_looking_ids()
    drive = _RealisticFolderDrive()
    fid = drive.add_inbox_file("IMG_res.MOV", sample_bytes)
    drive.files[fid]["parent"] = cfg2.inbox_folder_id
    for _ in range(3):
        run_cycle(db, drive, cfg2, storage, seed["session"])
    job = db.execute("SELECT * FROM ingest_job").fetchone()
    assert job["status"] == "retry_required" and job["resume_status"] == "uploading_results"
    assert job["retry_count"] == 0  # 5xx は待機扱い(消費なし)
    learned = [f for f in drive.folders if f.startswith("1Res")]
    assert len(learned) == 2
    persisted = _all_persisted_text(db)
    assert "HttpError 500" in persisted
    for folder_id in learned + [cfg2.inbox_folder_id]:
        assert folder_id not in persisted, folder_id
    assert "1Res" in persisted and "…(設定済み)" in persisted
    # 障害解消後に返却が完了する
    drive.fail_upload = False
    summary = run_cycle(db, drive, cfg2, storage, seed["session"])
    assert summary.completed == 1


def test_auth_error_during_discover_becomes_fatal(db, seed, storage, cfg, sample_bytes):
    """自己レビュー:受け箱一覧(discover)での認証エラーも WorkerFatalError(サイクル最初の API で
    トークン更新失敗が出るため)。通信断はそのまま伝播し CLI が次回再試行する。"""
    import ssl
    drive = FakeDrive()
    drive.add_inbox_file("IMG_disc.MOV", sample_bytes)
    drive.list_files = lambda folder_id: (_ for _ in ()).throw(
        ssl.SSLCertVerificationError(1, "certificate verify failed"))
    with pytest.raises(worker.WorkerFatalError, match="受け箱一覧"):
        run_cycle(db, drive, cfg, storage, seed["session"])
    drive.list_files = lambda folder_id: (_ for _ in ()).throw(ConnectionResetError("reset"))
    with pytest.raises(ConnectionResetError):
        run_cycle(db, drive, cfg, storage, seed["session"])
    assert db.execute("SELECT COUNT(*) FROM ingest_job").fetchone()[0] == 0  # 副作用なし


def test_future_observed_at_is_reinitialized(db, seed, storage, sample_bytes):
    """自己レビュー:観測時刻が未来(PC時計のずれ)だと間隔判定が永久に成立しないため初期化する。"""
    cfg60 = DriveIngestConfig(inbox_folder_id="inbox", results_parent_folder_id="inbox",
                              max_retries=2, stability_confirmations=2,
                              stability_interval_seconds=60)
    drive = FakeDrive()
    drive.add_inbox_file("IMG_future.MOV", sample_bytes)
    run_cycle(db, drive, cfg60, storage, seed["session"])
    job_id = db.execute("SELECT id FROM ingest_job").fetchone()["id"]
    probe = json.loads(_job(db, job_id)["stable_probe_json"])
    probe["observed_at"] = "2099-01-01T00:00:00Z"
    db.execute("UPDATE ingest_job SET stable_probe_json = ? WHERE id = ?",
               (json.dumps(probe), job_id))
    db.commit()
    run_cycle(db, drive, cfg60, storage, seed["session"])
    probe = json.loads(_job(db, job_id)["stable_probe_json"])
    assert probe["confirmations"] == 1 and probe["observed_at"] < "2099"
    messages = [r[0] for r in db.execute(
        "SELECT message FROM ingest_event WHERE ingest_job_id = ? ORDER BY rowid", (job_id,))]
    assert any(m and "未来" in m for m in messages)


def test_error_is_cleared_on_completion(db, seed, storage, cfg, sample_bytes):
    """自己レビュー:復旧して完了した行に旧エラーを残さない(履歴は IngestEvent に残る)。"""
    drive = FakeDrive()
    drive.add_inbox_file("IMG_clear.MOV", sample_bytes)
    run_cycle(db, drive, cfg, storage, seed["session"])
    original_info = drive.get_file_info
    drive.get_file_info = lambda fid: (_ for _ in ()).throw(ConnectionResetError("network down"))
    run_cycle(db, drive, cfg, storage, seed["session"])
    assert "network down" in db.execute("SELECT error FROM ingest_job").fetchone()[0]
    drive.get_file_info = original_info
    summary = run_cycle(db, drive, cfg, storage, seed["session"])
    job = db.execute("SELECT status, error FROM ingest_job").fetchone()
    assert summary.completed == 1 and tuple(job) == ("completed", None)
    assert any("network down" in (r[0] or "") for r in
               db.execute("SELECT message FROM ingest_event"))


def test_short_or_alias_folder_ids_are_not_redacted(db, seed, storage, cfg, sample_bytes):
    """自己レビュー:"root"(マイドライブ別名)や "inbox" のような短い設定値は置換対象にしない
    (パス・無関係な文言を壊さない)。実 Drive ID(25文字以上)は従来どおり伏せる。"""
    cfg_root = DriveIngestConfig(inbox_folder_id="root", results_parent_folder_id="root")
    text = "OSError: /data/root/ingest_tmp/x.mov (results_root)"
    assert worker.redact_secrets(text, worker.all_secrets(cfg_root)) == text
    long_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456"
    cfg_long = DriveIngestConfig(inbox_folder_id=long_id, results_parent_folder_id="root")
    assert long_id not in worker.redact_secrets(f"q='{long_id}' in parents",
                                                worker.all_secrets(cfg_long))
    worker.remember_secret("gfold01")  # 短い取得値も登録されない
    assert "gfold01" not in worker.all_secrets(cfg_root)


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
