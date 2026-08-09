# 変更履歴(CHANGELOG.md)

## 2026-08-09(PR #3 Codexレビュー対応)

- THIRD_PARTY_LICENSES.md 新規作成。BirdNETのライセンス表記を修正(コード:MIT/学習済みモデル:CC BY-NC-SA 4.0。研究・技術検証前提、商用利用前の権利者確認を必須化)。D-22・READMEにも反映
- 依存固定の状態を正確化(requirements-dev.lockは開発依存のみ、birdnet本体0.2.16固定、推移依存は未固定。ローカル推論成功後にaudio lockを生成する申し送り)
- D-22のインストール例を `pip install ".[audio]"` へ修正
- モデルがBIO_OBSERVER_MODELS_DIRへ自動保存されるという未確認記述を削除(保存先・形式・バージョン・SHA-256・オフライン再利用・キャッシュ固定可否はT-103で確定)
- 「同一モデルなので精度は同等」の断定を撤回(同一モデル系列だが出力一致は未検証、へ修正)
- STORAGE.mdの原データ保存パスを不透明ID方式へ変更(originals/<project_id>/<site_id>/<station_id>/<survey_session_id>/。地点名・希少種名をパスに使わない)
- AI_HANDOFF.mdへT-103時の申し送り5項目を追加(推論スモークテスト/audio lock生成/libsndfile確認/モデル保存先・ハッシュ確認/商用利用前ライセンス確認)

## 2026-08-09(T-003 リポジトリ初期化。Issue #2)

- Pythonプロジェクト初期構成(src/bio_observer、pyproject.toml、Python 3.11固定、依存ピン留め+requirements-dev.lock)
- STORAGE.md 追加(原データ/派生データ/モデル/DB/ログのディレクトリ方針)
- .env.example 追加(保存場所・FFmpegパス・タイムゾーン。秘密情報・座標を含めない)
- .gitignore 追加(動画・音声・モデル・DB・座標・秘密情報・ログを除外)
- 環境確認:FFmpeg/FFprobe 6.1.1、環境確認テスト7件、`bio-observer-envcheck` CLI
- 音声ライブラリ比較検証(D-13)→ 公式 birdnet 0.2.16 を採用(D-22)。BirdNET-Analyzer 2.4.0 はCustom Classifier学習・クロスチェック用の独立ツールと位置づけ
- 制約:本検証環境ではモデル配布元への通信遮断により推論未検証(M1着手時にローカル解析機で実施)
- 解析機能・DBスキーマ・UIは未実装(T-004以降)

## 2026-08-09(T-002再レビュー承認・最終更新)

- Codex再レビュー結果:前回指摘すべて反映済み・重大な矛盾なし・実装開始可・PR #1承認
- TASKS.md:T-002を「完了」へ更新
- AI_HANDOFF.md:状態を「PR #1マージ待ち」へ更新し、実装時の確認事項3点を追記
  - 統合後も各モデルの生スコア・モデル版・判定根拠を保持
  - イミュータブルなエンティティの上書き更新禁止
  - ReferenceObservationへ精査者・精査方法・確信度・二重確認の有無を追加
- DATA_MODEL.md:3.8(生スコア保持)・3.18(精査メタデータ)を上記に合わせ更新
- 実装コードは引き続き未着手

## 2026-08-09(CodexレビューT-002反映)

- Codex設計レビューの指摘10件を設計文書へ反映(PR #1 追加コミット)
  - 音声イベント検出(SED)工程を追加(D-7):ARCHITECTURE.md 第4章、ROADMAP.md MVP要件5
  - プロキシをUI確認用に限定し、映像検出は原解像度・タイル分割・複数解像度選択制に(D-8)
  - DerivedAsset(派生物の系譜・ハッシュ・生成条件・再生成状態)を追加(D-9):DATA_MODEL.md 3.16
  - AnalysisRunを完了後凍結とし、JobStep/RunEventを追加(D-10):DATA_MODEL.md 3.6・3.17
  - ReferenceObservation(精査済み評価データ)を追加し、野帳照合とベンチマーク評価を分離(D-11):DATA_MODEL.md 3.18、SURVEY_METHOD.md 5.2
  - T-303実装前は正確座標をDBへ保存しない方針に変更(D-12):DATA_MODEL.md 3.2、SECURITY.md
  - 音声ライブラリをbirdnetlib固定からT-003比較決定に変更(D-13)
  - 旧判断待ちP-1〜P-8をD-14〜D-21として決定済みへ移行
  - 既存HTMLプロトタイプ2点を archive/prototypes/ へ移動(D-21)
  - TASKS.md T-002を「対応中(指摘反映済み・再レビュー待ち)」へ更新
- 実装コードは引き続き未着手

## 2026-08-09

- Fable設計ドキュメント初版一式を追加(設計フェーズM0)
  - PROJECT_CHARTER.md:プロジェクト憲章・目的・非目的・変えてはいけない原則・ユースケース・役割分担
  - ARCHITECTURE.md:システム構成・映像/音声解析パイプライン・共通タイムライン・技術選定
  - DATA_MODEL.md:エンティティ定義(Project〜AccessLog の15実体)・状態遷移
  - SURVEY_METHOD.md:調査フロー・用語定義・確認状態の区分・検証計画
  - SECURITY.md:希少種情報保護・アクセス制御・保管運用
  - ROADMAP.md:MVP要件・非機能要件・マイルストーン・将来拡張
  - DECISIONS.md:決定D-1〜D-6・判断待ちP-1〜P-8(未決事項一覧)
  - TASKS.md:フェーズ0〜4の実装タスク分解と担当
  - AI_HANDOFF.md:AI間引き継ぎ(現在の状態・次にやること・変更禁止事項)
- コードは未着手(第12節「まだコードを書かない」に従う)
