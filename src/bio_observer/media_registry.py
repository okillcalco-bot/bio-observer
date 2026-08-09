"""メディア登録(T-101):ローカルの動画・音声を MediaAsset として登録する。

原則3(原データ不変):原本ファイルは読み取りのみで、上書き・削除・再エンコードを
一切行わない。保存先(originals/)へは 一時ファイル→ハッシュ照合→atomic rename で
コピーし、登録失敗時は中途半端なDB行・ファイルを残さない(設計判断はD-26)。

パス・IDには不透明IDのみを使い、地点名・種名・元ファイル名を保存先・DBへ
露出させない(STORAGE.md / SECURITY.md)。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bio_observer.config import StorageConfig
from bio_observer.db.ids import new_id, utc_now_iso

_CHUNK_SIZE = 8 * 1024 * 1024
_PART_SUFFIX = ".part"

# 対応形式(拡張子は保存先ファイル名の形式表示にのみ使用。判定はffprobeが行う)
SUPPORTED_EXTENSIONS = {".mov", ".mp4", ".mts", ".m2ts", ".avi", ".mkv",
                        ".wav", ".mp3", ".flac", ".m4a", ".ogg"}


class MediaRegistrationError(Exception):
    """登録失敗の基底。送出時、DB行・保存先ファイルは残っていないことを保証する。"""


class ProbeError(MediaRegistrationError):
    """FFprobe失敗(破損ファイル・非対応形式・ファイル不存在)。"""


class DuplicateMediaError(MediaRegistrationError):
    """同一原本(同じSHA-256)が登録済み。"""

    def __init__(self, sha256: str, existing_id: str):
        super().__init__(f"同一原本が登録済み: {existing_id}")
        self.sha256 = sha256
        self.existing_id = existing_id


class InsufficientSpaceError(MediaRegistrationError):
    """保存先の空き容量不足。"""


class CopyVerificationError(MediaRegistrationError):
    """コピー後のハッシュ・サイズ照合の不一致。"""


class PathCollisionError(MediaRegistrationError):
    """保存先パスが既存ファイルと衝突。既存ファイルには一切触れずに失敗する。"""


@dataclass(frozen=True)
class MediaMetadata:
    media_type: str  # 'video' | 'audio'
    codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    sample_rate: int | None
    channels: int | None
    duration_seconds: float | None


@dataclass(frozen=True)
class RegistrationResult:
    media_asset_id: str
    sha256: str
    relative_path: str
    metadata: MediaMetadata


def probe_media(source: str | Path, *, ffprobe: str = "ffprobe") -> MediaMetadata:
    """FFprobeでメタデータを取得する。失敗時は ProbeError。"""
    source = Path(source)
    if not source.is_file():
        raise ProbeError(f"ファイルがありません: {source}")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(source)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe失敗: {result.stderr.strip()[-300:]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe出力を解釈できません: {exc}") from exc

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise ProbeError("映像・音声ストリームが見つかりません(破損または非対応形式)")

    duration = data.get("format", {}).get("duration")
    fps = None
    if video is not None:
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
        if "/" in rate:
            num, _, den = rate.partition("/")
            if num.isdigit() and den.isdigit() and int(den) != 0:
                fps = round(int(num) / int(den), 3)

    primary = video if video is not None else audio
    return MediaMetadata(
        media_type="video" if video is not None else "audio",
        codec=primary.get("codec_name"),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=fps,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        channels=audio.get("channels") if audio else None,
        duration_seconds=float(duration) if duration is not None else None,
    )


def compute_sha256(source: str | Path) -> str:
    """SHA-256をストリーミング計算する(全編をメモリへ読み込まない)。"""
    digest = hashlib.sha256()
    with open(source, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_with_hash(source: Path, dest_part: Path) -> str:
    """原本を読み取りのみでコピーしつつSHA-256を計算する(1パス)。"""
    digest = hashlib.sha256()
    with open(source, "rb") as src, open(dest_part, "wb") as dst:
        while chunk := src.read(_CHUNK_SIZE):
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return digest.hexdigest()


def _session_chain(conn: sqlite3.Connection, survey_session_id: str) -> tuple[str, str, str]:
    row = conn.execute(
        """
        SELECT p.id AS project_id, si.id AS site_id, st.id AS station_id
        FROM survey_session ss
        JOIN station st ON st.id = ss.station_id
        JOIN site si ON si.id = st.site_id
        JOIN project p ON p.id = si.project_id
        WHERE ss.id = ?
        """,
        (survey_session_id,),
    ).fetchone()
    if row is None:
        raise MediaRegistrationError(f"SurveySessionがありません: {survey_session_id}")
    return row["project_id"], row["site_id"], row["station_id"]


def _finalize_exclusive(part: Path, final: Path) -> None:
    """一時ファイルを確定パスへ、既存ファイルを上書きせず原子的に確定する。

    同一ディレクトリ内で os.link により確定名を排他的に作成する(既存があれば
    FileExistsError となり、既存ファイルは変更されない)。成功後に一時ファイルを
    削除する。ハードリンク非対応のファイルシステム(exFAT等)では、存在確認の上で
    os.replace へフォールバックする(この場合のみ確認と確定の間にTOCTOU窓が残る。
    限界としてD-26に記録)。
    """
    try:
        os.link(part, final)
    except FileExistsError:
        raise PathCollisionError(f"確定先が既に存在します(既存ファイルは変更しません): {final.name}")
    except OSError:
        if final.exists():
            raise PathCollisionError(f"確定先が既に存在します(既存ファイルは変更しません): {final.name}")
        os.replace(part, final)
        return
    part.unlink()


def _validate_recording_start(
    basis: str | None, certainty: str | None
) -> None:
    # 自動取得(メタデータ・ファイル時刻)由来の日時を「確定」として自動断定しない
    if certainty == "confirmed" and basis not in ("manual", "corrected"):
        raise ValueError(
            "certainty='confirmed' は人の入力・補正(basis='manual'/'corrected')のみ許可"
        )


def register_media(
    conn: sqlite3.Connection,
    source: str | Path,
    survey_session_id: str,
    *,
    storage: StorageConfig,
    recording_started_at: str | None = None,
    recording_start_basis: str | None = None,
    recording_start_certainty: str | None = None,
    tz: str | None = None,
    note: str | None = None,
) -> RegistrationResult:
    """原本を originals/ へ安全にコピーし、MediaAsset として登録する。

    - 原本(source)は読み取りのみ。上書き・削除・再エンコードしない
    - 保存先パスは不透明IDのみで構成(元ファイル名を使わない)
    - 既存ファイルとの衝突時は、既存ファイルに一切触れず失敗する。例外時に
      削除するのは本呼出しが作成したファイルのみ(D-26)
    - 撮影開始日時が未指定の場合、ファイル更新時刻から basis='file_time'・
      certainty='estimated' として推定する(確定として自動断定しない。D-26)
    - 失敗時はDB行・コピー先ファイルを残さない

    トランザクション契約:本関数は接続のトランザクション所有者として振る舞い、
    成功時に conn.commit()、失敗時に conn.rollback() を接続全体へ発行する。
    呼び出し側の未確定変更と同一トランザクションで合成しないこと(合成が必要に
    なった場合はSAVEPOINT化を検討する。D-26)。
    """
    source = Path(source)
    metadata = probe_media(source, ffprobe=storage.ffprobe)

    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ProbeError(f"対応形式ではありません: {ext}(対応: {sorted(SUPPORTED_EXTENSIONS)})")

    # 撮影開始日時(未指定ならファイル更新時刻からの推定 = estimated)
    _validate_recording_start(recording_start_basis, recording_start_certainty)
    file_mtime_iso = (
        datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    if recording_started_at is None:
        recording_started_at = file_mtime_iso
        recording_start_basis = "file_time"
        recording_start_certainty = "estimated"

    # 保存先(不透明IDのみでパスを構成)
    project_id, site_id, station_id = _session_chain(conn, survey_session_id)
    media_id = new_id("med")
    rel_dir = Path(project_id) / site_id / station_id / survey_session_id
    relative_path = str(rel_dir / f"{media_id}{ext}")
    dest_dir = storage.originals_dir / rel_dir
    dest_final = storage.originals_dir / relative_path
    dest_part = dest_final.with_suffix(dest_final.suffix + _PART_SUFFIX)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 既存資産との衝突は、既存ファイルへ一切触れずに失敗させる(原則3)
    if dest_final.exists():
        raise PathCollisionError(f"確定先が既に存在します(既存ファイルは変更しません): {dest_final.name}")
    if dest_part.exists():
        raise PathCollisionError(f"一時ファイルが既に存在します(変更しません): {dest_part.name}")

    # 空き容量の事前確認(原本サイズ+余裕1%)
    source_size = source.stat().st_size
    free = shutil.disk_usage(dest_dir).free
    if free < source_size * 1.01:
        raise InsufficientSpaceError(
            f"空き容量不足: 必要 {source_size} bytes / 空き {free} bytes"
        )

    finalized = False
    try:
        # 1パス目:コピーしながら原本のハッシュを計算
        sha256 = _copy_with_hash(source, dest_part)

        # 二重登録防止(同一原本)
        existing = conn.execute(
            "SELECT id FROM media_asset WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if existing:
            raise DuplicateMediaError(sha256, existing["id"])

        # コピー先を再ハッシュして照合(コピー破損の検知)
        copied_sha256 = compute_sha256(dest_part)
        if copied_sha256 != sha256 or dest_part.stat().st_size != source_size:
            raise CopyVerificationError("コピー後のハッシュ・サイズが原本と一致しません")

        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO media_asset (
                id, survey_session_id, media_type, relative_path, sha256,
                codec, width, height, fps, sample_rate, channels, duration_seconds,
                metadata_recorded_at, recording_started_at,
                recording_start_basis, recording_start_certainty,
                timezone, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (media_id, survey_session_id, metadata.media_type, relative_path, sha256,
             metadata.codec, metadata.width, metadata.height, metadata.fps,
             metadata.sample_rate, metadata.channels, metadata.duration_seconds,
             file_mtime_iso, recording_started_at,
             recording_start_basis, recording_start_certainty,
             tz or storage.tz, note, now, now),
        )
        _finalize_exclusive(dest_part, dest_final)  # 既存を上書きしない原子的確定
        finalized = True
        conn.commit()
    except BaseException:
        conn.rollback()
        # 削除するのは本呼出しが作成したファイルのみ(既存資産へは触れない)
        dest_part.unlink(missing_ok=True)
        if finalized:
            dest_final.unlink(missing_ok=True)
        raise

    return RegistrationResult(
        media_asset_id=media_id,
        sha256=sha256,
        relative_path=relative_path,
        metadata=metadata,
    )
