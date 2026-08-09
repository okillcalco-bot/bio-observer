"""テスト共通フィクスチャ(T-004:テスト用一時DB)。

テストデータの表示名・パスにはダミー値のみを使い、実在の地点名・座標・
希少種の実個体情報を書かない(SECURITY.md)。
"""

import sqlite3

import pytest

from bio_observer.db import connect, migrate
from bio_observer.db.ids import new_id, utc_now_iso


@pytest.fixture()
def db(tmp_path) -> sqlite3.Connection:
    """一時ファイル上に最新スキーマを構築した接続を返す。"""
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def insert(conn: sqlite3.Connection, table: str, **cols) -> str:
    """created_at/updated_at を補完して1行INSERTし、idを返す。"""
    now = utc_now_iso()
    cols.setdefault("created_at", now)
    has_updated = any(
        row["name"] == "updated_at"
        for row in conn.execute(f"PRAGMA table_info({table})")
    )
    if has_updated:
        cols.setdefault("updated_at", now)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values()))
    return cols["id"]


@pytest.fixture()
def seed(db) -> dict:
    """project→site→station→survey_session→media_asset→analysis_run の最小チェーン。"""
    ids = {}
    ids["project"] = insert(db, "project", id=new_id("prj"), name="テストプロジェクト")
    ids["site"] = insert(
        db, "site", id=new_id("site"), project_id=ids["project"], name="テスト地点A",
        rounded_position="dummy-mesh-0000", rounding_level="mesh_10km",
    )
    ids["station"] = insert(
        db, "station", id=new_id("stn"), site_id=ids["site"], name="ST-1",
        equipment_type="camera",
    )
    ids["session"] = insert(
        db, "survey_session", id=new_id("ses"), station_id=ids["station"],
        survey_date="2026-08-01",
    )
    ids["media"] = insert(
        db, "media_asset", id=new_id("med"), survey_session_id=ids["session"],
        media_type="video",
        relative_path=f"{ids['project']}/{ids['site']}/{ids['station']}/{ids['session']}/video1.mp4",
        sha256="0" * 64,
        recording_started_at="2026-08-01T00:00:00Z",
        recording_start_basis="metadata",
        timezone="Asia/Tokyo",
    )
    ids["run"] = insert(
        db, "analysis_run", id=new_id("run"), media_asset_id=ids["media"],
        analysis_type="audio", status="running",
        model_name="birdnet", model_version="0.2.16",
    )
    ids["species"] = insert(
        db, "species", id=new_id("sp"), scientific_name="Testus dummius",
        japanese_name="テストドリ",
    )
    db.commit()
    return ids
