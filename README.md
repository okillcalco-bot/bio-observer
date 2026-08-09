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

| [STORAGE.md](STORAGE.md) | データ保存方針(原データ/派生データ/モデル/DB/ログ) |
| [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) | 外部ソフトウェア・モデルのライセンス記録 |

Claude Code・Codexは作業開始前に PROJECT_CHARTER.md・AI_HANDOFF.md・TASKS.md を確認すること。

## データベース(T-004時点)

SQLite+SQLファイル・マイグレーション(方式はDECISIONS.md D-23)。スキーマの正は [DATA_MODEL.md](DATA_MODEL.md)。

```python
from bio_observer.db import connect, migrate
conn = connect("path/to/bio_observer.sqlite3")  # 外部キー制約を強制有効化
migrate(conn)                                   # 未適用マイグレーションを適用
```

## 取込CLI(T-111時点)

Google Drive受け箱からの自動取込をコマンドで実行できる(Windows手順の詳細は [docs/WINDOWS_E2E.md](docs/WINDOWS_E2E.md))。

```bash
bio-observer check-config       # OAuth認可前の設定検査(Drive未接続)
bio-observer migrate            # DB初期化
bio-observer setup --project P --site A --station ST-1 --survey-date 2026-08-09
bio-observer run --session ses_xxx --once --dry-run   # 一覧確認(Drive・DB無変更)
bio-observer run --session ses_xxx --interval 300     # 継続実行(Ctrl+Cで安全停止)
bio-observer status             # ジョブ一覧・最終エラー
```

同一DATA_ROOTで同時に実行できるワーカーは1プロセスのみ(排他ロック)。

## セットアップ(T-003時点)

前提:Python 3.11、FFmpeg/FFprobe(6.x で確認済み)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # 開発用(テスト含む)
# pip install -e ".[audio]"    # 音声解析(birdnet。M1で使用。初回実行時にモデルを自動取得)
cp .env.example .env           # 保存場所等を編集(正確な座標・秘密情報は書かない)
bio-observer-envcheck          # 環境確認
pytest                         # 環境確認テスト
```

> **BirdNETのライセンス注意**:ソースコードはMITだが、学習済みモデルは **CC BY-NC-SA 4.0(非商用条件)**。本プロジェクトでは研究・技術検証での利用を前提とする。有償調査・解析サービス・商品化への利用可否は未確認であり、本番利用前に権利者への確認が必須([THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md))。

`archive/prototypes/` 配下のHTMLは設計以前のプロトタイプ(参考資料。DECISIONS.md D-21 参照)。
