# bio-observer

猛禽類・鳥類長時間モニタリング基盤。長時間の定点映像と環境音をAIで解析し、鳥類調査の確認作業を支援するシステム。

AIは「候補」を提示するのみで、種・行動の確定は常に調査者が行う(調査支援であり、自動確定ではない)。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [PROJECT_CHARTER.md](PROJECT_CHARTER.md) | プロジェクト憲章・目的・変えてはいけない原則(上位ルール) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | システム構成・解析パイプライン |
| [DATA_MODEL.md](DATA_MODEL.md) | データモデル |
| [SURVEY_METHOD.md](SURVEY_METHOD.md) | 調査フロー・判定区分・用語定義・検証計画 |
| [SECURITY.md](SECURITY.md) | 希少種情報の保護方針 |
| [ROADMAP.md](ROADMAP.md) | MVP要件・非機能要件・将来拡張 |
| [DECISIONS.md](DECISIONS.md) | 意思決定記録・未決事項 |
| [TASKS.md](TASKS.md) | 実装タスク分解・担当 |
| [AI_HANDOFF.md](AI_HANDOFF.md) | AI間の引き継ぎ(作業開始前に必読) |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・AI_HANDOFF.md・TASKS.md を確認すること。

`bio-observer-firebase.html` / `bio-observer-research.html` は設計以前のプロトタイプ(扱いは DECISIONS.md P-8 参照)。
