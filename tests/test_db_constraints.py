"""主要制約のテスト(外部キー/一意制約/enum/追記専用/生スコア保持/精査情報)。"""

import json

import pytest
import sqlite3

from bio_observer.db.ids import new_id
from conftest import insert


def test_foreign_key_violation_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "station", id=new_id("stn"), site_id="site_deadbeef",
               name="孤立ステーション", equipment_type="camera")


def test_unique_constraint(db, seed):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "media_asset", id=new_id("med"),
               survey_session_id=seed["session"], media_type="video",
               relative_path=db.execute(
                   "SELECT relative_path FROM media_asset WHERE id = ?",
                   (seed["media"],)).fetchone()["relative_path"],
               sha256="1" * 64)
    db.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "species", id=new_id("sp"), scientific_name="Testus dummius")


def test_enum_check_rejected(db, seed):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "analysis_run", id=new_id("run"),
               media_asset_id=seed["media"], analysis_type="telepathy")


def test_sed_only_audio_detection_without_species(db, seed):
    """SED由来・種候補なしの音声検出を保存できる(D-7)。"""
    det_id = insert(
        db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
        started_at="2026-08-01T00:10:00Z", ended_at="2026-08-01T00:10:03Z",
        media_start_offset_s=600.0, media_end_offset_s=603.0,
        detection_source="sed",
        species_candidates_json=None, top_confidence=None,
        is_unknown_sound=1,
        raw_model_outputs_json=json.dumps([
            {"model": "sed-energy", "model_version": "0.1", "raw_score": 0.83,
             "evidence": "band-energy onset"},
        ]),
    )
    row = db.execute("SELECT * FROM audio_detection WHERE id = ?", (det_id,)).fetchone()
    assert row["species_candidates_json"] is None
    assert row["detection_source"] == "sed"


def test_merged_detection_keeps_raw_model_outputs(db, seed):
    """統合後もSED・BirdNET双方の生スコア・モデル情報を保持できる(DATA_MODEL.md 3.8)。"""
    raw = [
        {"model": "sed-energy", "model_version": "0.1", "raw_score": 0.91,
         "evidence": "onset+band"},
        {"model": "birdnet", "model_version": "0.2.16 (v2.4)",
         "raw_scores": {"Testus dummius": 0.42, "Alterus avius": 0.11},
         "evidence": "classifier logits"},
    ]
    det_id = insert(
        db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
        started_at="2026-08-01T00:20:00Z", ended_at="2026-08-01T00:20:03Z",
        media_start_offset_s=1200.0, media_end_offset_s=1203.0,
        detection_source="merged",
        species_candidates_json=json.dumps([{"species": "Testus dummius", "score": 0.42}]),
        top_confidence=0.42,
        raw_model_outputs_json=json.dumps(raw),
    )
    stored = json.loads(db.execute(
        "SELECT raw_model_outputs_json FROM audio_detection WHERE id = ?", (det_id,)
    ).fetchone()[0])
    assert {entry["model"] for entry in stored} == {"sed-energy", "birdnet"}
    assert stored[1]["raw_scores"]["Alterus avius"] == 0.11  # 代表スコア以外も保持


def test_reference_observation_curation_fields(db, seed):
    """ReferenceObservationの精査情報(精査者・方法・確信度・二重確認)を保存できる(D-11)。"""
    ref_id = insert(
        db, "reference_observation", id=new_id("ref"),
        survey_session_id=seed["session"], species_id=seed["species"],
        started_at="2026-08-01T00:30:00Z", observation_method="audio_review",
        curator="調査者A", curation_method="スペクトログラム精査",
        curated_at="2026-08-02T10:00:00Z", curation_confidence="high",
        double_checked=1, second_curator="調査者B",
        second_curated_at="2026-08-03T09:00:00Z",
    )
    row = db.execute("SELECT * FROM reference_observation WHERE id = ?", (ref_id,)).fetchone()
    assert row["curator"] == "調査者A" and row["double_checked"] == 1
    # 二重確認ありなのに第二精査者なしは拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "reference_observation", id=new_id("ref"),
               survey_session_id=seed["session"], species_id=seed["species"],
               started_at="2026-08-01T00:40:00Z", observation_method="visual",
               curator="調査者A", curation_method="目視", curated_at="2026-08-02T10:00:00Z",
               curation_confidence="medium", double_checked=1)


def _add_review(db, seed) -> str:
    det = insert(
        db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
        started_at="2026-08-01T01:00:00Z", ended_at="2026-08-01T01:00:03Z",
        media_start_offset_s=3600.0, media_end_offset_s=3603.0,
        detection_source="classifier",
        raw_model_outputs_json="[]",
    )
    return insert(
        db, "review", id=new_id("rev"), audio_detection_id=det,
        reviewer="調査者A", reviewed_at="2026-08-02T00:00:00Z",
        review_status="species_confirmed", species_id=seed["species"],
        confirmed_rank="species", rationale="声質・節回しが一致",
    )


def test_review_is_append_only(db, seed):
    rev_id = _add_review(db, seed)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE review SET reviewer = 'X' WHERE id = ?", (rev_id,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM review WHERE id = ?", (rev_id,))


def test_review_targets_exactly_one_detection(db, seed):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "review", id=new_id("rev"), reviewer="A",
               reviewed_at="2026-08-02T00:00:00Z", review_status="undeterminable")


def test_analysis_run_frozen_after_completion(db, seed):
    run_id = seed["run"]
    # 実行中の更新(完了への遷移)は許可される
    db.execute(
        "UPDATE analysis_run SET status = 'completed', finished_at = ?, duration_seconds = 10 "
        "WHERE id = ?", ("2026-08-01T02:00:00Z", run_id))
    # 完了後は凍結(D-10)
    with pytest.raises(sqlite3.DatabaseError, match="frozen"):
        db.execute("UPDATE analysis_run SET note = 'x' WHERE id = ?", (run_id,))
    with pytest.raises(sqlite3.DatabaseError, match="must not be deleted"):
        db.execute("DELETE FROM analysis_run WHERE id = ?", (run_id,))


def test_run_event_and_access_log_append_only(db, seed):
    evt_id = insert(db, "run_event", id=new_id("evt"), analysis_run_id=seed["run"],
                    occurred_at="2026-08-01T00:00:01Z", event_type="started")
    log_id = insert(db, "access_log", id=new_id("alog"), actor="調査者A",
                    occurred_at="2026-08-01T00:00:02Z", action="view",
                    target="site:rounded_position")
    for table, column, row_id in (
        ("run_event", "message", evt_id),
        ("access_log", "result", log_id),
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(f"UPDATE {table} SET {column} = 'x' WHERE id = ?", (row_id,))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


def test_track_clip_many_to_many(db, seed):
    """動体Trackと抽出クリップの多対多対応(derived_asset_detection)。"""
    track1 = insert(db, "visual_detection", id=new_id("vdet"), analysis_run_id=seed["run"],
                    started_at="2026-08-01T04:00:00Z", ended_at="2026-08-01T04:00:10Z",
                    media_start_offset_s=0, media_end_offset_s=10,
                    candidate_tier="positive")
    track2 = insert(db, "visual_detection", id=new_id("vdet"), analysis_run_id=seed["run"],
                    started_at="2026-08-01T04:00:05Z", ended_at="2026-08-01T04:00:20Z",
                    media_start_offset_s=5, media_end_offset_s=20,
                    candidate_tier="insurance",
                    tier_reasons_json='["below_dynamic_flow_threshold","no_strong_flow"]',
                    features_json='{"straightness": 0.42, "frame_flow_mag_p90": 1.3}',
                    feature_schema_version="v1")
    clip1 = insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
                   media_asset_id=seed["media"], analysis_run_id=seed["run"],
                   relative_path="derived/clips/clip_0001.mp4", sha256="a" * 64)
    clip2 = insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
                   media_asset_id=seed["media"], analysis_run_id=seed["run"],
                   relative_path="derived/clips/clip_0002.mp4", sha256="b" * 64)
    # クリップ1に2トラック、トラック2は2クリップにまたがる
    insert(db, "derived_asset_detection", id=new_id("dmem"),
           derived_asset_id=clip1, visual_detection_id=track1, role="primary")
    insert(db, "derived_asset_detection", id=new_id("dmem"),
           derived_asset_id=clip1, visual_detection_id=track2)
    insert(db, "derived_asset_detection", id=new_id("dmem"),
           derived_asset_id=clip2, visual_detection_id=track2)
    (n_clip1,) = db.execute(
        "SELECT COUNT(*) FROM derived_asset_detection WHERE derived_asset_id = ?",
        (clip1,)).fetchone()
    (n_track2,) = db.execute(
        "SELECT COUNT(*) FROM derived_asset_detection WHERE visual_detection_id = ?",
        (track2,)).fetchone()
    assert (n_clip1, n_track2) == (2, 2)
    # 同一組の二重登録は拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "derived_asset_detection", id=new_id("dmem"),
               derived_asset_id=clip1, visual_detection_id=track1)


def test_candidate_tier_and_recording_certainty(db, seed):
    """Positive/Insurance区分のenumと、実時刻の算出根拠・確実性の保存。"""
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "visual_detection", id=new_id("vdet"), analysis_run_id=seed["run"],
               started_at="2026-08-01T05:00:00Z", ended_at="2026-08-01T05:00:01Z",
               media_start_offset_s=0, media_end_offset_s=1, candidate_tier="maybe")
    med = insert(db, "media_asset", id=new_id("med"), survey_session_id=seed["session"],
                 media_type="video", relative_path="x/video2.mp4", sha256="2" * 64,
                 recording_started_at="2026-08-01T01:00:00Z",
                 recording_start_basis="file_time",
                 recording_start_certainty="estimated", timezone="Asia/Tokyo")
    row = db.execute("SELECT recording_start_basis, recording_start_certainty "
                     "FROM media_asset WHERE id = ?", (med,)).fetchone()
    assert tuple(row) == ("file_time", "estimated")


def test_media_asset_physical_delete_and_hash_change_rejected(db, seed):
    """原データの物理DELETE禁止・sha256変更禁止(原則3)。論理削除は可能。"""
    with pytest.raises(sqlite3.DatabaseError, match="deleted physically"):
        db.execute("DELETE FROM media_asset WHERE id = ?", (seed["media"],))
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE media_asset SET sha256 = ? WHERE id = ?",
                   ("f" * 64, seed["media"]))
    db.execute("UPDATE media_asset SET deleted_at = '2026-08-02T00:00:00Z' WHERE id = ?",
               (seed["media"],))


def _second_media_and_run(db, seed):
    med2 = insert(db, "media_asset", id=new_id("med"), survey_session_id=seed["session"],
                  media_type="video", relative_path="x/video_other.mp4", sha256="9" * 64)
    run2 = insert(db, "analysis_run", id=new_id("run"), media_asset_id=med2,
                  analysis_type="visual", status="running")
    return med2, run2


def test_derived_asset_lineage_mismatch_rejected(db, seed):
    """別動画のRunとメディアの組合せ、別Runの検出との対応づけを拒否。"""
    med2, run2 = _second_media_and_run(db, seed)
    # Runが別動画のものである derived_asset は拒否
    with pytest.raises(sqlite3.DatabaseError, match="lineage mismatch"):
        insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
               media_asset_id=seed["media"], analysis_run_id=run2,
               relative_path="derived/clips/bad.mp4", sha256="c" * 64)
    # 正常系:med2×run2は登録できる
    asset2 = insert(db, "derived_asset", id=new_id("dast"), asset_type="proxy",
                    media_asset_id=med2, analysis_run_id=run2,
                    relative_path="derived/proxies/p2.mp4", sha256="d" * 64)
    # 別Run(seed[run])の検出を、run2の派生物へ対応づけるのは拒否
    det = insert(db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
                 started_at="2026-08-01T06:00:00Z", ended_at="2026-08-01T06:00:03Z",
                 media_start_offset_s=0, media_end_offset_s=3,
                 detection_source="classifier", raw_model_outputs_json="[]")
    with pytest.raises(sqlite3.DatabaseError, match="run mismatch"):
        insert(db, "derived_asset_detection", id=new_id("dmem"),
               derived_asset_id=asset2, audio_detection_id=det)


def test_lineage_ids_immutable_after_creation(db, seed):
    """系譜ID(Runのメディア、検出のRun)は作成後変更禁止(親側更新の抜け道防止)。"""
    med2, run2 = _second_media_and_run(db, seed)
    # 派生物を正常に関連付けた状態を作る
    det = insert(db, "visual_detection", id=new_id("vdet"), analysis_run_id=seed["run"],
                 started_at="2026-08-01T10:00:00Z", ended_at="2026-08-01T10:00:05Z",
                 media_start_offset_s=0, media_end_offset_s=5)
    asset = insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
                   media_asset_id=seed["media"], analysis_run_id=seed["run"],
                   relative_path="derived/clips/lineage.mp4", sha256="e" * 64)
    insert(db, "derived_asset_detection", id=new_id("dmem"),
           derived_asset_id=asset, visual_detection_id=det, role="primary")
    # DerivedAsset作成後もRunのmedia_asset_idは変更できない
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE analysis_run SET media_asset_id = ? WHERE id = ?",
                   (med2, seed["run"]))
    # DerivedAssetDetection作成後もDetectionのanalysis_run_idは変更できない
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE visual_detection SET analysis_run_id = ? WHERE id = ?",
                   (run2, det))
    # 音声検出も同様
    adet = insert(db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
                  started_at="2026-08-01T10:01:00Z", ended_at="2026-08-01T10:01:03Z",
                  media_start_offset_s=60, media_end_offset_s=63,
                  detection_source="sed", raw_model_outputs_json="[]")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE audio_detection SET analysis_run_id = ? WHERE id = ?",
                   (run2, adet))
    # 同じ値でのUPDATEは許可される
    db.execute("UPDATE analysis_run SET media_asset_id = ? WHERE id = ?",
               (seed["media"], seed["run"]))
    db.execute("UPDATE visual_detection SET analysis_run_id = ? WHERE id = ?",
               (seed["run"], det))


def test_derived_asset_present_requires_sha256(db, seed):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
               media_asset_id=seed["media"], analysis_run_id=seed["run"],
               relative_path="derived/clips/nohash.mp4")  # present なのに sha256 なし
    insert(db, "derived_asset", id=new_id("dast"), asset_type="video_clip",
           media_asset_id=seed["media"], analysis_run_id=seed["run"],
           relative_path="derived/clips/generating.mp4",
           regeneration_state="regenerating")  # 生成途中はハッシュなしを許容


def test_review_status_content_consistency(db, seed):
    """確認状態と判定内容の整合(SURVEY_METHOD.md 3.2.1)。"""
    det = insert(db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
                 started_at="2026-08-01T07:00:00Z", ended_at="2026-08-01T07:00:03Z",
                 media_start_offset_s=0, media_end_offset_s=3,
                 detection_source="classifier", raw_model_outputs_json="[]")
    base = dict(audio_detection_id=det, reviewer="A", reviewed_at="2026-08-02T00:00:00Z")
    # 種確定なのに species_id なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "review", id=new_id("rev"), review_status="species_confirmed",
               confirmed_rank="species", **base)
    # 誤検出なのに原因なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "review", id=new_id("rev"), review_status="false_positive", **base)
    # 誤検出に種確定を同時指定 → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "review", id=new_id("rev"), review_status="false_positive",
               false_positive_cause="cloud", species_id=seed["species"], **base)
    # 鳥類まで確認なのに rank なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "review", id=new_id("rev"), review_status="bird_confirmed", **base)
    # 正常系:属・科まで確認(分類群名つき)、誤検出(原因つき)
    insert(db, "review", id=new_id("rev"), review_status="genus_family_confirmed",
           confirmed_rank="genus", confirmed_taxon="Buteo", **base)
    insert(db, "review", id=new_id("rev"), review_status="false_positive",
           false_positive_cause="aircraft", **base)


def test_time_and_range_checks(db, seed):
    """時刻形式・順序・オフセットの基本整合性と終端状態の必須項目。"""
    common = dict(analysis_run_id=seed["run"], media_start_offset_s=0, media_end_offset_s=1)
    # 開始 > 終了 → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "visual_detection", id=new_id("vdet"),
               started_at="2026-08-01T08:00:05Z", ended_at="2026-08-01T08:00:00Z", **common)
    # 非UTC形式(Zなし) → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "visual_detection", id=new_id("vdet"),
               started_at="2026-08-01 08:00:00", ended_at="2026-08-01T08:00:01Z", **common)
    # 負のオフセット → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "visual_detection", id=new_id("vdet"),
               started_at="2026-08-01T08:00:00Z", ended_at="2026-08-01T08:00:01Z",
               analysis_run_id=seed["run"], media_start_offset_s=-1, media_end_offset_s=1)
    # 完了なのに finished_at なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE analysis_run SET status = 'completed' WHERE id = ?",
                   (seed["run"],))
    # 失敗なのに error なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "job_step", id=new_id("step"), analysis_run_id=seed["run"],
               step_name="probe", step_order=1, status="failed",
               finished_at="2026-08-01T09:00:00Z")
    # 二重確認なのに第二精査日時なし → 拒否
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "reference_observation", id=new_id("ref"),
               survey_session_id=seed["session"], species_id=seed["species"],
               started_at="2026-08-01T00:50:00Z", observation_method="visual",
               curator="A", curation_method="目視", curated_at="2026-08-02T10:00:00Z",
               curation_confidence="high", double_checked=1, second_curator="B")


def test_detection_link_confirmation_requires_human_fields(db, seed):
    det_a = insert(db, "audio_detection", id=new_id("adet"), analysis_run_id=seed["run"],
                   started_at="2026-08-01T03:00:00Z", ended_at="2026-08-01T03:00:03Z",
                   media_start_offset_s=0, media_end_offset_s=3,
                   detection_source="classifier", raw_model_outputs_json="[]")
    det_b = insert(db, "visual_detection", id=new_id("vdet"), analysis_run_id=seed["run"],
                   started_at="2026-08-01T03:00:01Z", ended_at="2026-08-01T03:00:04Z",
                   media_start_offset_s=1, media_end_offset_s=4)
    # AI提示の関連候補(未確定)はOK
    insert(db, "detection_link", id=new_id("link"), audio_a_id=det_a, visual_b_id=det_b,
           link_type="time_proximity", proposed_by="ai")
    # 確定者・日時なしの「確定」は拒否(自動確定の防止)
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "detection_link", id=new_id("link"), audio_a_id=det_a, visual_b_id=det_b,
               link_type="time_proximity", proposed_by="ai", confirmed=1)
