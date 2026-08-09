# 実装タスク分解(TASKS.md)

- 運用:各タスクの担当(Claude Code/Codex)と状態をここに記録する。**1つのタスクをClaude CodeとCodexが同時編集しない。**
- 状態:未着手/作業中/レビュー待ち/完了/保留
- 作業開始前に PROJECT_CHARTER.md・AI_HANDOFF.md・本文書を確認し、終了時に本文書と AI_HANDOFF.md を更新すること。

---

## フェーズ0:基盤(M0〜)

| ID | タスク | 内容 | 担当 | 状態 |
|---|---|---|---|---|
| T-001 | 設計ドキュメント作成 | 憲章・データモデル・調査方法・セキュリティ等の初版 | Fable | 完了 |
| T-002 | 設計レビュー | 実装観点での整合性確認。指摘10件はD-7〜D-21として反映し、再レビューで承認(重大な矛盾なし・実装開始可) | Codex | 完了 |
| T-003 | リポジトリ初期化 | Python環境、ディレクトリ構成(STORAGE.md)、.env.example、.gitignore(動画・モデル・座標・秘密情報除外)、環境確認テスト。音声ライブラリ比較の結果、公式 birdnet 0.2.16 を採用(D-22。推論スモークテストはM1着手時にローカル解析機で実施) | Claude Code | 完了(Issue #2) |
| T-004 | DBスキーマ実装 | DATA_MODEL.md 準拠のSQLiteスキーマ+マイグレーション基盤(全20テーブル。D-23〜D-25、T-005指摘対応済み)。PR #5でmainへマージ | Claude Code | 完了(Issue #4) |
| T-005 | スキーマレビュー | T-004 PRレビューとして実施。指摘4点+推奨1点+再レビュー1点の対応を確認し承認 | Codex | 完了 |

## フェーズ1:音声パイプライン(M1)

| ID | タスク | 内容 | 担当 | 状態 |
|---|---|---|---|---|
| T-101 | メディア登録 | 動画・音声取込(media_registry)、ストリーミングSHA-256+二重登録防止、FFprobeメタデータ、一時ファイル→照合→atomic rename、失敗時クリーンアップ、既存資産の不可侵・排他的確定、撮影開始日時の確実性ポリシー、確定処理の完全ロールバック・排他的フォールバック+読み戻し照合(D-26)。テスト19件(MVP要件1-3)。Codex承認済み・PR #8でmainへマージ | Claude Code | 完了(Issue #7) |
| T-102 | 音声抽出 | FFmpegで全時間抽出(無劣化優先)(MVP要件4) | Claude Code | 未着手 |
| T-103 | 音声イベント検出+BirdNET解析 | SED(種分類と独立。D-7)+全時間種分類、AudioDetection保存、AnalysisRun記録(MVP要件5,14)。ライブラリはT-003の決定に従う(D-13) | Claude Code | 未着手 |
| T-104 | クリップ/スペクトログラム生成 | 前後マージンつき(MVP要件6)。DerivedAssetとして登録(D-9) | Claude Code | 未着手 |
| T-105 | ジョブ再開機構 | JobStep/RunEventによる状態永続化、失敗時再開、二重実行防止。AnalysisRun完了後凍結(MVP要件15、D-10) | Claude Code | 未着手 |
| T-106 | 音声確認UI | 候補一覧・再生・スペクトログラム表示・判定入力(判定区分準拠) | Claude Code | 未着手 |
| T-107 | CSV出力(音声) | AI候補/人の判定の区別、位置丸め(MVP要件13) | Claude Code | 未着手 |
| T-108 | M1検証 | 実サンプル動画での動作検証、誤検出・見逃しの初期評価 | Codex | 未着手 |
| T-110 | Google Drive自動取込・結果返却 | IngestJob/IngestEvent(0002)、完了判定(時間間隔つき連続確認)、チャンクDL+サイズ検証、二重解析防止、results/<job_id>/返却(冪等)、再開・再試行、解析hook差込点(D-27)。Codex承認済み・PR #9でmainへマージ | Claude Code | 完了(Issue #6) |
| T-111 | 取込CLI | migrate/setup/check-config/run(--once/--interval/--dry-run)/status。単一ワーカー排他ロック、Ctrl+C安全停止、秘密情報マスク、OAuth前設定検査、Windows手順+E2Eチェックリスト(docs/WINDOWS_E2E.md)(D-28)。レビュー対応:dry-run/statusの完全読み取り専用化・ロック取得前倒し・interval入力制約。テスト14件。Codex承認済み・PR #11でmainへマージ | Claude Code | 完了(Issue #10) |
| T-112 | 撮影開始時刻の根拠優先順位 | 共通タイムラインの基準時刻改善:①動画メタデータcreation_time→②Drive modifiedTime→③ローカルファイル時刻→④人による補正(自動はestimated、人のみconfirmed=D-26維持)。E2E観察項目もdocsへ追加。要件はIssue #12。**短尺E2E成功後・T-102着手前に処理** | Claude Code | 未着手(要件登録済み・Issue #12) |

## フェーズ2:映像パイプライン(M2)

| ID | タスク | 内容 | 担当 | 状態 |
|---|---|---|---|---|
| T-201 | プロキシ生成 | 低解像度プロキシ(**UI確認・プレビュー専用**。検出には使わない。D-8)。DerivedAssetとして登録(D-9) | Claude Code | 未着手 |
| T-202 | マスク設定 | Station単位の除外領域エディタ・保存(MVP要件8) | Claude Code | 未着手 |
| T-203 | 動体検出 | フレーム間差分・背景差分・Optical Flow・追跡(MVP要件7)。**原解像度・タイル分割・複数解像度選択(D-8)** | Claude Code | 未着手 |
| T-204 | 候補分類 | 鳥/虫/葉/雲/航空機の粗分類、猛禽類候補度 | Claude Code | 未着手 |
| T-205 | 候補動画・サムネイル生成 | 前後マージン、軌跡画像(MVP要件9,10) | Claude Code | 未着手 |
| T-206 | 映像確認UI | 候補一覧・動画再生・判定入力 | Claude Code | 未着手 |
| T-207 | M2検証 | 実サンプルでの検出率・誤検出原因分析、パラメータ提案 | Codex | 未着手 |

## フェーズ3:統合(M3)

| ID | タスク | 内容 | 担当 | 状態 |
|---|---|---|---|---|
| T-301 | 共通タイムライン | 映像・音声の統合表示、関連候補(DetectionLink)提示(MVP要件11) | Claude Code | 未着手 |
| T-302 | 野帳照合・評価データ管理 | 野帳スクリーニング突合画面+ReferenceObservation登録・照合(役割分離。D-11、SURVEY_METHOD.md 5.2) | Claude Code | 未着手 |
| T-303 | アクセス制御・AccessLog | 3役割、正確位置の権限チェック、閲覧・出力記録。**完了後に正確座標のDB保持へ移行(それまでは地点ID・丸め座標のみ。D-12)** | Claude Code | 未着手 |
| T-304 | セキュリティレビュー | SECURITY.md 遵守確認(座標漏れ・ログ出力・Export丸め) | Codex | 未着手 |
| T-305 | M3統合検証 | エンドツーエンド検証、検証指標の初回計測 | Codex | 未着手 |

## フェーズ4:改善ループ(M4)

| ID | タスク | 内容 | 担当 | 状態 |
|---|---|---|---|---|
| T-401 | 誤検出集計 | 原因別集計・レポート | Claude Code | 未着手 |
| T-402 | ベンチマーク再解析 | ReferenceObservationを正解データとする固定評価セットでのモデル/パラメータ比較(D-11) | Codex | 未着手 |
| T-403 | 教師データ整理 | 確認済みデータのCustom Classifier用整理 | Claude Code | 未着手 |
