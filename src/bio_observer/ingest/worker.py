"""取込ワーカー(T-110):Drive受け箱の巡回→安全DL→登録→結果返却。

状態機械(ingest_job.status):
  discovered → waiting_for_upload → downloading → downloaded → registered
    → queued → analyzing → uploading_results → completed
  失敗時は retry_required(復帰先を resume_status に保持)→ 再試行、
  上限超過で failed。全遷移は ingest_event へ追記される(D-27)。

原則:
- Drive上の元動画は削除・移動・改名しない(読み取りのみ)
- 原本の正はローカル(register_media が原則3を担保)。DLした一時ファイルは
  登録成功後に削除する(originals/ にコピー済みのため)
- 同じDrive File ID(UNIQUE制約)・同じSHA-256(register_mediaの重複検知)を
  二重解析しない
- PC再起動後も、DB上の状態から process_pending() で途中再開できる
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bio_observer.config import StorageConfig
from bio_observer.db.ids import new_id, utc_now_iso
from bio_observer.ingest.drive import DriveClient, DriveFileInfo, DriveIngestConfig
from bio_observer.media_registry import (
    SUPPORTED_EXTENSIONS,
    DuplicateMediaError,
    MediaRegistrationError,
    register_media,
)

RETRYABLE_STATUSES = ("waiting_for_upload", "downloading", "downloaded",
                      "registered", "queued", "analyzing", "uploading_results")


@dataclass
class CycleSummary:
    discovered: int = 0
    completed: int = 0
    failed: int = 0
    waiting: int = 0
    retrying: int = 0


def _transition(conn: sqlite3.Connection, job_id: str, to_status: str,
                message: str | None = None, detail: dict | None = None,
                **job_updates) -> None:
    """状態遷移:ingest_job更新+ingest_eventへの追記を1コミットで行う。"""
    row = conn.execute("SELECT status FROM ingest_job WHERE id = ?", (job_id,)).fetchone()
    now = utc_now_iso()
    sets = ", ".join(["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in job_updates])
    conn.execute(
        f"UPDATE ingest_job SET {sets} WHERE id = ?",
        [to_status, now, *job_updates.values(), job_id],
    )
    conn.execute(
        "INSERT INTO ingest_event (id, ingest_job_id, occurred_at, from_status, to_status, "
        "message, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("ievt"), job_id, now, row["status"] if row else None, to_status,
         message, json.dumps(detail, ensure_ascii=False) if detail else None, now),
    )
    conn.commit()


def _fail_or_retry(conn: sqlite3.Connection, job: sqlite3.Row, cfg: DriveIngestConfig,
                   resume_status: str, error: str) -> str:
    """失敗時:上限内なら retry_required(復帰先つき)、超過で failed。"""
    retry_count = job["retry_count"] + 1
    if retry_count > cfg.max_retries:
        _transition(conn, job["id"], "failed",
                    message=f"再試行上限({cfg.max_retries})超過: {error}",
                    retry_count=retry_count, error=error)
        return "failed"
    _transition(conn, job["id"], "retry_required",
                message=f"再試行予約({retry_count}/{cfg.max_retries}): {error}",
                retry_count=retry_count, error=error, resume_status=resume_status)
    return "retry_required"


def _reload(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    """遷移後の最新ジョブ行を取り直す。"""
    return conn.execute("SELECT * FROM ingest_job WHERE id = ?", (job_id,)).fetchone()


def _tmp_dir(storage: StorageConfig) -> Path:
    path = storage.data_root / "ingest_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_ext(job: sqlite3.Row) -> str:
    return Path(job["original_file_name"] or "").suffix.lower()


def discover(conn: sqlite3.Connection, client: DriveClient, cfg: DriveIngestConfig,
             survey_session_id: str) -> list[str]:
    """受け箱を確認し、未処理の対応形式ファイルをingest_jobとして登録する。"""
    created: list[str] = []
    for info in client.list_files(cfg.inbox_folder_id):
        ext = Path(info.name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue  # フォルダ・非対応形式はスキップ(結果フォルダ等)
        exists = conn.execute(
            "SELECT 1 FROM ingest_job WHERE source = 'google_drive' AND drive_file_id = ?",
            (info.file_id,),
        ).fetchone()
        if exists:
            continue  # 同じDrive File IDは二重取込しない
        job_id = new_id("ijob")
        now = utc_now_iso()
        conn.execute(
            "INSERT INTO ingest_job (id, source, drive_file_id, original_file_name, "
            "mime_type, size_bytes, modified_time, survey_session_id, status, "
            "results_folder_name, created_at, updated_at) "
            "VALUES (?, 'google_drive', ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, ?)",
            (job_id, info.file_id, info.name, info.mime_type, info.size_bytes,
             info.modified_time, survey_session_id, job_id, now, now),
        )
        conn.commit()
        _transition(conn, job_id, "waiting_for_upload", message="発見・アップロード完了待ち",
                    detail={"size": info.size_bytes, "modified": info.modified_time})
        created.append(job_id)
    return created


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check_upload_stable(conn: sqlite3.Connection, client: DriveClient,
                         cfg: DriveIngestConfig, job: sqlite3.Row) -> bool:
    """サイズ・modifiedTimeが「最小時間間隔を空けた」連続確認で不変ならTrue。

    間隔(stability_interval_seconds)が足りない観測は確認回数に数えない。
    ワーカーを連続実行しても、実時間で間隔×(確認回数-1)以上待たないと
    ダウンロードへ進まない(4時間動画の途中取得防止)。
    """
    info = client.get_file_info(job["drive_file_id"])
    probe = json.loads(job["stable_probe_json"] or "{}")
    now = utc_now_iso()
    same = (probe.get("size") == info.size_bytes
            and probe.get("modified") == info.modified_time)
    if not same:
        confirmations = 1
        observed_at = now  # 変化を観測:基準時刻を更新して数え直し
    else:
        elapsed = (_parse_iso(now) - _parse_iso(probe["observed_at"])).total_seconds()
        if elapsed >= cfg.stability_interval_seconds:
            confirmations = probe.get("confirmations", 1) + 1
            observed_at = now
        else:
            # 間隔不足:確認回数・基準時刻を進めない
            confirmations = probe.get("confirmations", 1)
            observed_at = probe["observed_at"]
    conn.execute(
        "UPDATE ingest_job SET stable_probe_json = ?, size_bytes = ?, modified_time = ?, "
        "updated_at = ? WHERE id = ?",
        (json.dumps({"size": info.size_bytes, "modified": info.modified_time,
                     "confirmations": confirmations, "observed_at": observed_at}),
         info.size_bytes, info.modified_time, now, job["id"]),
    )
    conn.commit()
    return confirmations >= cfg.stability_confirmations


def _download(conn: sqlite3.Connection, client: DriveClient, cfg: DriveIngestConfig,
              storage: StorageConfig, job: sqlite3.Row) -> Path:
    """一時拡張子でDLし、サイズ検証後に確定名(一時領域内)へ移す。"""
    expected_size = job["size_bytes"]
    tmp = _tmp_dir(storage)
    part = tmp / f"{job['id']}{_job_ext(job)}.part"
    final = tmp / f"{job['id']}{_job_ext(job)}"
    if final.exists():
        return final  # 再開:DL済み
    free = shutil.disk_usage(tmp).free
    if expected_size is not None and free < expected_size * 1.05:
        raise OSError(f"ローカル空き容量不足: 必要 {expected_size} / 空き {free}")
    try:
        client.download_file(job["drive_file_id"], part)
        actual = part.stat().st_size
        if expected_size is not None and actual != expected_size:
            raise OSError(f"取得サイズ不一致: 期待 {expected_size} / 実際 {actual}")
        part.replace(final)  # 一時領域内の確定(originals/への確定はregister_mediaが担う)
        return final
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _register(conn: sqlite3.Connection, storage: StorageConfig,
              job: sqlite3.Row, downloaded: Path) -> None:
    """登録に決着(登録成功/重複確定)した場合のみ一時DLファイルを削除する。

    それ以外の例外(一時的なprobe失敗等)ではファイルを保持したまま伝播し、
    再試行でダウンロード済みファイルを再利用できるようにする。
    """
    try:
        result = register_media(
            conn, downloaded, job["survey_session_id"], storage=storage,
            # 優先順位2(T-112):Drive上の更新時刻。動画内creation_timeがあればそちらが優先
            origin_modified_time=job["modified_time"],
            note=f"ingest:{job['id']}",
        )
    except DuplicateMediaError as exc:
        # 同一原本は二重解析しない:重複参照を記録し、結果返却へ進む
        # (結果返却前にcompletedへせず、クラッシュしても再開時に返却される)
        _transition(conn, job["id"], "uploading_results",
                    message=f"同一原本が登録済みのためスキップ: {exc.existing_id}",
                    duplicate_of_media_asset_id=exc.existing_id)
        downloaded.unlink(missing_ok=True)
        return
    _transition(conn, job["id"], "registered", message="MediaAsset登録完了",
                detail={"sha256": result.sha256,
                        "recording_started_at": result.recording_started_at,
                        "recording_start_basis": result.recording_start_basis,
                        "recording_start_certainty": result.recording_start_certainty,
                        "recording_start_source": result.recording_start_source},
                media_asset_id=result.media_asset_id)
    downloaded.unlink(missing_ok=True)  # 原本はoriginals/とDrive上に存在


def _upload_results(conn: sqlite3.Connection, client: DriveClient,
                    cfg: DriveIngestConfig, storage: StorageConfig,
                    job: sqlite3.Row) -> None:
    """results/<job_id>/ へ status.json と summary.csv を返却する。"""
    results_root = client.ensure_folder(cfg.results_parent_folder_id, "results")
    job_folder = client.ensure_folder(results_root, job["results_folder_name"])

    media = None
    media_id = job["media_asset_id"] or job["duplicate_of_media_asset_id"]
    if media_id:
        media = conn.execute("SELECT * FROM media_asset WHERE id = ?", (media_id,)).fetchone()
    # 登録時に採用した撮影開始日時の根拠(registered遷移のdetailから。重複ジョブはなし)
    registered = conn.execute(
        "SELECT detail_json FROM ingest_event WHERE ingest_job_id = ? "
        "AND to_status = 'registered' ORDER BY rowid DESC LIMIT 1", (job["id"],)
    ).fetchone()
    recording = json.loads(registered["detail_json"]) if registered and registered["detail_json"] else {}

    tmp = _tmp_dir(storage)
    status_path = tmp / f"{job['id']}_status.json"
    summary_path = tmp / f"{job['id']}_summary.csv"
    try:
        status_path.write_text(json.dumps({
            "job_id": job["id"],
            "drive_file_id": job["drive_file_id"],
            "original_file_name": job["original_file_name"],
            "media_asset_id": job["media_asset_id"],
            "duplicate_of_media_asset_id": job["duplicate_of_media_asset_id"],
            "sha256": media["sha256"] if media else None,
            "recording_started_at": media["recording_started_at"] if media else None,
            "recording_start_basis": media["recording_start_basis"] if media else None,
            "recording_start_certainty": media["recording_start_certainty"] if media else None,
            "recording_start_source": recording.get("recording_start_source"),
            "status": "completed",
            "retry_count": job["retry_count"],
            "generated_at": utc_now_iso(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["job_id", "media_asset_id", "media_type", "codec",
                             "duration_seconds", "width", "height", "fps",
                             "sample_rate", "channels", "sha256",
                             "recording_started_at", "recording_start_basis",
                             "recording_start_certainty"])
            if media:
                writer.writerow([job["id"], media["id"], media["media_type"],
                                 media["codec"], media["duration_seconds"],
                                 media["width"], media["height"], media["fps"],
                                 media["sample_rate"], media["channels"], media["sha256"],
                                 media["recording_started_at"],
                                 media["recording_start_basis"],
                                 media["recording_start_certainty"]])
        client.upload_file(job_folder, status_path, "status.json")
        client.upload_file(job_folder, summary_path, "summary.csv")
    finally:
        status_path.unlink(missing_ok=True)
        summary_path.unlink(missing_ok=True)


def process_pending(conn: sqlite3.Connection, client: DriveClient,
                    cfg: DriveIngestConfig, storage: StorageConfig,
                    analysis_hook=None) -> CycleSummary:
    """未完了ジョブを1巡処理する。定期実行・再起動後の再開の両方に使う。

    analysis_hook(conn, job_row) は解析パイプライン(T-102以降)の差込点。
    未指定なら解析はスキップし、登録と結果返却のみ行う(Issue #6のE2E
    スモークテスト範囲。検出精度は合否条件にしない)。
    """
    summary = CycleSummary()
    jobs = conn.execute(
        "SELECT * FROM ingest_job WHERE status NOT IN ('completed', 'failed') "
        "ORDER BY created_at",
    ).fetchall()
    for job in jobs:
        job_id = job["id"]
        try:
            status = job["status"]
            if status == "retry_required":
                status = job["resume_status"] or "waiting_for_upload"
                _transition(conn, job_id, status, message="再試行")
            if status == "discovered":
                _transition(conn, job_id, "waiting_for_upload")
                status = "waiting_for_upload"
            if status == "waiting_for_upload":
                job = _reload(conn, job_id)
                if not _check_upload_stable(conn, client, cfg, job):
                    summary.waiting += 1
                    continue
                _transition(conn, job_id, "downloading", message="アップロード完了を確認")
                status = "downloading"
            if status == "downloading":
                job = _reload(conn, job_id)
                _download(conn, client, cfg, storage, job)
                _transition(conn, job_id, "downloaded", message="ダウンロード・サイズ検証完了")
                status = "downloaded"
            if status == "downloaded":
                job = _reload(conn, job_id)
                downloaded = _tmp_dir(storage) / f"{job_id}{_job_ext(job)}"
                if not downloaded.exists():
                    # 一時ファイル不在(手動削除・別領域の消失等)は詰まらせず再取得する
                    _transition(conn, job_id, "downloading",
                                message="ダウンロード済みファイル不在のため再取得")
                    job = _reload(conn, job_id)
                    downloaded = _download(conn, client, cfg, storage, job)
                    _transition(conn, job_id, "downloaded", message="再取得完了")
                    job = _reload(conn, job_id)
                _register(conn, storage, job, downloaded)
                job = _reload(conn, job_id)
                status = job["status"]  # registered または uploading_results(重複)
            if status == "registered":
                _transition(conn, job_id, "queued")
                status = "queued"
            if status == "queued":
                if analysis_hook is not None:
                    _transition(conn, job_id, "analyzing")
                    job = _reload(conn, job_id)
                    analysis_hook(conn, job)
                status = "uploading_results"
                _transition(conn, job_id, "uploading_results")
            if status == "analyzing":  # 再開:hook実行途中でのクラッシュ
                if analysis_hook is not None:
                    job = _reload(conn, job_id)
                    analysis_hook(conn, job)
                _transition(conn, job_id, "uploading_results")
                status = "uploading_results"
            if status == "uploading_results":
                job = _reload(conn, job_id)
                _upload_results(conn, client, cfg, storage, job)
                _transition(conn, job_id, "completed", message="結果返却完了")
                summary.completed += 1
        except (OSError, MediaRegistrationError, sqlite3.DatabaseError) as exc:
            job = _reload(conn, job_id)
            resume = job["status"] if job["status"] in RETRYABLE_STATUSES else "waiting_for_upload"
            outcome = _fail_or_retry(conn, job, cfg, resume, f"{type(exc).__name__}: {exc}")
            if outcome == "failed":
                summary.failed += 1
            else:
                summary.retrying += 1
    return summary


def run_cycle(conn: sqlite3.Connection, client: DriveClient, cfg: DriveIngestConfig,
              storage: StorageConfig, survey_session_id: str,
              analysis_hook=None) -> CycleSummary:
    """1サイクル:受け箱の発見+未完了ジョブの処理。定期実行のエントリポイント。"""
    created = discover(conn, client, cfg, survey_session_id)
    summary = process_pending(conn, client, cfg, storage, analysis_hook=analysis_hook)
    summary.discovered = len(created)
    return summary


def plan_inbox(conn: sqlite3.Connection, client: DriveClient,
               cfg: DriveIngestConfig) -> list[dict]:
    """受け箱の一覧と処理予定を返す(読み取り専用:Drive・DBとも変更しない)。"""
    plans: list[dict] = []
    for info in client.list_files(cfg.inbox_folder_id):
        ext = Path(info.name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            action = "skip(非対応形式)"
        else:
            job = conn.execute(
                "SELECT id, status FROM ingest_job "
                "WHERE source = 'google_drive' AND drive_file_id = ?",
                (info.file_id,),
            ).fetchone()
            action = f"既存ジョブ {job['id']}({job['status']})" if job else "new(次のrunで取込)"
        plans.append({"name": info.name, "file_id": info.file_id,
                      "size_bytes": info.size_bytes, "modified": info.modified_time,
                      "action": action})
    return plans


class WorkerAlreadyRunningError(RuntimeError):
    """同一DATA_ROOTに対する取込ワーカーの二重起動。"""


def acquire_single_instance_lock(storage: StorageConfig):
    """単一ワーカー制約(D-28)を排他的ファイルロックで保証する。

    同じDATA_ROOTに対して取込ワーカーは同時に1プロセスのみ。ロックは
    <DATA_ROOT>/ingest.lock に対する OSレベルの排他ロック(Windows: msvcrt、
    POSIX: fcntl)で、プロセス終了・クラッシュ時はOSが自動解放する。
    戻り値のファイルオブジェクトを保持し続けること(クローズで解放)。
    """
    storage.data_root.mkdir(parents=True, exist_ok=True)
    lock_path = storage.data_root / "ingest.lock"
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise WorkerAlreadyRunningError(
            "取込ワーカーが既に起動しています(同一DATA_ROOTで同時実行できるのは1プロセスのみ)")
    return handle
