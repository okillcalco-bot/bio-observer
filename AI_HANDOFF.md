# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Fable
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

設計フェーズ(M0)。Fableによる設計ドキュメント初版一式を作成済み。コードは未着手。
リポジトリには設計以前のHTMLプロトタイプ2点(bio-observer-firebase.html / bio-observer-research.html)が存在する(扱いは DECISIONS.md P-8)。

## 完了したこと

- PROJECT_CHARTER.md(憲章・目的・非目的・原則・ユースケース・役割分担・Git運用)
- ARCHITECTURE.md(システム構成・映像/音声パイプライン・共通タイムライン・技術選定)
- DATA_MODEL.md(15エンティティ定義・状態遷移・実装注意)
- SURVEY_METHOD.md(調査フロー・用語定義・判定区分・検証計画)
- SECURITY.md(機微情報保護・アクセス制御・運用)
- ROADMAP.md(MVP要件15項目・非機能要件・マイルストーンM0〜M4・将来拡張)
- DECISIONS.md(決定D-1〜D-6、判断待ちP-1〜P-8)
- TASKS.md(フェーズ0〜4のタスク分解・担当)
- CHANGELOG.md

## 未完了のこと

- 設計ドキュメントの調査責任者による承認
- コードは一切未着手(環境構築・スキーマ・パイプライン・UIすべて)

## 次に行うべきこと

1. Codex:T-002 設計レビュー(実装観点の整合性確認)
2. 調査責任者:DECISIONS.md の判断待ち P-1〜P-8 の確認(特にP-2位置丸め粒度、P-8既存プロトタイプの扱い)
3. Claude Code:T-003 リポジトリ初期化 → T-004 DBスキーマ実装(承認後)

## 既知の問題

- 既存HTMLプロトタイプはFirebase前提であり、D-2(ローカル実行・SQLite)と方針が異なる。統合せず参考資料扱いが暫定方針(P-8)
- 検証指標の数値目標は未設定(P-1)。実データ計測から開始する方針

## 判断が必要な事項

DECISIONS.md の「判断待ち」P-1〜P-8 を参照。

## 実行したテスト/テスト結果

- なし(設計フェーズのためコード・テスト未作成)

## 使用モデル・環境

- 設計:Claude(Fable役)、Claude Code リモート実行環境(Linux)
- 実装予定:Python + FFmpeg + OpenCV + BirdNET(birdnetlib)+ SQLite(ARCHITECTURE.md 第8章)

## 関連コミット

- 本ドキュメント群の追加コミット(ブランチ:claude/raptor-monitoring-platform-design-dhw923)
- 既存:7231874(bio-observer-research.html)、cc5719a、cbeb83d

## 変更してはいけない事項

- PROJECT_CHARTER.md 第5章の原則1〜5(自動確定禁止/Recall優先/原データ不変/再現可能性・Run追記/機微情報保護)
- AI候補と人の判定(Review)のエンティティ分離(D-1)
- AnalysisRun・Review・AccessLog の追記のみ運用(上書き・物理削除禁止)
- SURVEY_METHOD.md 第3章の判定区分の名称・定義(変更は調査責任者の承認要)
- 正確な座標を外部サービス・ログ・リポジトリへ出さないこと(SECURITY.md)
