"""DB接続・マイグレーション基盤(T-004)。

- SQLite(標準ライブラリ sqlite3)+ SQLファイルによる番号付きマイグレーション。
  方式選定の理由・代替案は DECISIONS.md D-23 を参照。
- 外部キー制約は接続ごとに必ず有効化し、有効化できない場合は接続を拒否する。
- スキーマは DATA_MODEL.md を正とする。追記専用エンティティはトリガーで保護される
  (仕組みと限界は D-23)。
"""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

from bio_observer.db.ids import utc_now_iso

_MIGRATION_NAME = re.compile(r"^(\d{4})_(.+)\.sql$")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """DBへ接続する。外部キー制約を有効化し、確認できなければ例外を送出する。"""
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    (enabled,) = conn.execute("PRAGMA foreign_keys").fetchone()
    if enabled != 1:
        conn.close()
        raise RuntimeError("SQLiteの外部キー制約を有効化できませんでした")
    return conn


def available_migrations() -> list[tuple[int, str, str]]:
    """パッケージ同梱のマイグレーション一覧を (version, name, sql) で返す(昇順)。"""
    migrations: list[tuple[int, str, str]] = []
    root = resources.files("bio_observer.db") / "migrations"
    for entry in root.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            migrations.append((int(match.group(1)), match.group(2), entry.read_text("utf-8")))
    migrations.sort(key=lambda m: m[0])
    versions = [m[0] for m in migrations]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"マイグレーション番号が重複しています: {versions}")
    return migrations


def schema_version(conn: sqlite3.Connection) -> int:
    """適用済みの最新スキーマバージョン(未初期化なら0)。"""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if not exists:
        return 0
    (version,) = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return version


def migrate(conn: sqlite3.Connection, *, target: int | None = None) -> list[int]:
    """未適用のマイグレーションを順に適用し、適用したバージョン一覧を返す。

    各マイグレーションは1トランザクションで適用する(失敗時はそのバージョンごと
    ロールバックされ、途中まで適用済みの状態からの再実行で残りが適用される)。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    current = schema_version(conn)
    applied: list[int] = []
    for version, name, sql in available_migrations():
        if version <= current:
            continue
        if target is not None and version > target:
            break
        try:
            conn.executescript("BEGIN;\n" + sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utc_now_iso()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(version)
    return applied
