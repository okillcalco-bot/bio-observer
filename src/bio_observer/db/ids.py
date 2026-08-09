"""不透明IDとUTC日時のヘルパー。

- ID方針(D-23):"<エンティティ略号>_<uuid4hex>"。地点名・種名等の表示名を
  ID・ファイルパスに含めない(SECURITY.md / STORAGE.md)。
- 日時方針(D-6):UTCのISO-8601文字列("YYYY-MM-DDTHH:MM:SSZ")で保存する。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# エンティティ略号(DATA_MODEL.md 3.1〜3.18 に対応)
ID_PREFIXES = frozenset({
    "prj",   # project
    "site",  # site
    "stn",   # station
    "ses",   # survey_session
    "med",   # media_asset
    "run",   # analysis_run
    "vdet",  # visual_detection
    "adet",  # audio_detection
    "rev",   # review
    "sp",    # species
    "ind",   # individual
    "beh",   # behavior
    "link",  # detection_link
    "exp",   # export
    "alog",  # access_log
    "dast",  # derived_asset
    "step",  # job_step
    "evt",   # run_event
    "ref",   # reference_observation
})

_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")


def new_id(prefix: str) -> str:
    """不透明IDを生成する。表示名由来の文字列をprefixに使わないこと。"""
    if prefix not in ID_PREFIXES:
        if not _PREFIX_PATTERN.match(prefix):
            raise ValueError(f"不正なIDプレフィックス: {prefix!r}")
        raise ValueError(
            f"未登録のIDプレフィックス: {prefix!r}(ids.ID_PREFIXES へ追加してから使うこと)"
        )
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_now_iso() -> str:
    """現在時刻をUTCのISO-8601文字列("...Z")で返す(D-6)。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_utc_iso(value: datetime) -> str:
    """awareなdatetimeをUTCのISO-8601文字列へ変換する。naiveは拒否する。"""
    if value.tzinfo is None:
        raise ValueError("naive datetimeは保存できません。タイムゾーンを付与してください(D-6)")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
