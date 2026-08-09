# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Fable
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

設計フェーズ(M0)。CodexによるT-002設計レビューの指摘10件を設計文書へ反映済み(PR #1 に追加コミット)。Codexの再レビュー待ち。コードは未着手。

## 完了したこと

- 設計ドキュメント初版一式(PROJECT_CHARTER / ARCHITECTURE / DATA_MODEL / SURVEY_METHOD / SECURITY / ROADMAP / DECISIONS / TASKS / AI_HANDOFF / CHANGELOG)
- PR #1 作成(https://github.com/okillcalco-bot/bio-observer/pull/1)
- CodexレビューT-002指摘の反映(D-7〜D-13):
  - 音声イベント検出(SED)工程の追加(D-7)
  - プロキシはUI用限定、検出は原解像度・タイル分割・複数解像度選択(D-8)
  - DerivedAssetエンティティ追加(D-9)
  - AnalysisRun完了後凍結+JobStep/RunEvent追加(D-10)
  - ReferenceObservation追加、野帳照合とベンチマーク評価の分離(D-11)
  - T-303前は正確座標をDBへ保存しない(D-12)
  - 音声ライブラリはT-003で比較決定(D-13。birdnetlib固定を撤回)
- 旧判断待ちP-1〜P-8をD-14〜D-21として決定済みへ移行(判断待ちは現在ゼロ)
- 既存HTMLプロトタイプ2点を archive/prototypes/ へ移動(D-21)

## 未完了のこと

- CodexによるT-002再レビュー(指摘反映の確認)
- PR #1 のマージ(調査責任者の指示待ち。現時点ではマージしない)
- コードは一切未着手(環境構築・スキーマ・パイプライン・UIすべて)

## 次に行うべきこと

1. Codex:T-002再レビュー(D-7〜D-21の反映内容と文書間整合の確認)
2. 調査責任者:PR #1 のマージ判断
3. Claude Code:マージ後にT-003 リポジトリ初期化(音声ライブラリ比較決定を含む)→ T-004 DBスキーマ実装

## 既知の問題

- なし(既存プロトタイプのFirebase前提とD-2の不整合は、archive/prototypes/への隔離で解消。D-21)

## 判断が必要な事項

- 現在なし(DECISIONS.md の判断待ちはゼロ。P-1〜P-8はD-14〜D-21として決定済み)

## 実行したテスト/テスト結果

- なし(設計フェーズのためコード・テスト未作成)
- 文書間整合性の確認:D-7〜D-21の各決定が ARCHITECTURE / DATA_MODEL / SURVEY_METHOD / SECURITY / ROADMAP / TASKS の該当箇所へ相互参照つきで反映されていることを確認済み

## 使用モデル・環境

- 設計:Claude(Fable役)、Claude Code リモート実行環境(Linux)
- 実装予定:Python + FFmpeg + OpenCV + BirdNET(ライブラリはT-003で比較決定。D-13)+ SQLite(ARCHITECTURE.md 第8章)

## 関連コミット

- 設計初版:49aac3b(ブランチ:claude/raptor-monitoring-platform-design-dhw923、PR #1)
- Codexレビュー反映:本コミット(同ブランチ・同PR)
- 既存:7231874、cc5719a、cbeb83d

## 変更してはいけない事項

- PROJECT_CHARTER.md 第5章の原則1〜5(自動確定禁止/Recall優先/原データ不変/再現可能性・Run追記/機微情報保護)
- AI候補と人の判定(Review)のエンティティ分離(D-1)
- AnalysisRun(完了後凍結)・Review・AccessLog・RunEvent の追記のみ運用(上書き・物理削除禁止。D-10)
- SURVEY_METHOD.md 第3章の判定区分の名称・定義(変更は調査責任者の承認要)
- 正確な座標を外部サービス・ログ・リポジトリへ出さないこと。T-303実装まではDBへも保存しないこと(SECURITY.md、D-12)
- ベンチマーク評価の正解データはReferenceObservationとし、野帳記録で代用しないこと(D-11)
