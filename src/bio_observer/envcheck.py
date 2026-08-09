"""実行環境の確認(T-003:MVP実装前の最低限チェック)。

使い方:
    bio-observer-envcheck            # インストール後
    python -m bio_observer.envcheck  # リポジトリ直下から

FFmpeg/FFprobe の存在とバージョン、Python バージョン、設定の読み込みを確認する。
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from bio_observer.config import StorageConfig

REQUIRED_PYTHON = (3, 11)


def check_python() -> tuple[bool, str]:
    ok = sys.version_info[:2] == REQUIRED_PYTHON
    return ok, f"Python {sys.version.split()[0]} (要求: {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}.x)"


def check_command(cmd: str) -> tuple[bool, str]:
    path = shutil.which(cmd)
    if not path:
        return False, f"{cmd}: 見つかりません(PATH または .env の BIO_OBSERVER_FFMPEG/FFPROBE を確認)"
    result = subprocess.run(
        [path, "-version"], capture_output=True, text=True, timeout=30
    )
    first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""
    return result.returncode == 0, f"{cmd}: {first_line}"


def check_config() -> tuple[bool, str]:
    config = StorageConfig.load()
    return True, f"設定読み込みOK(DATA_ROOT={config.data_root})"


def main() -> int:
    config = StorageConfig.load()
    checks = [
        check_python(),
        check_command(config.ffmpeg),
        check_command(config.ffprobe),
        check_config(),
    ]
    all_ok = True
    for ok, message in checks:
        print(f"[{'OK' if ok else 'NG'}] {message}")
        all_ok = all_ok and ok
    print("環境確認: " + ("すべてOK" if all_ok else "NGあり(上記を修正してください)"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
