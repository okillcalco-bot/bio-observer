"""マイグレーション基盤のテスト(空DBからの構築/段階的アップグレード/冪等性)。"""

import pytest

from bio_observer.db import available_migrations, connect, migrate, schema_version
from bio_observer.db.ids import ID_PREFIXES, new_id, to_utc_iso, utc_now_iso

EXPECTED_TABLES = {
    "project", "site", "station", "survey_session", "media_asset",
    "analysis_run", "job_step", "run_event",
    "visual_detection", "audio_detection", "review",
    "species", "individual", "behavior",
    "detection_link", "derived_asset", "derived_asset_detection",
    "reference_observation", "export", "access_log",
}


def tables(conn):
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    } - {"schema_migrations"}


def test_fresh_build(tmp_path):
    """空のSQLite DBへ最新スキーマを構築できる。"""
    conn = connect(tmp_path / "fresh.sqlite3")
    assert schema_version(conn) == 0
    applied = migrate(conn)
    assert applied, "適用されたマイグレーションがない"
    assert tables(conn) == EXPECTED_TABLES
    assert schema_version(conn) == max(v for v, _, _ in available_migrations())
    conn.close()


def test_stepwise_upgrade(tmp_path):
    """初回から最新まで、1バージョンずつ段階的にマイグレーションできる。"""
    conn = connect(tmp_path / "upgrade.sqlite3")
    versions = [v for v, _, _ in available_migrations()]
    for version in versions:
        applied = migrate(conn, target=version)
        assert applied == [version]
        assert schema_version(conn) == version
    assert tables(conn) == EXPECTED_TABLES
    conn.close()


def test_migrate_is_idempotent(db):
    """適用済みDBへの再実行は何も適用しない。"""
    assert migrate(db) == []


def test_foreign_keys_enforced_on_connect(db):
    (enabled,) = db.execute("PRAGMA foreign_keys").fetchone()
    assert enabled == 1


def test_integrity_check(db):
    (result,) = db.execute("PRAGMA integrity_check").fetchone()
    assert result == "ok"


def test_no_exact_coordinate_columns(db):
    """正確な座標を保存する列が存在しない(D-12)。丸め表現列(rounded_*)のみ許可。"""
    forbidden = {"lat", "latitude", "lon", "lng", "longitude", "easting", "northing",
                 "x", "y", "coordinates", "position", "location", "gps"}
    for table in tables(db):
        for row in db.execute(f"PRAGMA table_info({table})"):
            name = row["name"].lower()
            if name.startswith("rounded_"):
                continue
            parts = set(name.split("_"))
            assert not (parts & forbidden), (
                f"{table}.{row['name']} は座標列の可能性があります(D-12違反)"
            )


def test_new_id_policy():
    """不透明ID:登録済みプレフィックス+uuid4hex。未登録・不正名は拒否。"""
    for prefix in ID_PREFIXES:
        value = new_id(prefix)
        head, _, tail = value.partition("_")
        assert head == prefix and len(tail) == 32 and all(c in "0123456789abcdef" for c in tail)
    with pytest.raises(ValueError):
        new_id("hachikuma-nest-siteA")  # 表示名由来のプレフィックスは不可
    with pytest.raises(ValueError):
        new_id("camera1")  # 未登録


def test_utc_iso_helpers():
    from datetime import datetime, timezone, timedelta

    assert utc_now_iso().endswith("Z")
    jst = timezone(timedelta(hours=9))
    assert to_utc_iso(datetime(2026, 8, 1, 9, 0, 0, tzinfo=jst)) == "2026-08-01T00:00:00Z"
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 8, 1, 9, 0, 0))  # naiveは拒否(D-6)
