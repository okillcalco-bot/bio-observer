# 変更履歴(CHANGELOG.md)

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
