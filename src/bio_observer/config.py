"""環境設定の読み込み。

保存場所の方針は STORAGE.md、変数一覧は .env.example を参照。
正確な座標・秘密情報は設定に含めない(SECURITY.md / D-12)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PREFIX = "BIO_OBSERVER_"


@dataclass(frozen=True)
class StorageConfig:
    """データ保存場所。DATA_ROOT 配下の既定サブディレクトリは STORAGE.md に従う。"""

    data_root: Path
    originals_dir: Path
    derived_dir: Path
    models_dir: Path
    db_path: Path
    logs_dir: Path
    ffmpeg: str
    ffprobe: str
    tz: str
    # 動画内メタデータの日時にタイムゾーン表記がない場合の解釈条件(機器・調査設定
    # に基づく明示指定。例 "+09:00" / "Asia/Tokyo")。未設定なら表記なしは不採用(T-112)
    media_naive_timezone: str | None = None

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "StorageConfig":
        load_dotenv(env_file, override=False)

        data_root = Path(os.environ.get(f"{_PREFIX}DATA_ROOT", "data"))

        def _dir(name: str, default: str) -> Path:
            value = os.environ.get(f"{_PREFIX}{name}", "")
            return Path(value) if value else data_root / default

        return cls(
            data_root=data_root,
            originals_dir=_dir("ORIGINALS_DIR", "originals"),
            derived_dir=_dir("DERIVED_DIR", "derived"),
            models_dir=_dir("MODELS_DIR", "models"),
            db_path=_dir("DB_PATH", "db/bio_observer.sqlite3"),
            logs_dir=_dir("LOGS_DIR", "logs"),
            ffmpeg=os.environ.get(f"{_PREFIX}FFMPEG", "") or "ffmpeg",
            ffprobe=os.environ.get(f"{_PREFIX}FFPROBE", "") or "ffprobe",
            tz=os.environ.get(f"{_PREFIX}TZ", "Asia/Tokyo"),
            media_naive_timezone=os.environ.get(f"{_PREFIX}MEDIA_NAIVE_TIMEZONE", "") or None,
        )
