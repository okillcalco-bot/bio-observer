-- 0001_initial: DATA_MODEL.md の全エンティティ(3.1〜3.18)
--
-- 規約:
--  - id は不透明ID(TEXT, "<prefix>_<uuid4hex>")。パス・IDに地点名・希少種名を使わない
--  - 日時は UTC の ISO-8601 TEXT("YYYY-MM-DDTHH:MM:SSZ")。D-6
--  - enum は TEXT + CHECK。区分の定義は SURVEY_METHOD.md 第3章が正
--  - JSON 列は *_json サフィックス
--  - D-12: 正確な座標を格納する列は存在しない(T-303実装後に別マイグレーションで追加)
--  - 追記専用(review / access_log / run_event)と完了後の analysis_run はトリガーで更新・削除を拒否

-- ============ マスター系 ============

CREATE TABLE project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    purpose TEXT,
    start_date TEXT,
    end_date TEXT,
    lead_name TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('planned', 'active', 'paused', 'completed', 'archived')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE site (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    name TEXT NOT NULL,
    -- D-12: 正確な座標は保存しない。丸め済み表現(メッシュコード・市区町村名等)のみ
    rounded_position TEXT,
    rounding_level TEXT,
    environment_desc TEXT,
    sensitivity_level TEXT NOT NULL DEFAULT 'normal'
        CHECK (sensitivity_level IN ('normal', 'sensitive', 'highly_sensitive')),
    is_nesting_related INTEGER NOT NULL DEFAULT 0 CHECK (is_nesting_related IN (0, 1)),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE station (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES site(id),
    name TEXT NOT NULL,
    equipment_type TEXT NOT NULL CHECK (equipment_type IN ('camera', 'recorder', 'combined')),
    equipment_model TEXT,
    placement_desc TEXT,
    azimuth_deg REAL,
    elevation_deg REAL,
    field_of_view_deg REAL,
    installed_from TEXT,
    installed_to TEXT,
    -- 設置方向・画角の変更時は新レコードを作り、前身をリンクする(DATA_MODEL.md 3.3)
    predecessor_station_id TEXT REFERENCES station(id),
    default_mask_json TEXT,
    -- 画角・Stationごとの既定解析パラメータ(Runには常にスナップショットを別途保存)
    default_analysis_params_json TEXT,
    clock_offset_seconds REAL NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (site_id, name)
);

CREATE TABLE species (
    id TEXT PRIMARY KEY,
    japanese_name TEXT,
    scientific_name TEXT NOT NULL UNIQUE,
    english_name TEXT,
    taxon_order TEXT,
    family TEXT,
    genus TEXT,
    is_raptor INTEGER NOT NULL DEFAULT 0 CHECK (is_raptor IN (0, 1)),
    is_sensitive INTEGER NOT NULL DEFAULT 0 CHECK (is_sensitive IN (0, 1)),
    external_codes_json TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE individual (
    id TEXT PRIMARY KEY,
    species_id TEXT NOT NULL REFERENCES species(id),
    label TEXT NOT NULL,
    features TEXT,
    first_observed_date TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (species_id, label)
);

CREATE TABLE behavior (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_breeding_related INTEGER NOT NULL DEFAULT 0 CHECK (is_breeding_related IN (0, 1)),
    definition TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ============ 調査・メディア ============

CREATE TABLE survey_session (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES station(id),
    survey_date TEXT NOT NULL,
    surveyor TEXT,
    weather TEXT,
    temperature_c REAL,
    wind TEXT,
    purpose TEXT,
    -- 現地野帳メモ(速報的。定量評価には reference_observation を使う。D-11)
    field_notes TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_survey_session_station ON survey_session(station_id);

CREATE TABLE media_asset (
    id TEXT PRIMARY KEY,
    survey_session_id TEXT NOT NULL REFERENCES survey_session(id),
    media_type TEXT NOT NULL CHECK (media_type IN ('video', 'audio')),
    -- STORAGE.md: 不透明IDのみで構成した設定基準の相対パス
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL UNIQUE,
    codec TEXT,
    width INTEGER,
    height INTEGER,
    fps REAL,
    sample_rate INTEGER,
    channels INTEGER,
    duration_seconds REAL,
    metadata_recorded_at TEXT,
    -- 撮影開始日時(確定値, UTC)= 共通タイムラインの基準
    recording_started_at TEXT,
    -- 算出根拠:埋め込みメタデータ/ファイル作成・更新時刻からの推定/人の入力/補正
    recording_start_basis TEXT
        CHECK (recording_start_basis IS NULL OR recording_start_basis IN ('metadata', 'file_time', 'manual', 'corrected')),
    -- 実時刻の確実性(確定/推定/不明)
    recording_start_certainty TEXT
        CHECK (recording_start_certainty IS NULL OR recording_start_certainty IN ('confirmed', 'estimated', 'unknown')),
    timezone TEXT,
    -- 原データは物理削除しない。論理削除のみ
    deleted_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (recording_started_at IS NULL OR recording_started_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);
CREATE INDEX idx_media_asset_session ON media_asset(survey_session_id);

-- 原則3(原データ不変):物理削除禁止(論理削除 deleted_at のみ)、原本同一性 sha256 は変更禁止
CREATE TRIGGER trg_media_asset_no_delete
BEFORE DELETE ON media_asset
BEGIN
    SELECT RAISE(ABORT, 'media_asset must not be deleted physically: set deleted_at instead (原則3)');
END;

CREATE TRIGGER trg_media_asset_sha256_immutable
BEFORE UPDATE OF sha256 ON media_asset
WHEN OLD.sha256 <> NEW.sha256
BEGIN
    SELECT RAISE(ABORT, 'media_asset.sha256 is immutable (原則3)');
END;

-- ============ 解析実行(D-10: 完了後凍結) ============

CREATE TABLE analysis_run (
    id TEXT PRIMARY KEY,
    media_asset_id TEXT NOT NULL REFERENCES media_asset(id),
    analysis_type TEXT NOT NULL CHECK (analysis_type IN ('visual', 'audio')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'aborted')),
    model_name TEXT,
    model_version TEXT,
    code_commit TEXT,
    parameters_json TEXT,
    confidence_threshold REAL,
    exclusion_mask_json TEXT,
    environment_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_seconds REAL,
    error TEXT,
    parent_run_id TEXT REFERENCES analysis_run(id),
    note TEXT,
    created_at TEXT NOT NULL,
    -- 終端状態の整合性:完了・失敗・中断には終了時刻が必須、失敗にはエラー内容が必須
    CHECK (status NOT IN ('completed', 'failed', 'aborted') OR finished_at IS NOT NULL),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX idx_analysis_run_media ON analysis_run(media_asset_id);
CREATE INDEX idx_analysis_run_parent ON analysis_run(parent_run_id);

CREATE TRIGGER trg_analysis_run_frozen
BEFORE UPDATE ON analysis_run
WHEN OLD.status IN ('completed', 'failed', 'aborted')
BEGIN
    SELECT RAISE(ABORT, 'analysis_run is frozen after completion (D-10)');
END;

CREATE TRIGGER trg_analysis_run_no_delete
BEFORE DELETE ON analysis_run
BEGIN
    SELECT RAISE(ABORT, 'analysis_run must not be deleted (D-10)');
END;

-- 系譜IDは作成後変更禁止(親側更新による系譜破壊の防止。D-23)
CREATE TRIGGER trg_analysis_run_media_immutable
BEFORE UPDATE OF media_asset_id ON analysis_run
WHEN OLD.media_asset_id <> NEW.media_asset_id
BEGIN
    SELECT RAISE(ABORT, 'analysis_run.media_asset_id is immutable (lineage protection, D-23)');
END;

CREATE TABLE job_step (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    step_name TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    started_at TEXT,
    finished_at TEXT,
    resume_state_json TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (analysis_run_id, step_name),
    -- 終端状態の整合性(skippedは時刻なしを許容)
    CHECK (status NOT IN ('completed', 'failed') OR finished_at IS NOT NULL),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX idx_job_step_run ON job_step(analysis_run_id);

CREATE TABLE run_event (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_run_event_run ON run_event(analysis_run_id);

CREATE TRIGGER trg_run_event_no_update
BEFORE UPDATE ON run_event
BEGIN
    SELECT RAISE(ABORT, 'run_event is append-only (D-10)');
END;

CREATE TRIGGER trg_run_event_no_delete
BEFORE DELETE ON run_event
BEGIN
    SELECT RAISE(ABORT, 'run_event is append-only (D-10)');
END;

-- ============ 検出(AI出力。人の判定は review に分離。D-1) ============

CREATE TABLE visual_detection (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    media_start_offset_s REAL NOT NULL,
    media_end_offset_s REAL NOT NULL,
    bbox_series_json TEXT,
    trajectory_json TEXT,
    movement_direction_deg REAL,
    movement_speed_px_s REAL,
    apparent_size_px REAL,
    wingbeat_detected INTEGER CHECK (wingbeat_detected IS NULL OR wingbeat_detected IN (0, 1)),
    wingbeat_period_s REAL,
    flight_style_candidates_json TEXT,
    entered_cover INTEGER CHECK (entered_cover IS NULL OR entered_cover IN (0, 1)),
    exited_cover INTEGER CHECK (exited_cover IS NULL OR exited_cover IN (0, 1)),
    perched INTEGER CHECK (perched IS NULL OR perched IN (0, 1)),
    took_off INTEGER CHECK (took_off IS NULL OR took_off IN (0, 1)),
    category_candidate TEXT NOT NULL DEFAULT 'unknown'
        CHECK (category_candidate IN ('bird', 'insect', 'leaf', 'cloud', 'aircraft', 'other', 'unknown')),
    raptor_likelihood REAL,
    species_candidates_json TEXT,
    ai_confidence REAL,
    -- Positive候補とInsurance候補(Recall優先の保険的候補)の区別と、その判定理由
    candidate_tier TEXT NOT NULL DEFAULT 'positive'
        CHECK (candidate_tier IN ('positive', 'insurance')),
    tier_reasons_json TEXT,
    -- 生の特徴量(Optical Flow統計等)。主要検索項目は上の固定列、
    -- 残りはバージョン付き構造化データとして保持する(D-24)
    features_json TEXT,
    feature_schema_version TEXT,
    -- 各モデルの生スコア・モデル名/版・判定根拠(DATA_MODEL.md 3.8 と同趣旨)
    raw_model_outputs_json TEXT NOT NULL DEFAULT '[]',
    note TEXT,
    created_at TEXT NOT NULL,
    -- 時刻・範囲の基本整合性(UTC ISO-8601形式、開始<=終了、非負オフセット)
    CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ended_at >= started_at),
    CHECK (media_start_offset_s >= 0 AND media_end_offset_s >= media_start_offset_s)
);
CREATE INDEX idx_visual_detection_run ON visual_detection(analysis_run_id);
CREATE INDEX idx_visual_detection_time ON visual_detection(started_at);

-- 系譜IDは作成後変更禁止(D-23)
CREATE TRIGGER trg_visual_detection_run_immutable
BEFORE UPDATE OF analysis_run_id ON visual_detection
WHEN OLD.analysis_run_id <> NEW.analysis_run_id
BEGIN
    SELECT RAISE(ABORT, 'visual_detection.analysis_run_id is immutable (lineage protection, D-23)');
END;

CREATE TABLE audio_detection (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    media_start_offset_s REAL NOT NULL,
    media_end_offset_s REAL NOT NULL,
    -- D-7: SED由来は種候補なしで保存できる
    detection_source TEXT NOT NULL CHECK (detection_source IN ('sed', 'classifier', 'merged')),
    species_candidates_json TEXT,
    top_confidence REAL,
    frequency_band TEXT,
    audio_quality TEXT,
    background_noise_json TEXT,
    simultaneous_species_json TEXT,
    is_unknown_sound INTEGER NOT NULL DEFAULT 0 CHECK (is_unknown_sound IN (0, 1)),
    breeding_related_possibility TEXT,
    -- Positive候補とInsurance候補(低信頼度の保険的保存。D-18)の区別
    candidate_tier TEXT NOT NULL DEFAULT 'positive'
        CHECK (candidate_tier IN ('positive', 'insurance')),
    tier_reasons_json TEXT,
    -- 統合(merged)後も SED / 分類それぞれの生スコア・モデル名/版・根拠を保持する
    -- (代表スコア top_confidence だけで置き換えない。DATA_MODEL.md 3.8)
    raw_model_outputs_json TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ended_at >= started_at),
    CHECK (media_start_offset_s >= 0 AND media_end_offset_s >= media_start_offset_s)
);
CREATE INDEX idx_audio_detection_run ON audio_detection(analysis_run_id);
CREATE INDEX idx_audio_detection_time ON audio_detection(started_at);

-- 系譜IDは作成後変更禁止(D-23)
CREATE TRIGGER trg_audio_detection_run_immutable
BEFORE UPDATE OF analysis_run_id ON audio_detection
WHEN OLD.analysis_run_id <> NEW.analysis_run_id
BEGIN
    SELECT RAISE(ABORT, 'audio_detection.analysis_run_id is immutable (lineage protection, D-23)');
END;

-- ============ 人による確認(追記専用。D-1) ============

CREATE TABLE review (
    id TEXT PRIMARY KEY,
    visual_detection_id TEXT REFERENCES visual_detection(id),
    audio_detection_id TEXT REFERENCES audio_detection(id),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    -- SURVEY_METHOD.md 3.2 の確認状態(人の判定)
    review_status TEXT NOT NULL CHECK (review_status IN (
        'species_confirmed', 'genus_family_confirmed', 'raptor_confirmed', 'bird_confirmed',
        'species_unknown', 'false_positive', 'undeterminable', 'recheck_needed')),
    species_id TEXT REFERENCES species(id),
    confirmed_rank TEXT
        CHECK (confirmed_rank IS NULL OR confirmed_rank IN ('species', 'genus', 'family', 'raptor', 'bird')),
    -- 属・科まで確認の場合の分類群名(属名・科名)
    confirmed_taxon TEXT,
    age_class TEXT,
    sex TEXT,
    individual_identifiable INTEGER
        CHECK (individual_identifiable IS NULL OR individual_identifiable IN (0, 1)),
    individual_id TEXT REFERENCES individual(id),
    behavior_id TEXT REFERENCES behavior(id),
    breeding_related_judgment TEXT,
    -- SURVEY_METHOD.md 3.3 の誤検出原因分類コード
    false_positive_cause TEXT,
    rationale TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK ((visual_detection_id IS NULL) <> (audio_detection_id IS NULL)),
    -- 確認状態と判定内容の整合性(SURVEY_METHOD.md 3.2。許容組合せの定義は同3.2.1)
    -- 注意:NULL比較でCHECKが素通りしないよう、NULL安全な IS / COALESCE を使う
    CHECK (
        (review_status = 'species_confirmed'
            AND species_id IS NOT NULL AND confirmed_rank IS 'species')
        OR (review_status = 'genus_family_confirmed'
            AND species_id IS NULL AND COALESCE(confirmed_rank, '') IN ('genus', 'family')
            AND confirmed_taxon IS NOT NULL)
        OR (review_status = 'raptor_confirmed'
            AND species_id IS NULL AND confirmed_rank IS 'raptor')
        OR (review_status = 'bird_confirmed'
            AND species_id IS NULL AND confirmed_rank IS 'bird')
        OR (review_status = 'species_unknown'
            AND species_id IS NULL AND confirmed_rank IS NULL)
        OR (review_status = 'false_positive'
            AND species_id IS NULL AND confirmed_rank IS NULL
            AND false_positive_cause IS NOT NULL)
        OR (review_status IN ('undeterminable', 'recheck_needed')
            AND species_id IS NULL AND confirmed_rank IS NULL)
    )
);
CREATE INDEX idx_review_visual ON review(visual_detection_id);
CREATE INDEX idx_review_audio ON review(audio_detection_id);

CREATE TRIGGER trg_review_no_update
BEFORE UPDATE ON review
BEGIN
    SELECT RAISE(ABORT, 'review is append-only: record a new review row instead (D-1)');
END;

CREATE TRIGGER trg_review_no_delete
BEFORE DELETE ON review
BEGIN
    SELECT RAISE(ABORT, 'review is append-only (D-1)');
END;

-- ============ 関連候補(自動確定禁止) ============

CREATE TABLE detection_link (
    id TEXT PRIMARY KEY,
    visual_a_id TEXT REFERENCES visual_detection(id),
    audio_a_id TEXT REFERENCES audio_detection(id),
    visual_b_id TEXT REFERENCES visual_detection(id),
    audio_b_id TEXT REFERENCES audio_detection(id),
    link_type TEXT NOT NULL CHECK (link_type IN ('time_proximity', 'same_direction', 'other')),
    rationale TEXT,
    proposed_by TEXT NOT NULL CHECK (proposed_by IN ('ai', 'human')),
    -- 同一事象としての確定は人のみ。確定時は確定者・日時が必須
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    confirmed_by TEXT,
    confirmed_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((visual_a_id IS NULL) <> (audio_a_id IS NULL)),
    CHECK ((visual_b_id IS NULL) <> (audio_b_id IS NULL)),
    CHECK (confirmed = 0 OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
);

-- ============ 派生物(D-9) ============

CREATE TABLE derived_asset (
    id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL CHECK (asset_type IN (
        'proxy', 'extracted_audio', 'audio_clip', 'video_clip',
        'spectrogram', 'thumbnail', 'trajectory_image',
        'preview_image', 'report', 'other')),
    media_asset_id TEXT NOT NULL REFERENCES media_asset(id),
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT,
    size_bytes INTEGER,
    format TEXT,
    generation_params_json TEXT,
    regenerable INTEGER NOT NULL DEFAULT 1 CHECK (regenerable IN (0, 1)),
    regeneration_state TEXT NOT NULL DEFAULT 'present' CHECK (regeneration_state IN (
        'present', 'deleted_regenerable', 'regenerating', 'regeneration_failed')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- 実体が存在する状態ではハッシュ必須(生成途中はregenerating等を使う)
    CHECK (regeneration_state <> 'present' OR sha256 IS NOT NULL)
);
CREATE INDEX idx_derived_asset_media ON derived_asset(media_asset_id);
CREATE INDEX idx_derived_asset_run ON derived_asset(analysis_run_id);

-- 系譜整合:derived_assetのmedia_assetは、生成したanalysis_runのmedia_assetと一致すること
CREATE TRIGGER trg_derived_asset_lineage_insert
BEFORE INSERT ON derived_asset
WHEN (SELECT media_asset_id FROM analysis_run WHERE id = NEW.analysis_run_id) <> NEW.media_asset_id
BEGIN
    SELECT RAISE(ABORT, 'derived_asset lineage mismatch: analysis_run belongs to a different media_asset');
END;

CREATE TRIGGER trg_derived_asset_lineage_update
BEFORE UPDATE OF media_asset_id, analysis_run_id ON derived_asset
WHEN (SELECT media_asset_id FROM analysis_run WHERE id = NEW.analysis_run_id) <> NEW.media_asset_id
BEGIN
    SELECT RAISE(ABORT, 'derived_asset lineage mismatch: analysis_run belongs to a different media_asset');
END;

-- 派生物と検出の対応(多対多)。1クリップに複数track、1trackが複数クリップに
-- またがる場合に対応する。role='primary' はその派生物の主対象(任意)
CREATE TABLE derived_asset_detection (
    id TEXT PRIMARY KEY,
    derived_asset_id TEXT NOT NULL REFERENCES derived_asset(id),
    visual_detection_id TEXT REFERENCES visual_detection(id),
    audio_detection_id TEXT REFERENCES audio_detection(id),
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('primary', 'member')),
    created_at TEXT NOT NULL,
    CHECK ((visual_detection_id IS NULL) <> (audio_detection_id IS NULL))
);
CREATE UNIQUE INDEX uq_dad_visual ON derived_asset_detection(derived_asset_id, visual_detection_id)
    WHERE visual_detection_id IS NOT NULL;
CREATE UNIQUE INDEX uq_dad_audio ON derived_asset_detection(derived_asset_id, audio_detection_id)
    WHERE audio_detection_id IS NOT NULL;
CREATE INDEX idx_dad_visual ON derived_asset_detection(visual_detection_id);
CREATE INDEX idx_dad_audio ON derived_asset_detection(audio_detection_id);

-- 系譜整合:対応づける検出は、その派生物を生成したanalysis_runの検出であること
CREATE TRIGGER trg_dad_run_match_insert
BEFORE INSERT ON derived_asset_detection
WHEN (
    NEW.visual_detection_id IS NOT NULL
    AND (SELECT analysis_run_id FROM visual_detection WHERE id = NEW.visual_detection_id)
        <> (SELECT analysis_run_id FROM derived_asset WHERE id = NEW.derived_asset_id)
) OR (
    NEW.audio_detection_id IS NOT NULL
    AND (SELECT analysis_run_id FROM audio_detection WHERE id = NEW.audio_detection_id)
        <> (SELECT analysis_run_id FROM derived_asset WHERE id = NEW.derived_asset_id)
)
BEGIN
    SELECT RAISE(ABORT, 'derived_asset_detection run mismatch: detection belongs to a different analysis_run');
END;

CREATE TRIGGER trg_dad_run_match_update
BEFORE UPDATE OF derived_asset_id, visual_detection_id, audio_detection_id ON derived_asset_detection
WHEN (
    NEW.visual_detection_id IS NOT NULL
    AND (SELECT analysis_run_id FROM visual_detection WHERE id = NEW.visual_detection_id)
        <> (SELECT analysis_run_id FROM derived_asset WHERE id = NEW.derived_asset_id)
) OR (
    NEW.audio_detection_id IS NOT NULL
    AND (SELECT analysis_run_id FROM audio_detection WHERE id = NEW.audio_detection_id)
        <> (SELECT analysis_run_id FROM derived_asset WHERE id = NEW.derived_asset_id)
)
BEGIN
    SELECT RAISE(ABORT, 'derived_asset_detection run mismatch: detection belongs to a different analysis_run');
END;

-- ============ 精査済み評価データ(D-11) ============

CREATE TABLE reference_observation (
    id TEXT PRIMARY KEY,
    survey_session_id TEXT NOT NULL REFERENCES survey_session(id),
    species_id TEXT NOT NULL REFERENCES species(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    individual_count INTEGER,
    behavior_id TEXT REFERENCES behavior(id),
    distance_band TEXT,
    observation_method TEXT NOT NULL
        CHECK (observation_method IN ('visual', 'aural', 'video_review', 'audio_review')),
    evidence TEXT,
    curator TEXT NOT NULL,
    curation_method TEXT NOT NULL,
    curated_at TEXT NOT NULL,
    curation_confidence TEXT NOT NULL CHECK (curation_confidence IN ('high', 'medium', 'low')),
    double_checked INTEGER NOT NULL DEFAULT 0 CHECK (double_checked IN (0, 1)),
    second_curator TEXT,
    second_curated_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- 二重確認には第二精査者と精査日時の両方が必須
    CHECK (double_checked = 0 OR (second_curator IS NOT NULL AND second_curated_at IS NOT NULL)),
    CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ended_at IS NULL OR (ended_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
        AND ended_at >= started_at))
);
CREATE INDEX idx_reference_observation_session ON reference_observation(survey_session_id);

-- ============ 出力・監査 ============

CREATE TABLE export (
    id TEXT PRIMARY KEY,
    exported_by TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    format TEXT NOT NULL,
    scope_json TEXT,
    -- 適用した位置丸め粒度(D-15)
    rounding_level TEXT NOT NULL,
    file_sha256 TEXT,
    purpose TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE access_log (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('view', 'export', 'change')),
    target TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER trg_access_log_no_update
BEFORE UPDATE ON access_log
BEGIN
    SELECT RAISE(ABORT, 'access_log is append-only (SECURITY.md)');
END;

CREATE TRIGGER trg_access_log_no_delete
BEFORE DELETE ON access_log
BEGIN
    SELECT RAISE(ABORT, 'access_log is append-only (SECURITY.md)');
END;
