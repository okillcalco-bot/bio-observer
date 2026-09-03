"""T-101 メディア登録のテスト。

実地データは使わず、FFmpegで生成した合成メディアのみを使用する(SECURITY.md)。
"""

import errno
import hashlib
import shutil
import sqlite3
import subprocess

import pytest

from bio_observer.config import StorageConfig
from bio_observer import media_registry
from bio_observer.media_registry import (
    CopyVerificationError,
    DuplicateMediaError,
    InsufficientSpaceError,
    PathCollisionError,
    ProbeError,
    compute_sha256,
    probe_media,
    register_media,
)


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "field_recording_original.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=800:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


@pytest.fixture(scope="module")
def sample_wav(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "ambient_original.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=2000:duration=1",
         "-ar", "48000", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


@pytest.fixture()
def storage(tmp_path) -> StorageConfig:
    root = tmp_path / "store"
    return StorageConfig(
        data_root=root,
        originals_dir=root / "originals",
        derived_dir=root / "derived",
        models_dir=root / "models",
        db_path=root / "db" / "test.sqlite3",
        logs_dir=root / "logs",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        tz="Asia/Tokyo",
    )


def _leftover_files(storage: StorageConfig) -> list:
    if not storage.originals_dir.exists():
        return []
    return [p for p in storage.originals_dir.rglob("*") if p.is_file()]


def test_register_video(db, seed, storage, sample_video):
    original_bytes = sample_video.read_bytes()
    result = register_media(db, sample_video, seed["session"], storage=storage)

    row = db.execute("SELECT * FROM media_asset WHERE id = ?",
                     (result.media_asset_id,)).fetchone()
    assert row["media_type"] == "video"
    assert row["codec"] == "h264"
    assert (row["width"], row["height"]) == (320, 240)
    assert row["fps"] == pytest.approx(10, abs=0.1)
    assert row["sample_rate"] == 44100 and row["channels"] >= 1
    assert row["duration_seconds"] == pytest.approx(2.0, abs=0.5)
    # 撮影開始日時:ファイル時刻からの推定は estimated 扱い(自動で confirmed にしない)
    assert row["recording_start_basis"] == "file_time"
    assert row["recording_start_certainty"] == "estimated"
    assert row["recording_started_at"].endswith("Z")

    # 保存先は不透明IDのみで構成され、元ファイル名を露出しない
    assert "field_recording" not in row["relative_path"]
    parts = row["relative_path"].split("/")
    assert parts[0].startswith("prj_") and parts[1].startswith("site_")
    assert parts[2].startswith("stn_") and parts[3].startswith("ses_")
    assert parts[4] == f"{result.media_asset_id}.mp4"

    # コピーが原本と一致し、原本は無傷
    dest = storage.originals_dir / row["relative_path"]
    assert dest.is_file()
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == row["sha256"]
    assert sample_video.read_bytes() == original_bytes
    # 一時ファイルが残っていない
    assert all(not p.name.endswith(".part") for p in _leftover_files(storage))


def test_register_audio_wav(db, seed, storage, sample_wav):
    result = register_media(db, sample_wav, seed["session"], storage=storage)
    row = db.execute("SELECT media_type, sample_rate, relative_path FROM media_asset "
                     "WHERE id = ?", (result.media_asset_id,)).fetchone()
    assert row["media_type"] == "audio"
    assert row["sample_rate"] == 48000
    assert "ambient" not in row["relative_path"]


def test_sha256_streaming_matches_full_read(sample_video):
    assert compute_sha256(sample_video) == hashlib.sha256(
        sample_video.read_bytes()).hexdigest()


def test_duplicate_registration_rejected(db, seed, storage, sample_video):
    first = register_media(db, sample_video, seed["session"], storage=storage)
    with pytest.raises(DuplicateMediaError) as exc:
        register_media(db, sample_video, seed["session"], storage=storage)
    assert exc.value.existing_id == first.media_asset_id
    (count,) = db.execute("SELECT COUNT(*) FROM media_asset WHERE sha256 = ?",
                          (first.sha256,)).fetchone()
    assert count == 1
    assert all(not p.name.endswith(".part") for p in _leftover_files(storage))


def test_broken_file_rejected_without_leftovers(db, seed, storage, tmp_path):
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"this is not a video" * 1000)
    (before,) = db.execute("SELECT COUNT(*) FROM media_asset").fetchone()
    with pytest.raises(ProbeError):
        register_media(db, broken, seed["session"], storage=storage)
    (after,) = db.execute("SELECT COUNT(*) FROM media_asset").fetchone()
    assert after == before
    assert _leftover_files(storage) == []


def test_missing_file_and_unsupported_extension(db, seed, storage, tmp_path, sample_video):
    with pytest.raises(ProbeError):
        register_media(db, tmp_path / "nonexistent.mp4", seed["session"], storage=storage)
    odd = tmp_path / "renamed.xyz"
    shutil.copyfile(sample_video, odd)
    with pytest.raises(ProbeError, match="対応形式"):
        register_media(db, odd, seed["session"], storage=storage)
    assert _leftover_files(storage) == []


def test_insufficient_space(db, seed, storage, sample_video, monkeypatch):
    class FakeUsage:
        free = 10  # bytes
    monkeypatch.setattr(media_registry.shutil, "disk_usage", lambda _: FakeUsage)
    with pytest.raises(InsufficientSpaceError):
        register_media(db, sample_video, seed["session"], storage=storage)
    assert _leftover_files(storage) == []


def test_confirmed_certainty_requires_human_basis(db, seed, storage, sample_video):
    # 自動由来(file_time/metadata)を confirmed として断定できない
    with pytest.raises(ValueError):
        register_media(db, sample_video, seed["session"], storage=storage,
                       recording_started_at="2026-08-01T00:00:00Z",
                       recording_start_basis="file_time",
                       recording_start_certainty="confirmed")
    # 人の入力(manual)なら confirmed を許可
    result = register_media(db, sample_video, seed["session"], storage=storage,
                            recording_started_at="2026-08-01T00:00:00Z",
                            recording_start_basis="manual",
                            recording_start_certainty="confirmed")
    row = db.execute("SELECT recording_start_basis, recording_start_certainty "
                     "FROM media_asset WHERE id = ?",
                     (result.media_asset_id,)).fetchone()
    assert tuple(row) == ("manual", "confirmed")


def test_cleanup_when_finalize_fails(db, seed, storage, sample_video, monkeypatch):
    """確定処理失敗時にDB行・一時ファイル・確定ファイルを残さない。"""
    def boom(*args):
        raise OSError("simulated finalize failure")
    monkeypatch.setattr(media_registry, "_finalize_exclusive", boom)
    sha = compute_sha256(sample_video)
    with pytest.raises(OSError):
        register_media(db, sample_video, seed["session"], storage=storage)
    (count,) = db.execute("SELECT COUNT(*) FROM media_asset WHERE sha256 = ?",
                          (sha,)).fetchone()
    assert count == 0
    assert _leftover_files(storage) == []


def _fixed_media_id(monkeypatch, media_id: str):
    """new_id('med')だけを固定IDへ差し替える(他のプレフィックスは素通し)。"""
    real_new_id = media_registry.new_id

    def fake(prefix):
        return media_id if prefix == "med" else real_new_id(prefix)

    monkeypatch.setattr(media_registry, "new_id", fake)


def test_id_collision_never_touches_existing_row_or_files(
        db, seed, storage, sample_video, monkeypatch):
    """ID衝突でDB INSERTが失敗しても、既存行・既存ファイルへ一切触れない。"""
    _fixed_media_id(monkeypatch, seed["media"])  # 既存MediaAssetの主キーと衝突させる
    before_row = dict(db.execute("SELECT * FROM media_asset WHERE id = ?",
                                 (seed["media"],)).fetchone())
    with pytest.raises(sqlite3.IntegrityError):
        register_media(db, sample_video, seed["session"], storage=storage)
    after_row = dict(db.execute("SELECT * FROM media_asset WHERE id = ?",
                                (seed["media"],)).fetchone())
    assert after_row == before_row  # 既存行は無傷
    assert _leftover_files(storage) == []  # 本呼出しが作ったファイルも残っていない


def test_existing_final_file_not_overwritten_or_deleted(
        db, seed, storage, sample_video, monkeypatch):
    """確定先に既存ファイルがある場合、上書き・削除せず登録を失敗させる。"""
    _fixed_media_id(monkeypatch, "med_collision0000000000000000000")
    dest = (storage.originals_dir / seed["project"] / seed["site"]
            / seed["station"] / seed["session"]
            / "med_collision0000000000000000000.mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"existing original asset - must not change"
    dest.write_bytes(sentinel)

    (rows_before,) = db.execute("SELECT COUNT(*) FROM media_asset").fetchone()
    with pytest.raises(PathCollisionError):
        register_media(db, sample_video, seed["session"], storage=storage)
    assert dest.read_bytes() == sentinel  # 内容・存在とも無傷
    (rows_after,) = db.execute("SELECT COUNT(*) FROM media_asset").fetchone()
    assert rows_after == rows_before
    assert [p for p in _leftover_files(storage) if p != dest] == []


def test_finalize_is_exclusive_even_after_preflight(
        db, seed, storage, sample_video, monkeypatch):
    """事前確認をすり抜けても、確定処理自体が既存ファイルを上書きしない(排他的作成)。"""
    _fixed_media_id(monkeypatch, "med_collision1111111111111111111")
    dest = (storage.originals_dir / seed["project"] / seed["site"]
            / seed["station"] / seed["session"]
            / "med_collision1111111111111111111.mp4")
    sentinel = b"late-arriving existing asset"

    # 事前確認の後・確定の直前に既存ファイルが現れる状況(TOCTOU)を再現
    real_copy = media_registry._copy_with_hash

    def copy_then_plant(source, part):
        digest = real_copy(source, part)
        dest.write_bytes(sentinel)
        return digest

    monkeypatch.setattr(media_registry, "_copy_with_hash", copy_then_plant)
    with pytest.raises(PathCollisionError):
        register_media(db, sample_video, seed["session"], storage=storage)
    assert dest.read_bytes() == sentinel  # os.linkの排他的作成により無傷
    assert [p for p in _leftover_files(storage) if p != dest] == []


def test_unknown_session_rejected_before_copy(db, storage, sample_video):
    with pytest.raises(media_registry.MediaRegistrationError, match="SurveySession"):
        register_media(db, sample_video, "ses_deadbeef", storage=storage)
    assert _leftover_files(storage) == []


def test_part_discard_failure_rolls_back_final(db, seed, storage, sample_video, monkeypatch):
    """link成功後に一時ファイル削除が失敗しても、DB行のない確定ファイルを残さない。"""
    def boom(part):
        raise OSError("simulated part unlink failure")
    monkeypatch.setattr(media_registry, "_discard_part", boom)
    sha = compute_sha256(sample_video)
    with pytest.raises(OSError):
        register_media(db, sample_video, seed["session"], storage=storage)
    (count,) = db.execute("SELECT COUNT(*) FROM media_asset WHERE sha256 = ?",
                          (sha,)).fetchone()
    assert count == 0
    assert _leftover_files(storage) == []  # 確定ファイルも一時ファイルも残らない


def _force_link_unsupported(monkeypatch):
    """ハードリンク非対応FS(exFAT等)を再現:os.linkがEPERMで失敗する。"""
    def no_link(src, dst):
        raise OSError(errno.EPERM, "hard links not supported (simulated)")
    monkeypatch.setattr(media_registry.os, "link", no_link)


def test_fallback_without_hardlink_registers_exclusively(
        db, seed, storage, sample_video, monkeypatch):
    """ハードリンク非対応FSでもO_EXCLの排他的作成で登録が成功する。"""
    _force_link_unsupported(monkeypatch)
    result = register_media(db, sample_video, seed["session"], storage=storage)
    dest = storage.originals_dir / result.relative_path
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == result.sha256
    assert all(not p.name.endswith(".part") for p in _leftover_files(storage))


def test_fallback_never_overwrites_existing_final(
        db, seed, storage, sample_video, monkeypatch):
    """ハードリンク非対応FSのフォールバックでも既存確定ファイルを上書きしない。"""
    _force_link_unsupported(monkeypatch)
    _fixed_media_id(monkeypatch, "med_collision2222222222222222222")
    dest = (storage.originals_dir / seed["project"] / seed["site"]
            / seed["station"] / seed["session"]
            / "med_collision2222222222222222222.mp4")
    sentinel = b"existing asset on exfat - must not change"

    # 事前確認の後に既存ファイルが現れるTOCTOU状況でも、O_EXCLが上書きを拒否する
    real_copy = media_registry._copy_with_hash

    def copy_then_plant(source, part):
        digest = real_copy(source, part)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(sentinel)
        return digest

    monkeypatch.setattr(media_registry, "_copy_with_hash", copy_then_plant)
    with pytest.raises(PathCollisionError):
        register_media(db, sample_video, seed["session"], storage=storage)
    assert dest.read_bytes() == sentinel
    assert [p for p in _leftover_files(storage) if p != dest] == []


def test_fallback_copy_corruption_fully_rolled_back(
        db, seed, storage, sample_video, monkeypatch):
    """フォールバックコピーが破損した場合、final読み戻し照合で検知し完全に取り消す。"""
    _force_link_unsupported(monkeypatch)

    class TruncatingWriter:
        """書込みを途中で打ち切り、コピー破損(サイズ・ハッシュ不一致)を注入する。"""

        def __init__(self, inner):
            self._inner = inner

        def write(self, data):
            return self._inner.write(data[: max(1, len(data) // 2)])

        def flush(self):
            self._inner.flush()

        def fileno(self):
            return self._inner.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    real_fdopen = media_registry.os.fdopen

    def corrupting_fdopen(fd, mode="r", *args, **kwargs):
        return TruncatingWriter(real_fdopen(fd, mode, *args, **kwargs))

    monkeypatch.setattr(media_registry.os, "fdopen", corrupting_fdopen)
    sha = compute_sha256(sample_video)
    with pytest.raises(CopyVerificationError):
        register_media(db, sample_video, seed["session"], storage=storage)
    (count,) = db.execute("SELECT COUNT(*) FROM media_asset WHERE sha256 = ?",
                          (sha,)).fetchone()
    assert count == 0          # DB行なし
    assert _leftover_files(storage) == []  # final・.part とも残らない


def test_link_failure_with_other_errno_does_not_fall_back(
        db, seed, storage, sample_video, monkeypatch):
    """ハードリンク非対応以外のOSError(例:ENOSPC)はフォールバックせず失敗する。"""
    def link_enospc(src, dst):
        raise OSError(errno.ENOSPC, "no space left (simulated)")
    monkeypatch.setattr(media_registry.os, "link", link_enospc)
    with pytest.raises(OSError) as exc:
        register_media(db, sample_video, seed["session"], storage=storage)
    assert exc.value.errno == errno.ENOSPC
    assert not isinstance(exc.value, media_registry.MediaRegistrationError)
    assert _leftover_files(storage) == []


@pytest.fixture(scope="module")
def sample_video_with_creation_time(tmp_path_factory):
    """動画内メタデータ creation_time を持つ合成動画(UTC表記)。"""
    path = tmp_path_factory.mktemp("media") / "with_creation_time.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-c:v", "libx264", "-metadata", "creation_time=2026-07-29T08:01:00Z",
         str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


def test_parse_timestamp_explicit_timezone_variants():
    from bio_observer.media_registry import TZ_EXPLICIT, TZ_INVALID, TZ_MISSING, parse_timestamp
    for raw in ("2026-07-29T08:01:00.000000Z", "2026-07-29T17:01:00+0900",
                "2026-07-29T17:01:00+09:00", "2026-07-29 03:01:00-05:00"):
        parsed = parse_timestamp(raw)
        assert parsed.normalized_value == "2026-07-29T08:01:00Z", raw
        assert parsed.timezone == TZ_EXPLICIT and parsed.raw_value == raw
    assert parse_timestamp("not-a-date").timezone == TZ_INVALID
    assert parse_timestamp(None).timezone == TZ_MISSING


def test_naive_timestamp_is_rejected_without_interpretation_basis():
    """タイムゾーン表記なしはUTCとして自動採用しない(timezone_unknown→不採用)。"""
    from bio_observer.media_registry import TZ_UNKNOWN, parse_timestamp
    parsed = parse_timestamp("2026-07-29 17:01:00")
    assert parsed.normalized_value is None
    assert parsed.timezone == TZ_UNKNOWN
    assert "解釈根拠がない" in parsed.interpretation


def test_naive_timestamp_adopted_only_with_explicit_assumption():
    """機器・調査設定に基づく解釈条件が与えられた場合のみ採用し、条件を記録する。"""
    from bio_observer.media_registry import TZ_ASSUMED, TZ_INVALID, parse_timestamp
    for assumption in ("+09:00", "Asia/Tokyo"):
        parsed = parse_timestamp("2026-07-29 17:01:00", naive_timezone=assumption,
                                 naive_timezone_origin="BIO_OBSERVER_MEDIA_NAIVE_TIMEZONE")
        assert parsed.normalized_value == "2026-07-29T08:01:00Z"
        assert parsed.timezone == TZ_ASSUMED
        assert assumption in parsed.interpretation
        assert "BIO_OBSERVER_MEDIA_NAIVE_TIMEZONE" in parsed.interpretation
    # 解釈条件そのものが不正なら採用しない
    assert parse_timestamp("2026-07-29 17:01:00",
                           naive_timezone="Mars/Olympus").timezone == TZ_INVALID


def test_candidate_records_are_reproducible(sample_video):
    """各候補について source/raw/normalized/timezone/解釈条件/採否/不採用理由を保持する。"""
    from bio_observer.media_registry import MediaMetadata, evaluate_recording_start_candidates
    meta = MediaMetadata(media_type="video", codec="h264", width=1, height=1, fps=1,
                         sample_rate=None, channels=None, duration_seconds=1.0,
                         creation_time_raw="2026-07-29 17:01:00",  # 表記なし
                         creation_time_tag="format.tags.creation_time")
    epoch = 1785000000.0
    # 解釈条件なし:①はtimezone_unknownで不採用 → ②を採用
    candidates = evaluate_recording_start_candidates(
        meta, "2026-08-09T11:35:51Z", epoch)
    assert [c["source"] for c in candidates] == [
        "media_metadata_creation_time", "origin_modified_time", "local_file_mtime"]
    first, second, third = candidates
    assert first["raw_value"] == "2026-07-29 17:01:00" and first["normalized_value"] is None
    assert first["timezone"] == "timezone_unknown" and first["adopted"] is False
    assert "timezone_unknown" in first["rejection_reason"]
    assert second["adopted"] is True and second["normalized_value"] == "2026-08-09T11:35:51Z"
    assert second["timezone"] == "explicit" and second["rejection_reason"] is None
    assert third["adopted"] is False and "優先度の高い候補" in third["rejection_reason"]
    assert third["normalized_value"] is not None and third["raw_value"] == repr(epoch)
    # 解釈条件あり:①を採用し、条件が記録される
    candidates = evaluate_recording_start_candidates(
        meta, "2026-08-09T11:35:51Z", epoch, naive_timezone="+09:00",
        naive_timezone_origin="BIO_OBSERVER_MEDIA_NAIVE_TIMEZONE")
    assert candidates[0]["adopted"] is True
    assert candidates[0]["normalized_value"] == "2026-07-29T08:01:00Z"
    assert candidates[0]["timezone"] == "assumed"
    assert "+09:00" in candidates[0]["interpretation"]
    assert candidates[1]["adopted"] is False


def test_recording_start_priority_metadata_first(db, seed, storage,
                                                 sample_video_with_creation_time):
    """優先1:動画内メタデータのcreation_timeを採用(取込元時刻・ファイル時刻より優先)。"""
    result = register_media(db, sample_video_with_creation_time, seed["session"],
                            storage=storage,
                            origin_modified_time="2026-08-09T00:00:00Z")
    row = db.execute("SELECT recording_started_at, recording_start_basis, "
                     "recording_start_certainty FROM media_asset WHERE id = ?",
                     (result.media_asset_id,)).fetchone()
    assert tuple(row) == ("2026-07-29T08:01:00Z", "metadata", "estimated")
    assert result.recording_start_source == "media_metadata_creation_time"
    assert result.metadata.creation_time == "2026-07-29T08:01:00Z"
    assert result.metadata.creation_time_tag == "format.tags.creation_time"
    # 候補記録が結果に含まれる(①採用、②③は不採用理由つき)
    assert [c["adopted"] for c in result.recording_start_candidates] == [True, False, False]
    assert result.recording_start_candidates[0]["timezone"] == "explicit"


def test_recording_start_priority_origin_modified_second(db, seed, storage, sample_video):
    """優先2:creation_timeがなければ取込元(Drive等)の更新時刻を採用(basis=file_time)。"""
    assert probe_media(sample_video).creation_time is None
    result = register_media(db, sample_video, seed["session"], storage=storage,
                            origin_modified_time="2026-08-09T01:02:03.456Z")
    row = db.execute("SELECT recording_started_at, recording_start_basis, "
                     "recording_start_certainty FROM media_asset WHERE id = ?",
                     (result.media_asset_id,)).fetchone()
    assert tuple(row) == ("2026-08-09T01:02:03Z", "file_time", "estimated")
    assert result.recording_start_source == "origin_modified_time"


def test_recording_start_priority_local_mtime_last(db, seed, storage, sample_video):
    """優先3:creation_timeも取込元時刻もなければローカルファイル時刻(最後の手段)。"""
    result = register_media(db, sample_video, seed["session"], storage=storage)
    assert result.recording_start_source == "local_file_mtime"
    assert result.recording_start_basis == "file_time"
    assert result.recording_start_certainty == "estimated"


def test_metadata_basis_cannot_be_confirmed_automatically(
        db, seed, storage, sample_video_with_creation_time):
    """自動取得(metadata)由来を confirmed として指定できない(人の補正のみ)。"""
    with pytest.raises(ValueError):
        register_media(db, sample_video_with_creation_time, seed["session"],
                       storage=storage,
                       recording_started_at="2026-07-29T08:01:00Z",
                       recording_start_basis="metadata",
                       recording_start_certainty="confirmed")


def test_probe_media_reports_streams(sample_video, sample_wav):
    video_meta = probe_media(sample_video)
    assert (video_meta.media_type, video_meta.codec) == ("video", "h264")
    audio_meta = probe_media(sample_wav)
    assert audio_meta.media_type == "audio"
    assert audio_meta.sample_rate == 48000
