# 変更履歴(CHANGELOG.md)

## 2026-08-09(T-101再レビュー対応:確定処理の完全ロールバックとフォールバックの排他化)

- os.link成功後の一時ファイル削除失敗時、自分が作成した確定ファイルを取り消して再送出(DB行のない確定ファイルが残る経路を封鎖)
- ハードリンク非対応FSのフォールバックから os.replace を排除し、O_CREAT|O_EXCL の排他的作成+コピーへ変更(全経路で既存上書きが構造的に不可能)
- os.link のOSErrorをerrnoで判別(EPERM/EOPNOTSUPP/ENOSYS/EINVAL/EXDEVのみフォールバック、ENOSPC等は即失敗)
- 回帰テスト4件追加:後処理失敗の完全ロールバック/フォールバック経路の登録成功/フォールバックでも既存を上書きしない(TOCTOU再現)/非対象errnoはフォールバックしない(全52件パス)
- D-26の確定処理記述を更新

## 2026-08-09(T-101レビュー対応:既存資産の不可侵)

- 保存先衝突時は既存ファイルへ一切触れず失敗(PathCollisionError)。例外時に削除するのは本呼出しが作成したファイルのみ
- 確定処理を os.replace(上書きあり)から os.link による排他的・原子的作成へ変更(ハードリンク非対応FSのフォールバックと限界はD-26)
- register_media のトランザクション所有契約をdocstringへ明文化(SAVEPOINT化は将来検討)
- 回帰テスト3件追加:ID衝突時の既存行・ファイル不可侵/既存確定先の上書き・削除拒否/TOCTOU状況でも排他的確定(全48件パス)

## 2026-08-09(T-101 メディア登録。Issue #7)

- `bio_observer.media_registry` 追加:ローカル動画・音声のMediaAsset登録(MVP要件1〜3)
  - FFprobeメタデータ取得(codec/width/height/fps/sample_rate/channels/duration)
  - ストリーミングSHA-256+同一原本の二重登録防止
  - 原本は読み取りのみ。一時ファイル→ハッシュ照合→atomic renameで保存先へコピー
  - 失敗時はDB行・一時/確定ファイルを残さない(rename失敗注入テストで検証)
  - 撮影開始日時の確実性ポリシー:confirmed は人の入力・補正のみ、ファイル時刻由来は estimated(D-26)
  - 保存先・DBに元ファイル名を露出しない(不透明IDのみ)
  - 空き容量の事前確認
- テスト11件追加(全45件パス)

## 2026-08-09(T-005再レビュー指摘対応:系譜IDのイミュータブル化)

- analysis_run.media_asset_id / visual_detection.analysis_run_id / audio_detection.analysis_run_id を作成後変更禁止に(トリガー。同値UPDATEは許可)。親側更新による系譜整合の迂回を防止(D-23追記)
- 回帰テスト1件追加(全34件パス)

## 2026-08-09(追加要件登録:Google Drive自動取込 T-110)

- Google Drive受け箱による自動取込・結果返却を承認済み追加要件として登録(Issue #6、着手はT-101完了後・独立PR)
- ROADMAP.md(承認済み追加要件の節を新設)、TASKS.md(T-110)、AI_HANDOFF.mdへ追記(設計文書への最小限の追記のみ。Drive API実装は未着手)

## 2026-08-09(CodexレビューT-005指摘対応)

- media_asset:物理DELETE拒否・sha256変更拒否トリガーを追加(原則3)。原本同一性フィールドの方針をD-23へ追記
- 系譜整合トリガー:derived_assetのRun⇄メディア一致、derived_asset_detectionのRun一致を強制(誤接続の拒否)
- review:確認状態と判定内容の整合CHECK(SURVEY_METHOD.md 3.2.1に許容組合せ表を新設)、confirmed_taxon列を追加。NULL安全なIS/COALESCEを使用
- 時刻・範囲CHECK:検出・精査データ・撮影開始日時にUTC ISO-8601形式/開始≦終了/非負オフセット、Run・JobStep終端状態にfinished_at必須(failedはerror必須)。迂回防止方針をD-23へ記録
- reference_observation:二重確認に第二精査者+精査日時の両方を必須化
- derived_asset:present状態でsha256必須(推奨事項対応)
- テスト5件追加(全33件パス)

## 2026-08-09(先行成果品の分析に基づくT-004スキーマ補強。D-24/D-25)

- 先行成果品(Google Drive参考資料:画角別解析結果・track_summary約35特徴量・config_used約70パラメータ・positive/insurance区分・人によるスクリーニング結果)を分析し、将来の映像解析結果を保存できるかを検証
- DerivedAssetDetection(派生物⇄検出の多対多)を新設:1クリップ複数track/1track複数クリップに対応。DerivedAssetの単一検出FKは廃止
- visual_detection / audio_detection に候補区分(positive/insurance)+区分理由を追加
- media_asset に実時刻の確実性(confirmed/estimated/unknown)を追加、算出根拠にfile_timeを追加
- station に既定解析パラメータ(default_analysis_params_json)を追加
- derived_asset の種別に preview_image / report を追加
- Track特徴量はハイブリッド方式に決定(主要検索項目=固定列、その他=feature_schema_version付きJSON。D-24)
- DBテスト2件追加(全28件パス)。参考ファイル・動画はリポジトリへコピーしていない

## 2026-08-09(T-004 DBスキーマ・マイグレーション基盤。Issue #4)

- `bio_observer.db` 追加:DB接続(外部キー制約の強制有効化)、番号付きSQLマイグレーション基盤、schema_migrations管理(実装方式はD-23)
- 初回マイグレーション 0001_initial:DATA_MODEL.md 3.1〜3.18の全19テーブル+FK・一意制約・CHECK enum・インデックス
- 追記専用の担保:review/access_log/run_event のUPDATE/DELETE拒否トリガー、analysis_run の完了後凍結トリガー(D-1/D-10。限界はD-23に明記)
- 不透明ID生成(`ids.new_id`、登録制プレフィックス)・UTC日時ヘルパー(D-6、naive拒否)
- D-12遵守:正確な座標を保存する列は不存在(Siteは丸め表現+丸め粒度のみ。テストで列名検査)
- DBテスト19件追加(全26件パス)
- 解析機能・API・UIは未実装(T-101以降)

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
