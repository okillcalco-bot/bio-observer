# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Claude Code(T-003)
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

フェーズ1(音声パイプライン)開始。T-004はT-005承認を経てPR #5でmainへマージ済み(Issue #4クローズ、main: 80c506d)。T-101(メディア登録)を実装完了し、Codexレビュー待ち(Issue #7、ブランチ claude/t101-media-registration)。音声抽出・BirdNET/SED・映像検出・UI・Drive連携(T-110)は未実装。

## 完了したこと

- 設計ドキュメント一式(PR #1、mainへマージ済み)
- T-003 リポジトリ初期化(Issue #2):
  - Pythonプロジェクト初期構成(src/bio_observer、pyproject.toml、Python 3.11固定)
  - 依存関係の固定(python-dotenv 1.0.1、pytest 8.3.4、requirements-dev.lock)
  - FFmpeg/FFprobe 6.1.1の利用確認(合成メディア生成→ffprobe取得までテストで検証)
  - .env.example(保存場所・FFmpegパス・タイムゾーン。秘密情報・座標なし)
  - .gitignore(動画・音声・モデル・DB・座標・秘密情報・ログを除外)
  - STORAGE.md(原データ/派生データ/モデル/DB/ログのディレクトリ方針)
  - 音声ライブラリ比較検証 → 公式 birdnet 0.2.16 を採用(D-22。BirdNET-AnalyzerはCustom Classifier学習・クロスチェック用の独立ツール)
  - 環境確認テスト7件(tests/test_environment.py)+ `bio-observer-envcheck` CLI
- T-004 DBスキーマ・マイグレーション基盤(Issue #4。実装方式はD-23):
  - `bio_observer.db`:接続(外部キー強制有効化)・番号付きSQLマイグレーション・schema_migrations管理
  - 初回マイグレーション 0001_initial:DATA_MODEL.md 3.1〜3.19の全20テーブル+インデックス+トリガー
  - 追記専用の担保:review/access_log/run_event はUPDATE/DELETE拒否、analysis_runは完了後凍結(トリガー。限界はD-23に明記)
  - 不透明ID(`ids.new_id`、登録制プレフィックス)・UTC日時ヘルパー(naive拒否)
  - 正確な座標列は不存在(D-12。テストで列名検査)
  - DBテスト26件(空DB構築/段階的アップグレード/冪等性/FK・一意・enum違反/SED由来種候補なし保存/生スコア保持/精査情報/追記専用/確定に人の記録必須/Track⇄クリップ多対多/候補区分・実時刻確実性/原データDELETE・sha256変更拒否/系譜整合/Review整合/時刻・範囲CHECK)
- CodexレビューT-005指摘対応(D-23追記):原データ物理DELETE禁止トリガー、derived_asset/derived_asset_detectionの系譜整合トリガー、Review整合CHECK(SURVEY_METHOD.md 3.2.1)+confirmed_taxon列、時刻・範囲CHECK(UTC形式GLOB・開始≦終了・非負オフセット・終端状態のfinished_at/error必須)、二重確認の第二精査日時必須化、DerivedAsset present時sha256必須
- T-005再レビュー指摘対応:系譜ID(analysis_run.media_asset_id、visual/audio_detection.analysis_run_id)を作成後イミュータブル化(親側更新による系譜迂回の防止。同値UPDATEは許可)
- 先行成果品(Google Drive参考資料)の分析とスキーマ反映(D-24/D-25):
  - Track⇄クリップの多対多(DerivedAssetDetection)、positive/insurance候補区分+理由、実時刻の算出根拠(file_time追加)と確実性、Station既定解析パラメータ、DerivedAsset種別にpreview_image/report追加
  - Track特徴量はハイブリッド方式(主要項目=固定列、その他=feature_schema_version付きJSON。D-24)
  - 参考ファイル・動画はリポジトリへコピーしていない(Drive参照のみ)

## 未完了のこと

- T-101 PRのCodexレビュー・マージ
- T-102以降(音声抽出、BirdNET/SED、クリップ生成、UI、CSV出力)、T-110(Drive自動取込。T-101マージ後)
- リモート環境からの `claude/t003-repo-init`・`claude/t004-database-schema` ブランチ削除(ref削除権限403。GitHub上で手動削除)
- 実地スモークテスト用の短尺動画はT-110のE2Eテストで使用する(リポジトリへはコミットしない)
- **M1着手時(T-103)にローカル解析機で必ず実施する申し送り事項**(結果はDECISIONS.md D-22とTHIRD_PARTY_LICENSES.mdへ追記):
  1. **ローカル推論スモークテスト**:モデルダウンロード+合成WAVでの推論実行+処理速度計測(本検証環境ではモデル配布元 zenodo.org / tuc.cloud がegressポリシーで遮断され未実施)
  2. **audio推移依存のlock生成**:推論成功後に `pip freeze` でaudio依存を含むlock(`requirements-audio.lock`)を生成する(現状 `requirements-dev.lock` は開発依存のみ。birdnet本体は0.2.16固定だが推移依存は未固定)
  3. **libsndfile確認**:soundfileが依存するネイティブライブラリ libsndfile の存在・バージョンを対象環境で確認する
  4. **モデル保存先・ハッシュ確認**:モデルの保存先・形式・バージョン・SHA-256・オフライン再利用方法を確定し、モデルキャッシュを `BIO_OBSERVER_MODELS_DIR` 等の管理対象ディレクトリへ固定できるか検証する
  5. **商用利用前のライセンス確認**:BirdNETモデルはCC BY-NC-SA 4.0(非商用条件)。有償調査・解析サービス・商品化での利用前に権利者確認を必須とする(THIRD_PARTY_LICENSES.md)

## 次に行うべきこと

1. Codex:T-101 PRのレビュー(media_registryのD-26遵守・クリーンアップ保証・テスト網羅の確認)
2. 調査責任者:T-101 PRのマージ判断
3. Claude Code:マージ後にT-110 Google Drive自動取込(Issue #6。独立ブランチ・独立PR)、並行してT-102 音声抽出

## T-101実装の要点(レビュー観点)

- `bio_observer.media_registry`:probe_media(FFprobe)/compute_sha256(ストリーミング)/register_media
- 登録手順:probe→容量確認→コピー(1パスでハッシュ計算)→コピー先再ハッシュ照合→重複確認→DB挿入→atomic rename→commit。例外時はrollback+ファイル削除(D-26)
- 保存先は originals/<project_id>/<site_id>/<station_id>/<survey_session_id>/<media_id><ext>(元ファイル名を露出しない)
- certainty='confirmed' は basis='manual'/'corrected' のみ許可(自動断定禁止)
- レビュー対応:既存資産の不可侵(衝突時は既存へ触れず失敗、削除は本呼出し作成分のみ)、os.linkによる排他的・原子的確定(exFAT等はフォールバック+限界をD-26に記録)、トランザクション所有契約のdocstring明文化(SAVEPOINT化は将来検討の申し送り)
- テスト14件:動画/WAV登録、ストリーミングハッシュ一致、重複拒否、破損ファイル、不存在・非対応拡張子、容量不足、confirmed制限、確定失敗時クリーンアップ、不明セッション、probe単体、ID衝突時の既存行・ファイル不可侵、既存確定先の上書き・削除拒否、TOCTOU時の排他的確定

## 承認済み追加要件:Google Drive自動取込(T-110 / Issue #6)

- Driveを受け箱として使う自動取込・結果返却worker。要件詳細・状態管理(discovered〜retry_required)・エラー対応・E2Eスモークテスト手順はIssue #6が正
- **受け箱フォルダのID・URLとOAuth認証情報はリポジトリへコミットしない**(環境変数で指定。SECURITY.md)。受け箱には短尺テスト動画2本(IMG_3355.MOV / IMG_3356.MOV)があり、最初のE2Eスモークテストに使う(検出精度は合否条件にしない)
- 将来は約4時間動画を同方式で扱う:チャンクDL・15〜30分チャンク解析・JobStep/RunEventでの進捗記録・再起動後の再開・容量事前確認・一晩処理目標(D-17)維持
- Drive上の元動画は削除・移動・改名しない。動画をリポジトリへコミットしない
- 現行スキーマとの対応:sha256 UNIQUE(二重登録防止)、recording_start_basis/certainty、job_step.resume_state_json(再開)、derived_asset(結果物の系譜)。取込状態の持ち方(IngestJob等の新エンティティ)は実装時にDECISIONS.mdへ記録

## 既知の問題

- 本リモート環境からは zenodo.org / tuc.cloud への通信が遮断されており、BirdNETモデルの取得・推論実行ができない(ローカル解析機では問題にならない見込み。D-2のローカル実行方針どおり)

## 判断が必要な事項

- 現在なし

## 実行したテスト/テスト結果

- `pytest`:48件すべてパス(環境確認7件+DB27件+メディア登録14件)
- DBテスト内訳:空DBへの最新スキーマ構築/1バージョンずつの段階的マイグレーション/再実行の冪等性/外部キー有効化・integrity_check/正確座標列の不存在検査/不透明IDポリシー/UTCヘルパー/FK違反拒否/一意制約/enum CHECK拒否/SED由来・種候補なしAudioDetection保存/統合後の生スコア保持/ReferenceObservation精査情報+二重確認CHECK/review追記専用/analysis_run完了後凍結/run_event・access_log追記専用/DetectionLink確定に人の記録必須
- `bio-observer-envcheck`:すべてOK(Python 3.11.15 / ffmpeg 6.1.1 / ffprobe 6.1.1 / 設定読み込み)
- ライブラリ比較:birdnet 0.2.16・birdnet-analyzer 2.4.0のインストール・API検証(詳細はD-22)。推論は未実施(T-103申し送り)

## 使用モデル・環境

- 実行環境:Python 3.11.15、pip 24.0、FFmpeg/FFprobe 6.1.1、SQLite(標準ライブラリ)、Linux(Claude Codeリモート実行環境)
- 採用ライブラリ:birdnet 0.2.16(pyproject optional-dependencies `audio`。D-22)

## 関連コミット

- 設計:PR #1(main: 24c56d2)
- T-003:PR #3(main: 5014c90、Issue #2クローズ)
- T-004:本ブランチ claude/t004-database-schema(Issue #4)

## 変更してはいけない事項

- PROJECT_CHARTER.md 第5章の原則1〜5(自動確定禁止/Recall優先/原データ不変/再現可能性・Run追記/機微情報保護)
- AI候補と人の判定(Review)のエンティティ分離(D-1)
- イミュータブルなエンティティ(完了後のAnalysisRun・Review・AccessLog・RunEvent)の上書き更新禁止(D-10)
- SURVEY_METHOD.md 第3章の判定区分の名称・定義(変更は調査責任者の承認要)
- 正確な座標を外部サービス・ログ・リポジトリへ出さないこと。T-303実装まではDBへも保存しないこと(SECURITY.md、D-12)
- ベンチマーク評価の正解データはReferenceObservationとし、野帳記録で代用しないこと(D-11)
- 統合後の検出でも各モデルの生スコア・モデル版・判定根拠を保持すること(DATA_MODEL.md 3.8)
- .gitignoreの機微・大容量除外パターンを緩めないこと(SECURITY.md)
