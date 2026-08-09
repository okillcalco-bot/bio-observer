# Windows解析PCでの実Drive E2E手順(T-110/T-111)

- 対象:館山側Windows解析PCで、Google Drive受け箱の短尺動画2本(IMG_3355.MOV / IMG_3356.MOV)を使ったEnd-to-Endスモークテストを実施する
- 合否条件は**取込・系譜・再現性・結果返却**の正常動作。解析パイプライン(T-102以降)接続前のため、**鳥類・鳴声の検出精度、音声抽出・派生物生成は合否条件にしない**(Issue #6)
- **禁止事項**:動画・OAuth credentials/token・DB・結果ファイルをGitへコミットしない。Drive上の原動画を削除・移動・改名しない。正確な座標をDB・ログ・ファイル名へ入れない(SECURITY.md)

## 1. 事前準備(PowerShell)

前提:Python 3.11、FFmpeg(`ffmpeg`/`ffprobe` がPATHにあること。`winget install Gyan.FFmpeg` 等)

```powershell
git clone https://github.com/okillcalco-bot/bio-observer.git
cd bio-observer
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,drive]"
Copy-Item .env.example .env
notepad .env
```

`.env` に最低限設定する項目(**このファイルはGit管理外**):

| 変数 | 値 |
|---|---|
| `BIO_OBSERVER_DATA_ROOT` | ローカル保存先(例 `D:\bio-observer-data`) |
| `BIO_OBSERVER_DRIVE_INBOX_FOLDER_ID` | 受け箱フォルダURLの `folders/` 以降のID |
| `BIO_OBSERVER_DRIVE_CREDENTIALS_FILE` | OAuthクライアントの credentials.json のパス(Git管理外の場所) |
| `BIO_OBSERVER_DRIVE_TOKEN_FILE` | トークン保存先(例 `%USERPROFILE%\.bio-observer\token.json`) |

OAuthクライアントの準備(初回のみ):Google Cloud Consoleでプロジェクト作成 → Drive APIを有効化 → OAuthクライアントID(デスクトップアプリ)を作成 → credentials.json をダウンロードし上記パスへ配置。

## 2. 設定検査 → DB初期化 → 調査コンテキスト登録

```powershell
bio-observer check-config        # OAuth認可の前に .env・FFmpeg・DBを検査(Drive未接続)
bio-observer migrate             # DB初期化(全マイグレーション適用)
bio-observer setup --project "館山モニタリング" --site "地点A" `
  --rounded-position "<丸めメッシュ等>" --station "ST-1" `
  --equipment-type camera --survey-date 2026-08-09 --surveyor "調査者名"
```

- `setup` の出力末尾に `SurveySession: ses_xxxx` が表示される。以降 `--session` に使う
- 地点名・`--rounded-position` に**正確な座標・営巣地を特定できる名称を入れない**(D-12)

## 3. 取込の実行

```powershell
# まず一覧確認(Drive・DBとも変更しない)
bio-observer run --session ses_xxxx --once --dry-run

# 1サイクル実行(初回はブラウザでOAuth認可が1度開く)
bio-observer run --session ses_xxxx --once

# 60秒以上待って2回目(安定確認は最小60秒間隔の連続2回で成立)
bio-observer run --session ses_xxxx --once

# または継続実行(既定300秒間隔。Ctrl+Cで安全に停止=状態はDB保存済み)
bio-observer run --session ses_xxxx --interval 300

# 状態確認
bio-observer status
```

**単一ワーカー制約**:同じ `BIO_OBSERVER_DATA_ROOT` に対して `run` を同時に実行できるのは1プロセスのみ(排他ロックで保証。二重起動は起動時にエラー)。

## 4. E2Eチェックリスト

| # | 確認項目 | 確認方法 | 結果 |
|---|---|---|---|
| 1 | IMG_3355.MOV / IMG_3356.MOV の発見 | `--dry-run` で2本が `new` と表示 → `run` 後 `status` にジョブ2件 | ☐ |
| 2 | 60秒以上離した安定確認 | 1回目の `run` 直後は `waiting_for_upload`。**60秒以上空けた**2回目でダウンロードへ進む(60秒未満の連続実行では進まないことも確認) | ☐ |
| 3 | サイズ・FFprobeメタデータ・SHA-256の登録 | `status` が `completed`。DBの media_asset に codec/duration/width/height/sha256 が入る(`results/<job_id>/summary.csv` でも確認可) | ☐ |
| 4 | originals配下が不透明IDのみ | `<DATA_ROOT>\originals\prj_…\site_…\stn_…\ses_…\med_….mov` の形式で、`IMG_3355` 等の元ファイル名・地点名がパスに現れない | ☐ |
| 5 | Drive原本が未変更 | 受け箱の2本が残っており、名前・更新日時が変わっていない | ☐ |
| 6 | results/<job_id>/ への返却 | 受け箱直下 `results/` に job_id 名のフォルダが2つでき、各々に `status.json`・`summary.csv` がある(sha256がDBと一致) | ☐ |
| 7 | 再実行で重複しない | もう一度 `run --once` してもジョブ・MediaAssetが増えない(同じ動画を受け箱へ再アップロードした場合は新ジョブが `duplicate→med_…` で完了し、MediaAssetは増えない) | ☐ |
| 8 | 再起動後の再開 | 継続実行中に Ctrl+C(または処理途中でPC再起動)→ 再度 `run` すると未完了ジョブが途中状態から再開され完了する(`status` の遷移で確認) | ☐ |

問題が発生した場合は `bio-observer status` の最終エラーと、`results/<job_id>/status.json` を添えて報告してください(スクリーンショットに正確な座標・フォルダIDが写り込まないよう注意)。

### 観察項目(合否外。T-112の改善効果の比較基準として記録)

現状、Drive経由の取込では `recording_started_at` がダウンロード時刻に近い推定値になる既知事項がある(改善はIssue #12=T-112。撮影時刻の根拠優先順位:①動画内メタデータcreation_time→②Drive modifiedTime→③ローカル一時ファイル時刻→④人による補正・確定。自動取得は原則estimated、人の確認・補正のみconfirmed)。

`results/<job_id>/summary.csv` から以下を記録しておく:

| 観察項目 | IMG_3355 | IMG_3356 |
|---|---|---|
| recording_started_at の値 | | |
| 実際の撮影時刻との差 | | |
| recording_start_basis | | |
| recording_start_certainty | | |

## 5. 短尺で通った後(次工程)

4時間動画を受け箱へ置いて同じ手順を実行する(空き容量:動画サイズの約2倍以上を確保)。音声抽出・BirdNET/SED・候補クリップは T-102〜T-104 の接続後に `results/` へ追加される。
