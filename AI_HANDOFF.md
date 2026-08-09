# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Claude Code(T-003)
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

フェーズ0(基盤)。設計はPR #1でmainへマージ済み。T-003(リポジトリ初期化)を完了し、PRレビュー待ち(Issue #2、ブランチ claude/t003-repo-init)。解析機能・DBスキーマ・UIは未実装(T-004以降)。

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

## 未完了のこと

- T-003 PRのレビュー・マージ
- **birdnet推論のスモークテスト**:本検証環境ではモデル配布元(zenodo.org / tuc.cloud)がegressポリシーで遮断され未実施。**M1着手時にローカル解析機で必ず実施し、結果をDECISIONS.md D-22へ追記すること**
- T-004以降すべて(DBスキーマ、解析パイプライン、UI)

## 次に行うべきこと

1. Codex:T-005の前段として、T-003 PRのレビュー
2. 調査責任者:T-003 PRのマージ判断
3. Claude Code:マージ後にT-004 DBスキーマ実装(1 Issue = 1 branch = 1 PR)

## 既知の問題

- 本リモート環境からは zenodo.org / tuc.cloud への通信が遮断されており、BirdNETモデルの取得・推論実行ができない(ローカル解析機では問題にならない見込み。D-2のローカル実行方針どおり)

## 判断が必要な事項

- 現在なし

## 実行したテスト/テスト結果

- `pytest`:7件すべてパス(Python版、パッケージimport、ffmpeg/ffprobe存在、合成メディア生成+ffprobe読取、設定既定値、.env.example機微混入チェック)
- `bio-observer-envcheck`:すべてOK(Python 3.11.15 / ffmpeg 6.1.1 / ffprobe 6.1.1 / 設定読み込み)
- ライブラリ比較:birdnet 0.2.16・birdnet-analyzer 2.4.0のインストール・API検証(詳細はD-22)。推論は上記の通り未実施

## 使用モデル・環境

- 実行環境:Python 3.11.15、pip 24.0、FFmpeg/FFprobe 6.1.1、Linux(Claude Codeリモート実行環境)
- 採用ライブラリ:birdnet 0.2.16(pyproject optional-dependencies `audio`。D-22)

## 関連コミット

- 設計:PR #1(main: 24c56d2)
- T-003:本ブランチ claude/t003-repo-init(Issue #2)

## 変更してはいけない事項

- PROJECT_CHARTER.md 第5章の原則1〜5(自動確定禁止/Recall優先/原データ不変/再現可能性・Run追記/機微情報保護)
- AI候補と人の判定(Review)のエンティティ分離(D-1)
- イミュータブルなエンティティ(完了後のAnalysisRun・Review・AccessLog・RunEvent)の上書き更新禁止(D-10)
- SURVEY_METHOD.md 第3章の判定区分の名称・定義(変更は調査責任者の承認要)
- 正確な座標を外部サービス・ログ・リポジトリへ出さないこと。T-303実装まではDBへも保存しないこと(SECURITY.md、D-12)
- ベンチマーク評価の正解データはReferenceObservationとし、野帳記録で代用しないこと(D-11)
- 統合後の検出でも各モデルの生スコア・モデル版・判定根拠を保持すること(DATA_MODEL.md 3.8)
- .gitignoreの機微・大容量除外パターンを緩めないこと(SECURITY.md)
