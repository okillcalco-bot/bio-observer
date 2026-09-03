"""メディア登録(T-101):ローカルの動画・音声を MediaAsset として登録する。

原則3(原データ不変):原本ファイルは読み取りのみで、上書き・削除・再エンコードを
一切行わない。保存先(originals/)へは 一時ファイル→ハッシュ照合→atomic rename で
コピーし、登録失敗時は中途半端なDB行・ファイルを残さない(設計判断はD-26)。

パス・IDには不透明IDのみを使い、地点名・種名・元ファイル名を保存先・DBへ
露出させない(STORAGE.md / SECURITY.md)。
"""

from __future__ import annotations

import errno
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
    # 動画内メタデータの作成日時(UTC ISO-8601 "…Z" へ正規化済み。なければ None)
    creation_time: str | None = None


# 撮影開始日時の自動推定に採用した根拠(RegistrationResult.recording_start_source)
SOURCE_CALLER = "caller"                      # 呼び出し側の指定(人の入力・補正を含む)
SOURCE_MEDIA_METADATA = "media_metadata_creation_time"  # 優先1:動画内メタデータ
SOURCE_ORIGIN_MODIFIED = "origin_modified_time"         # 優先2:取込元(Drive等)の更新時刻
SOURCE_LOCAL_MTIME = "local_file_mtime"                 # 優先3:ローカルファイル時刻


@dataclass(frozen=True)
class RegistrationResult:
    media_asset_id: str
    sha256: str
    relative_path: str
    metadata: MediaMetadata
    recording_started_at: str | None = None
    recording_start_basis: str | None = None
    recording_start_certainty: str | None = None
    recording_start_source: str = SOURCE_CALLER


_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"
_CREATION_TIME_TAGS = ("creation_time", "com.apple.quicktime.creationdate")


def normalize_utc_iso(value: str | None) -> str | None:
    """各種表記の日時文字列を UTC ISO-8601("YYYY-MM-DDTHH:MM:SSZ")へ正規化する。

    受理:ISO-8601(Z / ±HH:MM / ±HHMM / 小数秒あり)、"YYYY-MM-DD HH:MM:SS"。
    タイムゾーンのない値は UTC とみなす(FFmpegのcreation_timeはUTC表記が慣例。
    採用時は根拠を記録し、確実性は estimated に留める=D-26/T-112)。
    解釈できない値は None。
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":" and text[-4:].isdigit():
        text = text[:-2] + ":" + text[-2:]  # ±HHMM → ±HH:MM
    text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(_ISO_UTC)


def _extract_creation_time(data: dict) -> str | None:
    """format tags → 各stream tags の順で作成日時タグを探し、UTC ISO へ正規化する。"""
    candidates = [data.get("format", {}).get("tags") or {}]
    candidates += [s.get("tags") or {} for s in data.get("streams", [])]
    for tags in candidates:
        for key in _CREATION_TIME_TAGS:
            normalized = normalize_utc_iso(tags.get(key))
            if normalized:
                return normalized
    return None


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
        creation_time=_extract_creation_time(data),
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


# os.link がハードリンク非対応を示す errno のみフォールバックする(それ以外は再送出)
_LINK_UNSUPPORTED_ERRNOS = {
    errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL, errno.EXDEV,
}


def _discard_part(part: Path) -> None:
    """確定成功後の一時ファイル削除(テストから失敗を注入できる分離点)。"""
    part.unlink()


def _exclusive_copy(part: Path, final: Path,
                    expected_sha256: str, expected_size: int) -> None:
    """O_EXCLで確定名を排他的に作成し、一時ファイルの内容をコピー・照合する。

    既存ファイルがあれば FileExistsError(上書きは構造的に不可能)。コピー後に
    finalを読み戻してSHA-256・サイズを期待値と照合し、不一致(コピー破損)や
    途中失敗の場合は、自分が排他的に作成した確定ファイルのみ削除して再送出する。
    """
    fd = os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "wb") as dst, open(part, "rb") as src:
            while chunk := src.read(_CHUNK_SIZE):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        # finalを読み戻して照合(DBのハッシュと実ファイルの食い違いを防ぐ)
        if (compute_sha256(final) != expected_sha256
                or final.stat().st_size != expected_size):
            raise CopyVerificationError(
                "フォールバックコピー後の確定ファイルがハッシュ・サイズ照合で不一致")
    except BaseException:
        final.unlink(missing_ok=True)
        raise


def _finalize_exclusive(part: Path, final: Path,
                        expected_sha256: str, expected_size: int) -> None:
    """一時ファイルを確定パスへ、既存ファイルを上書きせず排他的に確定する。

    - 第一手段:同一ディレクトリ内の os.link による確定名の排他的作成
      (既存があれば FileExistsError となり、既存ファイルは変更されない)。
      linkは検証済み .part と同一inodeを指すため再照合は不要
    - ハードリンク非対応FS(exFAT等。errnoで判別):O_EXCL による排他的作成+
      コピーへフォールバックし、**finalを読み戻してSHA-256・サイズを照合**する。
      どの経路でも既存ファイルの上書きは起こらず、DBのハッシュと実ファイルが
      食い違う状態も残らない
    - 確定後の一時ファイル削除に失敗した場合は、自分が作成した確定ファイルを
      取り消して(削除して)例外を再送出する=後処理失敗も完全ロールバック(D-26)
    """
    try:
        os.link(part, final)
    except FileExistsError:
        raise PathCollisionError(
            f"確定先が既に存在します(既存ファイルは変更しません): {final.name}")
    except OSError as exc:
        if exc.errno not in _LINK_UNSUPPORTED_ERRNOS:
            raise
        try:
            _exclusive_copy(part, final, expected_sha256, expected_size)
        except FileExistsError:
            raise PathCollisionError(
                f"確定先が既に存在します(既存ファイルは変更しません): {final.name}")
    # 確定名の作成に成功。一時ファイルの削除失敗も完全にロールバックする
    try:
        _discard_part(part)
    except OSError:
        final.unlink(missing_ok=True)  # 自分が作成した確定ファイルのみ取り消す
        raise


def _validate_recording_start(
    basis: str | None, certainty: str | None
) -> None:
    # 自動取得(メタデータ・ファイル時刻)由来の日時を「確定」として自動断定しない
    if certainty == "confirmed" and basis not in ("manual", "corrected"):
        raise ValueError(
            "certainty='confirmed' は人の入力・補正(basis='manual'/'corrected')のみ許可"
        )


def _estimate_recording_start(
    metadata: MediaMetadata,
    origin_modified_time: str | None,
    local_mtime_iso: str,
) -> tuple[str, str, str, str]:
    """撮影開始日時の自動推定(T-112の優先順位)。戻り値:(日時, basis, certainty, source)。

    1. 動画内メタデータの creation_time(basis=metadata)
    2. 取込元(Drive等)の更新時刻(basis=file_time)
    3. ローカルファイル時刻(basis=file_time。最後の手段)
    自動推定は常に certainty='estimated'(confirmed は人の入力・補正のみ=D-26)。
    """
    if metadata.creation_time:
        return metadata.creation_time, "metadata", "estimated", SOURCE_MEDIA_METADATA
    origin = normalize_utc_iso(origin_modified_time)
    if origin:
        return origin, "file_time", "estimated", SOURCE_ORIGIN_MODIFIED
    return local_mtime_iso, "file_time", "estimated", SOURCE_LOCAL_MTIME


def register_media(
    conn: sqlite3.Connection,
    source: str | Path,
    survey_session_id: str,
    *,
    storage: StorageConfig,
    recording_started_at: str | None = None,
    recording_start_basis: str | None = None,
    recording_start_certainty: str | None = None,
    origin_modified_time: str | None = None,
    tz: str | None = None,
    note: str | None = None,
) -> RegistrationResult:
    """原本を originals/ へ安全にコピーし、MediaAsset として登録する。

    - 原本(source)は読み取りのみ。上書き・削除・再エンコードしない
    - 保存先パスは不透明IDのみで構成(元ファイル名を使わない)
    - 既存ファイルとの衝突時は、既存ファイルに一切触れず失敗する。例外時に
      削除するのは本呼出しが作成したファイルのみ(D-26)
    - 撮影開始日時が未指定の場合は自動推定する(T-112の優先順位:
      動画内メタデータ creation_time → 取込元の更新時刻 origin_modified_time
      (Drive の modifiedTime 等)→ ローカルファイル時刻)。自動推定は常に
      certainty='estimated'(確定として自動断定しない。D-26)
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

    # 撮影開始日時(未指定なら優先順位に従って自動推定 = estimated)
    _validate_recording_start(recording_start_basis, recording_start_certainty)
    file_mtime_iso = (
        datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        .strftime(_ISO_UTC)
    )
    recording_start_source = SOURCE_CALLER
    if recording_started_at is None:
        (recording_started_at, recording_start_basis, recording_start_certainty,
         recording_start_source) = _estimate_recording_start(
            metadata, origin_modified_time, file_mtime_iso)

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
        _finalize_exclusive(dest_part, dest_final, sha256, source_size)  # 排他的確定
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
        recording_started_at=recording_started_at,
        recording_start_basis=recording_start_basis,
        recording_start_certainty=recording_start_certainty,
        recording_start_source=recording_start_source,
    )
