# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Claude Code(T-003)
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

フェーズ0(基盤)。T-003はPR #3でmainへマージ済み(Issue #2クローズ)。T-004(DBスキーマ・マイグレーション基盤)は実装完了し、CodexのT-005レビュー指摘(必須4点+推奨1点)への対応を反映済み。T-005再レビュー待ち(Issue #4、PR #5、ブランチ claude/t004-database-schema)。解析機能(FFmpeg抽出・BirdNET・SED・映像検出)・API・UIは未実装(T-101以降)。

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
- 先行成果品(Google Drive参考資料)の分析とスキーマ反映(D-24/D-25):
  - Track⇄クリップの多対多(DerivedAssetDetection)、positive/insurance候補区分+理由、実時刻の算出根拠(file_time追加)と確実性、Station既定解析パラメータ、DerivedAsset種別にpreview_image/report追加
  - Track特徴量はハイブリッド方式(主要項目=固定列、その他=feature_schema_version付きJSON。D-24)
  - 参考ファイル・動画はリポジトリへコピーしていない(Drive参照のみ)

## 未完了のこと

- T-004 PRのレビュー(Codex T-005)・マージ
- T-101以降すべて(メディア登録、FFmpeg抽出、BirdNET/SED、映像検出、UI、CSV出力)
- リモート環境からの `claude/t003-repo-init` ブランチ削除(git/API双方403。権限が付与され次第削除するか、GitHub上で手動削除)
- 実地スモークテスト用の7秒動画は解析パイプライン実装後に使用する(リポジトリへはコミットしない)
- **M1着手時(T-103)にローカル解析機で必ず実施する申し送り事項**(結果はDECISIONS.md D-22とTHIRD_PARTY_LICENSES.mdへ追記):
  1. **ローカル推論スモークテスト**:モデルダウンロード+合成WAVでの推論実行+処理速度計測(本検証環境ではモデル配布元 zenodo.org / tuc.cloud がegressポリシーで遮断され未実施)
  2. **audio推移依存のlock生成**:推論成功後に `pip freeze` でaudio依存を含むlock(`requirements-audio.lock`)を生成する(現状 `requirements-dev.lock` は開発依存のみ。birdnet本体は0.2.16固定だが推移依存は未固定)
  3. **libsndfile確認**:soundfileが依存するネイティブライブラリ libsndfile の存在・バージョンを対象環境で確認する
  4. **モデル保存先・ハッシュ確認**:モデルの保存先・形式・バージョン・SHA-256・オフライン再利用方法を確定し、モデルキャッシュを `BIO_OBSERVER_MODELS_DIR` 等の管理対象ディレクトリへ固定できるか検証する
  5. **商用利用前のライセンス確認**:BirdNETモデルはCC BY-NC-SA 4.0(非商用条件)。有償調査・解析サービス・商品化での利用前に権利者確認を必須とする(THIRD_PARTY_LICENSES.md)

## 次に行うべきこと

1. Codex:T-005 スキーマレビュー(T-004 PRのレビューとして。DATA_MODEL.mdとの突合、追記性・PostgreSQL移行性の確認)
2. 調査責任者:T-004 PRのマージ判断
3. Claude Code:マージ後にT-101 メディア登録(1 Issue = 1 branch = 1 PR)

## 既知の問題

- 本リモート環境からは zenodo.org / tuc.cloud への通信が遮断されており、BirdNETモデルの取得・推論実行ができない(ローカル解析機では問題にならない見込み。D-2のローカル実行方針どおり)

## 判断が必要な事項

- 現在なし

## 実行したテスト/テスト結果

- `pytest`:33件すべてパス(環境確認7件+DB26件)
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
