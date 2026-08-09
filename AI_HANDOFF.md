# AI引き継ぎ(AI_HANDOFF.md)

- 最終更新:2026-08-09/更新者:Fable
- Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・本文書・TASKS.md を確認し、作業終了時に本文書と TASKS.md を更新すること。

---

## 現在の状態

**PR #1 マージ待ち。** 設計フェーズ(M0)の成果物はCodexによるT-002設計レビュー(再レビュー含む)で承認済み。文書間の重大な矛盾なし、実装開始を妨げる問題なし。コードは未着手(マージ後にT-003から開始)。

## 完了したこと

- 設計ドキュメント初版一式(PROJECT_CHARTER / ARCHITECTURE / DATA_MODEL / SURVEY_METHOD / SECURITY / ROADMAP / DECISIONS / TASKS / AI_HANDOFF / CHANGELOG)
- PR #1 作成(https://github.com/okillcalco-bot/bio-observer/pull/1)
- CodexレビューT-002指摘10件の反映(D-7〜D-21)と再レビューによる承認(T-002完了)
- 旧判断待ちP-1〜P-8をD-14〜D-21として決定済みへ移行(判断待ちは現在ゼロ)
- 既存HTMLプロトタイプ2点を archive/prototypes/ へ移動(D-21)
- 再レビュー時の最終指摘の反映(実装時の確認事項の明文化、ReferenceObservationの精査メタデータ追加)

## 未完了のこと

- PR #1 のマージ(調査責任者の実施待ち)
- コードは一切未着手(環境構築・スキーマ・パイプライン・UIすべて)

## 次に行うべきこと

1. 調査責任者:PR #1 のマージ
2. Claude Code:マージ後にT-003 リポジトリ初期化(音声ライブラリ比較決定D-13を含む)→ T-004 DBスキーマ実装
3. Codex:T-005 スキーマレビュー

## 実装時の確認事項(Codex再レビューでの申し送り)

実装者(Claude Code)は以下を満たすこと。Codexはレビュー時にこれを確認すること。

1. **SEDとBirdNETの検出を統合した後も、各モデルの生スコア・モデル版・判定根拠を統合前のまま保持する**(統合結果だけを残さない。DATA_MODEL.md 3.8)
2. **イミュータブルなエンティティを上書き更新しない**(完了後のAnalysisRun、Review、AccessLog、RunEvent。修正・状態変化は常に追記で表現する。D-1/D-10)
3. **ReferenceObservationに精査者・精査方法・確信度・二重確認の有無を持たせる**(DATA_MODEL.md 3.18。ベンチマーク正解データの品質を担保する)

## 既知の問題

- なし

## 判断が必要な事項

- 現在なし(DECISIONS.md の判断待ちはゼロ)

## 実行したテスト/テスト結果

- なし(設計フェーズのためコード・テスト未作成)
- 文書間整合性:D-7〜D-21の反映と相互参照をレビューで確認済み。Codex再レビュー結果=前回指摘すべて反映済み・重大な矛盾なし・実装開始可・PR #1承認

## 使用モデル・環境

- 設計:Claude(Fable役)、Claude Code リモート実行環境(Linux)
- 実装予定:Python + FFmpeg + OpenCV + BirdNET(ライブラリはT-003で比較決定。D-13)+ SQLite(ARCHITECTURE.md 第8章)

## 関連コミット

- 設計初版:49aac3b/Codexレビュー反映:1f7193e/最終更新:本コミット(いずれもブランチ claude/raptor-monitoring-platform-design-dhw923、PR #1)
- 既存:7231874、cc5719a、cbeb83d

## 変更してはいけない事項

- PROJECT_CHARTER.md 第5章の原則1〜5(自動確定禁止/Recall優先/原データ不変/再現可能性・Run追記/機微情報保護)
- AI候補と人の判定(Review)のエンティティ分離(D-1)
- イミュータブルなエンティティ(完了後のAnalysisRun・Review・AccessLog・RunEvent)の上書き更新禁止(D-10)
- SURVEY_METHOD.md 第3章の判定区分の名称・定義(変更は調査責任者の承認要)
- 正確な座標を外部サービス・ログ・リポジトリへ出さないこと。T-303実装まではDBへも保存しないこと(SECURITY.md、D-12)
- ベンチマーク評価の正解データはReferenceObservationとし、野帳記録で代用しないこと(D-11)
- 統合後の検出でも各モデルの生スコア・モデル版・判定根拠を保持すること(DATA_MODEL.md 3.8)
