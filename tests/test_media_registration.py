"""T-101 メディア登録のテスト。

実地データは使わず、FFmpegで生成した合成メディアのみを使用する(SECURITY.md)。
"""

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


def test_cleanup_when_atomic_rename_fails(db, seed, storage, sample_video, monkeypatch):
    """rename失敗時にDB行・一時ファイル・確定ファイルを残さない。"""
    def boom(src, dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(media_registry.os, "replace", boom)
    sha = compute_sha256(sample_video)
    with pytest.raises(OSError):
        register_media(db, sample_video, seed["session"], storage=storage)
    (count,) = db.execute("SELECT COUNT(*) FROM media_asset WHERE sha256 = ?",
                          (sha,)).fetchone()
    assert count == 0
    assert _leftover_files(storage) == []


def test_unknown_session_rejected_before_copy(db, storage, sample_video):
    with pytest.raises(media_registry.MediaRegistrationError, match="SurveySession"):
        register_media(db, sample_video, "ses_deadbeef", storage=storage)
    assert _leftover_files(storage) == []


def test_probe_media_reports_streams(sample_video, sample_wav):
    video_meta = probe_media(sample_video)
    assert (video_meta.media_type, video_meta.codec) == ("video", "h264")
    audio_meta = probe_media(sample_wav)
    assert audio_meta.media_type == "audio"
    assert audio_meta.sample_rate == 48000
