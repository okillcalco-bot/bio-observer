"""T-003:最低限の環境確認テスト。

解析機能のテストではない(それらはT-004以降)。
実行環境が MVP 実装を開始できる状態かを確認する。
"""

import shutil
import subprocess
import sys

from bio_observer import __version__
from bio_observer.config import StorageConfig
from bio_observer.envcheck import check_command, check_python


def test_python_version():
    assert sys.version_info[:2] == (3, 11)
    ok, _ = check_python()
    assert ok


def test_package_importable():
    assert __version__


def test_ffmpeg_available():
    assert shutil.which("ffmpeg"), "ffmpeg が PATH にありません"
    ok, message = check_command("ffmpeg")
    assert ok, message


def test_ffprobe_available():
    assert shutil.which("ffprobe"), "ffprobe が PATH にありません"
    ok, message = check_command("ffprobe")
    assert ok, message


def test_ffprobe_reads_synthetic_media(tmp_path):
    """FFmpegで合成音声つき動画を生成し、FFprobeでメタデータ取得できること。

    実地データは使わない(位置情報を含むフィクスチャ禁止。SECURITY.md)。
    """
    media = tmp_path / "synthetic.mp4"
    gen = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(media),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert gen.returncode == 0, gen.stderr[-500:]

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "csv=p=0", str(media),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode == 0, probe.stderr[-500:]
    assert "video" in probe.stdout and "audio" in probe.stdout


def test_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BIO_OBSERVER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("BIO_OBSERVER_ORIGINALS_DIR", raising=False)
    config = StorageConfig.load()
    assert config.originals_dir == config.data_root / "originals"
    assert config.derived_dir == config.data_root / "derived"
    assert config.db_path == config.data_root / "db/bio_observer.sqlite3"


def test_env_example_has_no_secrets_or_coordinates():
    """テンプレートに秘密情報・座標が混入していないこと(SECURITY.md / D-12)。"""
    text = open(".env.example", encoding="utf-8").read()
    for var, value in (
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ):
        stripped = value.strip()
        assert "key" not in var.lower() or stripped == "", var
        assert "lat" not in var.lower() and "lon" not in var.lower(), var
        # 数値座標らしき値(例: 34.98, 139.87)が書かれていないこと
        assert not any(
            part.replace(".", "").replace("-", "").isdigit() and "." in part
            for part in stripped.split(",")
        ), f"{var} に座標らしき値が含まれています"
