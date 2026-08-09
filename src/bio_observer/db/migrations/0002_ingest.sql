-- 0002_ingest: Google Drive受け箱による自動取込(T-110 / Issue #6 / D-27)
--
--  - ingest_job:取込ジョブの状態機械(discovered〜retry_required)。
--    Drive上の表示名(original_file_name)はここでのみ保持する(D-26:
--    media_asset・保存パスには露出させない)
--  - ingest_event:状態変化の追記ログ(上書きだけでなく履歴を残す。追記専用)

CREATE TABLE ingest_job (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'google_drive' CHECK (source IN ('google_drive', 'local')),
    drive_file_id TEXT NOT NULL,
    -- Drive上の表示名(取込エンティティ側でのみ保持。D-26)
    original_file_name TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    modified_time TEXT,
    -- アップロード完了判定用の直近観測(サイズ・modifiedTime・観測時刻)
    stable_probe_json TEXT,
    survey_session_id TEXT NOT NULL REFERENCES survey_session(id),
    media_asset_id TEXT REFERENCES media_asset(id),
    -- 同一ハッシュの既登録があった場合の参照(二重解析防止)
    duplicate_of_media_asset_id TEXT REFERENCES media_asset(id),
    status TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
        'discovered', 'waiting_for_upload', 'downloading', 'downloaded', 'registered',
        'queued', 'analyzing', 'uploading_results', 'completed', 'failed', 'retry_required')),
    -- retry_required からの復帰先
    resume_status TEXT CHECK (resume_status IS NULL OR resume_status IN (
        'waiting_for_upload', 'downloading', 'downloaded', 'registered',
        'queued', 'analyzing', 'uploading_results')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    -- Drive結果フォルダ名(= job id。表示用フォルダ名と不透明IDを混同しない)
    results_folder_name TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- 同じDrive File IDを二重取込しない
    UNIQUE (source, drive_file_id)
);
CREATE INDEX idx_ingest_job_status ON ingest_job(status);
CREATE INDEX idx_ingest_job_media ON ingest_job(media_asset_id);

CREATE TABLE ingest_event (
    id TEXT PRIMARY KEY,
    ingest_job_id TEXT NOT NULL REFERENCES ingest_job(id),
    occurred_at TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    message TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ingest_event_job ON ingest_event(ingest_job_id);

CREATE TRIGGER trg_ingest_event_no_update
BEFORE UPDATE ON ingest_event
BEGIN
    SELECT RAISE(ABORT, 'ingest_event is append-only (D-27)');
END;

CREATE TRIGGER trg_ingest_event_no_delete
BEFORE DELETE ON ingest_event
BEGIN
    SELECT RAISE(ABORT, 'ingest_event is append-only (D-27)');
END;
