# 意思決定記録(DECISIONS.md)

- 文書区分:Fable管理文書
- 運用:設計・実装上の判断は本文書に追記する。決定済み(D-xx)と判断待ち(P-xx)を区別する。実装の都合でFableの原則に影響が出る場合、実装者は勝手に変更せずP-xxとして起票する。

---

## 決定済み

### D-1:AI判定と人の判定をエンティティレベルで分離する
- 日付:2026-08-09/決定者:Fable(憲章 原則1に基づく)
- 内容:AI出力は VisualDetection/AudioDetection、人の判定は Review に保存し、Reviewは追記のみとする。
- 理由:自動確定の禁止と修正履歴の保全を構造で保証するため。

### D-2:MVPはローカル実行・SQLiteで開始する
- 日付:2026-08-09/決定者:Fable(合理的仮定)
- 内容:解析・DB・確認UIをローカル完結とし、DBはSQLite(PostgreSQL移行可能なスキーマ)とする。
- 理由:MVP要件がローカル動画前提であり、現地・オフラインでの確認作業を優先。クラウド解析は非必須と明記されている。

### D-3:音声パイプライン(M1)を映像(M2)より先行させる
- 日付:2026-08-09/決定者:Fable(合理的仮定)
- 内容:マイルストーンは M1音声 → M2映像 → M3統合 の順とする。
- 理由:BirdNETにより早期に実用価値が出る。映像より技術リスクが低く、「全時間解析」「原データ不変」「Run追記」の設計検証を先に済ませられる。

### D-4:動き検出は差分系を主、Optical Flowを補助として併用する
- 日付:2026-08-09/決定者:Fable(合理的仮定)
- 内容:フレーム間差分+背景差分を一次抽出、Optical Flowを軌跡・方向推定の補助に使う。閾値はRecall優先で初期設定。
- 理由:長時間動画の処理コストと見逃し最小化の両立。組み合わせはAnalysisRunパラメータとして記録し、後から比較変更可能。

### D-5:外部サービスへ正確な座標を送信しない
- 日付:2026-08-09/決定者:Fable(SECURITY.md に基づく)
- 内容:BirdNET等に位置情報を与える場合は丸めた座標を使用する。
- 理由:機微情報保護。丸め座標でも種フィルタリングの効果は十分得られる。

### D-6:時刻はUTC保存・タイムゾーン保持
- 日付:2026-08-09/決定者:Fable(合理的仮定)
- 内容:DB内の時刻はUTC、SurveySession/MediaAssetにタイムゾーンを保持し表示時に変換。
- 理由:複数機器同期・将来の複数地点展開での曖昧さ排除。

---

## Codex設計レビュー(T-002)による決定(2026-08-09、調査責任者承認)

### D-7:BirdNET種分類と独立の音声イベント検出(SED)工程を追加する
- 内容:音声パイプラインに、種分類とは独立の音声イベント検出工程を設け、未知・低信頼度の鳴声を種候補なしの AudioDetection として拾い上げる。
- 理由:BirdNETの学習外の声・遠い声・幼鳥の声等の見逃しを防ぐ(Recall優先の原則2の安全網)。
- 反映:ARCHITECTURE.md 第4章、DATA_MODEL.md 3.8、ROADMAP.md MVP要件5。

### D-8:低解像度プロキシはUI確認用に限定し、映像検出は原解像度・タイル分割・複数解像度を選択可能とする
- 内容:検出処理はプロキシではなく原解像度を基本とし、タイル分割・解像度選択をAnalysisRunパラメータで制御する。
- 理由:遠方の小さな飛翔体はプロキシ解像度では消失し、見逃しに直結するため。処理時間との両立はパラメータで調整。
- 反映:ARCHITECTURE.md 第3章、ROADMAP.md MVP要件7・非機能要件。

### D-9:DerivedAssetエンティティを追加する
- 内容:クリップ・WAV・スペクトログラム・サムネイル・軌跡画像・プロキシ等の派生物を DerivedAsset として一元管理し、系譜(元データ・生成Run・対応検出)・ハッシュ・生成条件・再生成可否/状態を保持する。
- 理由:憲章 原則3(派生データの系譜管理)を構造で保証し、ディスク逼迫時の安全な削除・再生成を可能にする。
- 反映:DATA_MODEL.md 3.16、ARCHITECTURE.md 第3・4章。

### D-10:AnalysisRunは完了後に凍結し、実行中の状態・再開情報はJobStep/RunEventへ追記する
- 内容:AnalysisRun本体はイミュータブル(完了後凍結)とし、実行中の進捗・状態変化・エラー・再開オフセットは JobStep/RunEvent に追記する。
- 理由:憲章 原則4(再現可能性・上書き禁止)とMVP要件15(再開)を、可変状態と不変記録の分離で両立する。
- 反映:DATA_MODEL.md 3.6・3.17、ARCHITECTURE.md 第2・8章。

### D-11:ReferenceObservation(精査済み評価データ)を追加し、野帳照合とベンチマーク評価を分離する
- 内容:速報的な現地野帳記録(SurveySession保持)は見逃しスクリーニング用、精査済みの ReferenceObservation は定量指標(検出率・見逃し率)算出用と役割を分ける。
- 理由:時刻精度の粗い野帳を正解データに使うと評価が歪むため。
- 反映:DATA_MODEL.md 3.4・3.18、SURVEY_METHOD.md 5.2。

### D-12:T-303(アクセス制御)実装前は正確座標をDBへ保存しない
- 内容:アクセス制御・AccessLog実装完了までは、DBに地点ID・丸め座標のみを保持し、正確位置は調査責任者がDB外で管理する。
- 理由:権限チェックと閲覧記録のない期間に機微情報をシステムへ置かない(憲章 原則5)。
- 反映:DATA_MODEL.md 3.2、SECURITY.md 第3・4章。

### D-13:音声解析ライブラリはbirdnetlibに固定せず、T-003で比較決定する
- 内容:公式 birdnet Python library と BirdNET-Analyzer を、精度・処理速度・保守性・Custom Classifier対応の観点でT-003にて比較し決定する。比較結果と選定理由は本文書へ追記する。
- 理由:birdnetlibはサードパーティ製であり、公式ラインの保守状況を確認した上で選定すべきため。
- 反映:ARCHITECTURE.md 第2・4・8章、TASKS.md T-003。

### D-22:音声解析ライブラリは公式 birdnet Python library を採用する(T-003比較検証の結果)
- 日付:2026-08-09/決定者:Claude Code(T-003。D-13の比較方針に基づく)/Issue #2
- 比較対象とバージョン:公式 `birdnet` 0.2.16(PyPI)/`birdnet-analyzer` 2.4.0(PyPI)。いずれもBirdNETチーム(Stefan Taubert / Stefan Kahl)による
- ライセンス:**ソースコードはMIT、学習済みモデル(v2.4等)はCC BY-NC-SA 4.0(非商用条件)**。研究・技術検証での利用を前提とし、有償調査・解析サービス・商品化への利用可否は未確認のため、本番利用前に権利者確認を必須とする(THIRD_PARTY_LICENSES.md)
- 検証環境:Python 3.11.15、Linux(Claude Codeリモート実行環境)、pip 24.0

**比較結果**

| 観点 | birdnet 0.2.16 | birdnet-analyzer 2.4.0 |
|---|---|---|
| 位置づけ | パイプライン組み込み用の公式ライブラリ | CLI/GUIを持つ公式アプリケーション |
| API | `birdnet.load("acoustic", "2.4", "tf")` → `AcousticPredictionSession` によるプログラマブルAPI。Perch v2対応(`load_perch_v2`) | `python -m birdnet_analyzer.analyze` のCLI実行が基本(モジュール:analyze/train/embeddings/segments/gui等) |
| Custom Classifier | **実行**:`birdnet.load_custom(...)` で対応 | **学習**:`birdnet_analyzer.train` で対応 |
| 依存規模 | 51パッケージ・約2.6GB(TensorFlow含む)。ai-edge-litert対応 | 68パッケージ・約2.8GB(TensorFlow、librosa、matplotlib等) |
| インストール | `pip install birdnet==0.2.16` 成功 | `pip install birdnet-analyzer==2.4.0` 成功 |
| モデル | BirdNET v2.4系。初回実行時に zenodo.org から自動取得 | BirdNET v2.4系。初回実行時に tuc.cloud から自動取得 |
| 推論精度 | 同一モデル系列(v2.4)を使用するが、**前処理・バックエンド・設定を含む実際の出力一致は未検証** | 同左 |
| モデルライセンス | CC BY-NC-SA 4.0(非商用条件。コードのMITと異なる) | 同左 |

**決定**
- 解析パイプライン(M1/T-103)への組み込みは **公式 `birdnet` 0.2.16** を採用する(pyproject.toml の optional-dependencies `audio` にピン留め)
- **BirdNET-Analyzer はアプリの実行時依存にはしない。** Custom Classifier の学習(M4/T-403)と結果のクロスチェック用の独立ツールとして利用する
- 採用理由:(1) セッション型のプログラマブルAPIでAnalysisRun記録・JobStep再開と整合させやすい、(2) `load_custom` により将来のCustom Classifier実行に対応(D-13の要件)、(3) 依存がやや軽く、アプリからのサブプロセスCLI呼び出しよりエラー処理・進捗取得が確実、(4) 同一チームの公式ライブラリであり、同一モデル系列(v2.4)を使用する(ただし実際の出力一致は未検証。下記の制約参照)

**依存固定の状態(正確な記載)**
- `birdnet` 本体は 0.2.16 に固定(pyproject.toml optional-dependencies `audio`)
- `requirements-dev.lock` は**開発依存(dev)のみ**のロックであり、birdnetの**推移依存はまだ完全固定されていない**
- ローカル対象環境で推論成功後に、audio依存を含むlock(例:`requirements-audio.lock`)を生成すること(AI_HANDOFF.md申し送り)

**実行方法(記録)**
```bash
pip install ".[audio]"   # または pip install birdnet==0.2.16
python -c "
import birdnet, pathlib
model = birdnet.load('acoustic', '2.4', 'tf')      # 初回はモデル自動ダウンロード
with birdnet.AcousticPredictionSession(model) as s:
    result = s.predict(pathlib.Path('sample.wav'))
"
```

**制約(重要)**:本検証環境ではモデル配布元(zenodo.org / tuc.cloud)への通信が組織のegressポリシーで遮断されており(プロキシ403)、**推論実行までは未検証**。インストール・API検証・依存関係までを確認済み。T-103のローカル推論確認時に以下を必ず実施し、結果を本文書へ追記すること:
- 推論スモークテスト(モデルダウンロード+合成WAVでの実行+処理速度計測)
- **モデルの保存先・モデル形式・バージョン・SHA-256・オフライン再利用方法の確定**(モデルキャッシュを `BIO_OBSERVER_MODELS_DIR` 等の管理対象ディレクトリへ固定できるかの検証を含む。現時点でライブラリ既定の保存先は未確認)
- 速度・精度に問題が出た場合は本決定を見直す(その場合も本エントリは上書きせず追記で改訂する)

### D-23:DB実装方式(T-004)— 標準ライブラリsqlite3+SQLファイル・マイグレーション
- 日付:2026-08-09/決定者:Claude Code(T-004)/Issue #4
- **方式**:ORMを使わず、標準ライブラリ `sqlite3` + 番号付きSQLマイグレーションファイル(`src/bio_observer/db/migrations/NNNN_name.sql`)+ 小さなランナー(`bio_observer.db.migrate`)。適用履歴は `schema_migrations` テーブルで管理し、各マイグレーションは1トランザクションで適用する。
- **代替案と不採用理由**:SQLAlchemy+Alembic(業界標準でPostgreSQL移行が滑らか)は、MVPの依存最小方針・スキーマの見通し(生SQLでレビュー可能)を優先して現時点では不採用。テーブル数がさらに増える・PostgreSQL移行が現実化した時点で再評価する(移行時はSQLファイルがそのままDDLの正となる)。
- **外部キー**:接続ヘルパー `connect()` が `PRAGMA foreign_keys = ON` を必ず実行し、有効化を確認できなければ接続を拒否する。
- **不透明ID**:`<エンティティ略号>_<uuid4hex>`(例:`site_3f2a…`)。略号は `ids.ID_PREFIXES` に登録制とし、表示名由来のプレフィックスを拒否する。パス・IDに地点名・希少種名を使わない(STORAGE.md)。
- **日時**:UTCのISO-8601文字列(`YYYY-MM-DDTHH:MM:SSZ`)で保存(D-6)。naive datetimeはヘルパーが拒否する。
- **enum**:TEXT+CHECK制約。判定区分はSURVEY_METHOD.md第3章のASCIIコード対応(例:`species_confirmed`)。
- **座標**:正確な座標列は作らない(D-12)。Siteは丸め表現(`rounded_position`:メッシュコード・市区町村名等の文字列)+`rounding_level` のみ。数値の緯度経度列は丸め済みであっても現段階では設けない(テストで列名を検査)。
- **追記専用の担保**:DBトリガーで実装。`review`/`access_log`/`run_event` はUPDATE/DELETEを常に拒否。`analysis_run` は終端状態(completed/failed/aborted)後のUPDATEと、状態を問わずDELETEを拒否(実行中の状態遷移・完了時の確定更新は許可。D-10)。
  - **限界(明記)**:SQLiteのトリガーはDDL権限があれば削除可能であり、DBファイルへ直接アクセスできる利用者に対する完全な防御ではない。誤操作(アプリのバグ・手動SQL)への防御と位置づける。PostgreSQL移行時はロール権限(INSERT可・UPDATE/DELETE不可)での担保へ置き換える。
- 反映:src/bio_observer/db/、tests/test_db_migrations.py、tests/test_db_constraints.py。

**追記(2026-08-09、CodexレビューT-005対応)**
- **原データの物理DELETE禁止**:media_asset はDELETE拒否トリガーで保護(削除は論理削除 deleted_at のみ)。**原本同一性フィールドの方針**:sha256 は登録後変更禁止(トリガーで拒否)。relative_path は保管先再編成時のみ変更可とする(同一性の根拠はハッシュであり、パスは可変)。その他のメタデータ列(コーデック・長さ等)は再取得での訂正を許容する。
- **系譜整合の強制**:derived_asset の media_asset_id は analysis_run の media_asset_id と一致必須、derived_asset_detection が対応づける検出はその派生物を生成したRunの検出に限る(いずれもINSERT/UPDATEトリガーで拒否)。
- **Review整合CHECK**:確認状態と判定内容の許容組合せをCHECK制約で強制(定義は SURVEY_METHOD.md 3.2.1)。属・科確認用に confirmed_taxon 列を追加。**SQLiteのCHECKはNULL評価で素通りするため、NULL安全な IS / COALESCE を使用する**(実装上の注意として明記)。
- **時刻・範囲の整合CHECKの適用範囲**:共通タイムラインに直結する列(visual/audio_detection の開始・終了時刻とメディア内オフセット、reference_observation の時刻、media_asset.recording_started_at)にUTC ISO-8601形式(GLOB)・開始≦終了・非負オフセットのCHECKを付与。analysis_run/job_step の終端状態には finished_at 必須(failedはerrorも必須)。**迂回防止方針**:その他の時刻列(created_at等)はDB制約を課さず、書込は必ず `ids.utc_now_iso()`/`to_utc_iso()` ヘルパー経由とする(naive拒否)。生SQLでの直接書込はテスト・マイグレーションを除き禁止し、コードレビューで担保する。将来書込APIを実装する際(T-101以降)、時刻列への書込をヘルパーへ一元化する。
- **DerivedAssetのsha256**:regeneration_state='present'(実体が存在)ではsha256必須(CHECK)。NULLを許すのは生成途中(regenerating)・削除済み(deleted_regenerable)・生成失敗のみ。
- **Run/Detectionの系譜IDは作成後変更禁止**(T-005再レビュー指摘対応):`analysis_run.media_asset_id`、`visual_detection.analysis_run_id`、`audio_detection.analysis_run_id` は作成後イミュータブル(トリガーで変更拒否。同値UPDATEは許可)。関連付け後に親側のIDを差し替えて系譜整合トリガーを迂回する抜け道を塞ぐ。

### D-24:Track特徴量はハイブリッド方式(主要検索項目=固定列、その他=バージョン付き構造化データ)
- 日付:2026-08-09/決定者:Claude Code(T-004。先行成果品の分析に基づく)/Issue #4
- 背景:先行成果品(Google Drive参考資料)のtrack_summary.csvは1トラック約35列の特徴量(直進性、面積変化率、Flow magnitude統計、動的Flow閾値、境界接触率、局所コントラスト等)を持ち、これらは手法改善に伴い**増減することが確実**。
- 決定:**すべてを固定列にしない。** 主要検索項目(開始・終了時刻、座標、移動方向、速度、見かけの大きさ、羽ばたき、候補分類、猛禽類候補度、AI信頼度、候補区分)のみ visual_detection の固定列とし、その他の生特徴量は `features_json` に **`feature_schema_version` を付けて**構造化データとして保持する。
- 代替案と不採用理由:(a) 全固定列=特徴量の追加・変更のたびにマイグレーションが必要でRun間の比較も崩れる。(b) EAV(縦持ち)=クエリが複雑化し型安全性も低い。ハイブリッドは検索性能(固定列+インデックス)と拡張性を両立する。
- 運用:feature_schema_version はAnalysisRunのモデル版・パラメータとともに記録され、版が異なるRun間の特徴量比較は同版内でのみ行う。頻繁に検索する特徴量が固定した段階で列へ昇格させる(昇格は新マイグレーションで行い、features_json側も残す)。

### D-25:先行成果品の分析に基づくスキーマ補強(T-004)
- 日付:2026-08-09/決定者:Claude Code(T-004)/Issue #4
- 背景:先行成果品(画角別解析結果・約70パラメータのconfig_used・positive/insurance区分・クリップ⇄トラック対応・ファイル時刻由来の実時刻推定・人によるスクリーニング結果)を、原則(コピーではなくCHARTER/DATA_MODEL/SECURITY優先)に照らして取り込んだ。
- 決定内容:
  1. **DerivedAssetDetection(多対多)を新設**:動体Trackと抽出クリップの多対多関係(1クリップ複数track/1track複数クリップ)に対応。DerivedAssetの単一検出FKは廃止
  2. **候補区分(candidate_tier: positive/insurance)+区分理由**を visual_detection / audio_detection に追加(Recall優先の保険的候補を明示的に管理。原則2/D-18)
  3. **実時刻の確実性**:media_asset に recording_start_certainty(confirmed/estimated/unknown)を追加し、算出根拠enumへ file_time(ファイル作成・更新時刻からの推定)を追加
  4. **Station既定解析パラメータ**(default_analysis_params_json)を追加:画角ごとに検出特性・マスク・閾値が大きく異なるため。実際に使った値は常にAnalysisRunのスナップショットが正
  5. **DerivedAsset種別に preview_image(マスク・閾値プレビュー)と report(集計CSV等)を追加**:先行成果品のプレビューJPG・サマリCSV相当を派生物として系譜管理する
- 先行成果品から**採用しなかった**点:表示名ベースのファイル名・フォルダ名(不透明ID方式を維持。STORAGE.md)、クリップ単位のみの人判定(本設計は検出単位のReviewを正とし、クリップはDerivedAssetとして対応付ける)、CSV上での判定と候補の混在(AI候補とReviewの分離を維持。D-1)
- 参考資料の扱い:Google Drive上の参照のみ。ファイル・動画はリポジトリへコピーしない

### D-26:メディア登録の設計(T-101)
- 日付:2026-08-09/決定者:Claude Code(T-101)/Issue #7(レビュー対応の経緯は下の追記参照)
- **コピーとハッシュの1パス化+二重照合**:原本を読み取りのみでコピーしながらSHA-256を計算し(1パス)、コピー先(`.part`)を再ハッシュして原本と照合、成功後に**排他的確定**(下の追記:os.link、フォールバック時はO_EXCL+読み戻し照合)で確定パスを作成する。コピー破損は登録前に検知される。
- **失敗時のクリーンアップ保証**:登録は「probe→容量確認→コピー→照合→重複確認→DB挿入→排他的確定→commit」の順で行い、例外時は rollback+**本呼出しが作成したファイルのみ**削除を必ず実行する。登録失敗で中途半端なDB行・ファイルは残らず、既存資産へは一切触れない(テストで確定失敗・破損を注入して検証)。
- **撮影開始日時の確実性ポリシー**:`certainty='confirmed'` は人の入力・補正(`basis='manual'/'corrected'`)のみ許可。自動取得(埋め込みメタデータ・ファイル時刻)由来は最大でも `estimated` とし、確定日時として自動断定しない。未指定時はファイル更新時刻から `file_time`/`estimated` として推定する。
- **元ファイル名を保持しない**:保存先パス・DBに元ファイル名を使わず露出させない(不透明IDのみ。STORAGE.md)。取込元ファイル名の記録が必要になるのはDrive取込(T-110)であり、その際は取込専用エンティティ(IngestJob等)側に保持する。
- **対応形式**:拡張子ホワイトリスト(mov/mp4/mts/m2ts/avi/mkv/wav/mp3/flac/m4a/ogg)+ffprobeによる実体検証の二段構え。
- **空き容量の事前確認**:コピー前に保存先の空き容量(原本サイズ+1%)を確認する。

**追記(2026-08-09、T-101レビュー対応)**
- **既存資産の不可侵**:確定先・一時パスが既に存在する場合は、既存ファイルへ一切触れず PathCollisionError で失敗する。例外時に削除するのは**本呼出しが作成したファイルのみ**(一時ファイルは常に本呼出し作成=事前確認で保証、確定ファイルは本呼出しが確定した場合のみ削除)。
- **排他的・原子的な確定**:`os.replace`(既存を上書きする)を全経路から排除。第一手段は同一ディレクトリ内の `os.link` による確定名の排他的作成。ハードリンク非対応FS(exFAT等)は **errno判別**(EPERM/EOPNOTSUPP/ENOSYS/EINVAL/EXDEVのみフォールバック、ENOSPC等は即失敗)の上で **O_CREAT|O_EXCL による排他的作成+コピー**へフォールバックする。どの経路でも既存ファイルの上書きは構造的に不可能(TOCTOU窓なし)。フォールバック経路では、コピー後に**finalを読み戻してSHA-256・サイズを期待値と照合**し、不一致(コピー破損)時はDB・一時ファイル・確定ファイルを完全に取り消す(DBのハッシュと実ファイルが食い違う状態を残さない)。リンク経路は検証済み一時ファイルと同一inodeを指すため再照合不要。フォールバック経路は rename でないためプロセスクラッシュで部分ファイルが残り得るが、DB行のない孤児ファイルであり原本喪失はない(検出・掃除はT-110の取込ワーカーで扱う)。
- **確定後の後処理も完全ロールバック**:`os.link` 成功後の一時ファイル削除に失敗した場合、自分が作成した確定ファイル(リンク)を取り消して例外を再送出する。「DB行のない確定ファイル」が残る経路を塞ぐ。フォールバックのコピー途中失敗も、自分が排他的に作成した確定ファイルのみ削除して再送出する。
- **トランザクション契約の明文化**:`register_media` は接続のトランザクション所有者として振る舞い、成功時 `conn.commit()`/失敗時 `conn.rollback()` を接続全体へ発行する。呼び出し側の未確定変更と同一トランザクションで合成しないこと。将来、取込ワーカー(T-110)等で他のDB操作と合成する必要が生じた場合はSAVEPOINTによる局所トランザクション化を検討する(非ブロッキング申し送り)。

**追記(2026-08-09、T-112:撮影開始時刻の根拠優先順位。Issue #12)**
- 背景:Drive経由の取込ではローカル一時ファイルの更新時刻がダウンロード時刻となり、共通タイムラインの基準である `recording_started_at` が実撮影時刻からズレる既知事項があった。解析(T-102以降)開始前に基準時刻を正す。
- **自動推定の優先順位**:①動画内メタデータの creation_time(format tags→stream tags。basis=metadata)→ ②取込元の更新時刻 `origin_modified_time`(Drive の modifiedTime 等。basis=file_time)→ ③ローカルファイル時刻(basis=file_time。最後の手段)。自動推定は常に certainty=estimated。④人による補正・確定(basis=manual/corrected)のみ confirmed を許可(本決定の確実性ポリシーを維持)。
- **採用根拠の記録**:media_asset のスキーマは変更せず(basis/certainty列のまま)、採用した根拠の詳細(media_metadata_creation_time / origin_modified_time / local_file_mtime / caller)を RegistrationResult・ingest_event(registered遷移のdetail)・results/<job_id>/status.json に記録する。列追加(マイグレーション)を伴わない最小変更を選択。
- **タイムゾーン解釈**:creation_time 等はISO-8601として解釈し、Z/±HH:MM/±HHMM/小数秒/空白区切りを受理してUTCへ正規化する。**タイムゾーン表記のない値はUTCとみなす**(FFmpegのcreation_timeはUTC表記が慣例)。解釈不能な値は採用せず次の優先順位へ進む。カメラ機種によるローカル時刻表記の可能性は、estimated に留めることと人の補正(④)で吸収する。

### D-27:Google Drive自動取込の設計(T-110)
- 日付:2026-08-09/決定者:Claude Code(T-110)/Issue #6
- **取込状態は専用エンティティで管理**:IngestJob(状態機械)+IngestEvent(追記専用の遷移ログ)を新設(マイグレーション0002、DATA_MODEL.md 3.20/3.21)。解析実行のJobStep/RunEventとは分離する(取込は解析Runの前段であり、RunEventはanalysis_runに紐づくため)。状態変化は上書きに加えて必ずIngestEventへ追記する。
- **アップロード完了判定**:Drive APIのサイズ・modifiedTimeを**連続N回(既定2回)の確認で不変**のときに完了とみなす(4時間動画の途中取得防止)。観測履歴はstable_probe_jsonに保持。
- **安全なダウンロード**:一時領域(`<DATA_ROOT>/ingest_tmp`)へ `.part` 拡張子でチャンクDL→取得サイズをDriveメタデータと検証→一時領域内の確定名へ移動。originals/への最終確定と原本保護はregister_media(D-26)が担う。DL済み一時ファイルは登録成功後に削除する(原本はoriginals/とDrive上に存在)。空き容量はDL前に確認。
- **二重解析防止の二段構え**:同じDrive File IDはUNIQUE制約で再取込しない。別File IDでも同一SHA-256ならregister_mediaのDuplicateMediaErrorを捕捉し、duplicate_of_media_asset_id を記録して完了(新規登録なし)。重複ジョブにも結果(status.json)は返却する。
- **結果返却**:入力フォルダ直下を汚さず `results/<job_id>/` へ status.json・summary.csv を返却(将来のクリップ・スペクトログラム等はvideo_clips/等のサブフォルダへ追加)。Drive上のフォルダ名はjob id(不透明ID)であり表示名と混同しない。座標・希少種名・地点名は含めない。
- **再開性**:状態はDBが正。ワーカーの process_pending() は未完了ジョブを状態から続行できる(PC再起動対応)。失敗時は retry_required(復帰先resume_status保持)→上限(既定3回)超過で failed。
- **解析パイプラインの差込点**:analysis_hook(未指定ならスキップ)として分離。T-102以降のパイプライン実装後に接続する。Issue #6のE2Eスモークテストのうち音声抽出・派生物生成はT-102/T-104接続後に検証する(初回スモークは取込・系譜・再現性・結果返却まで。検出精度は合否条件外)。
- **Drive操作の制約**:元動画に対しては読み取りのみ(削除・移動・改名のAPIを実装しない)。書き込みはresults配下の作成・アップロードのみ。
- **実装方式**:ワーカーはDriveClientプロトコルに依存し、自動テストはフェイク実装で全状態遷移を検証(実Drive APIはネットワーク・OAuth必須のため)。実運用実装はgoogle-api-python-client 2.198.0/google-auth 2.56.3/google-auth-oauthlib 1.4.0(optional-dependencies `drive` にピン留め)。OAuth認証情報・トークン・フォルダIDは環境変数でGit管理外のパス・値を指定(SECURITY.md)。
- **実機E2Eスモークテスト**:Windows解析PC上で受け箱の短尺2本(IMG_3355/3356.MOV)を用いて実施する(AI_HANDOFF.md申し送り)。

**追記(2026-08-09、T-110レビュー対応)**
- **完了判定に最小時間間隔を導入**:連続確認は `stability_interval_seconds`(既定60秒)以上の実時間を空けた観測のみ数える。間隔不足の観測は確認回数・基準時刻を進めない(ワーカーを連続実行しても数秒で2回確認扱いにならず、4時間動画の途中取得を防ぐ)。
- **登録失敗時はDL済みファイルを保持**:一時DLファイルを削除するのは登録に決着した場合(登録成功/重複確定)のみ。一時的な登録失敗では保持したまま再試行し、「ファイルがない」で詰まらない。downloaded状態でファイルが消えていた場合も、エラーにせず再取得(downloading)へ戻して続行する。
- **重複ジョブの完了は結果返却後**:同一ハッシュ検出時は completed へ直行せず uploading_results へ遷移する。結果返却前にクラッシュしても、再開時に処理対象として残り、必ず結果が返る。
- **結果返却の冪等性**:DriveClient.upload_file の契約を「同名があれば置換」とする(GoogleDriveClientはname一致のfiles().update、フェイクも同契約)。同じジョブの再試行で status.json 等が増殖しない。

### D-28:取込CLIの設計(T-111)
- 日付:2026-08-09/決定者:Claude Code(T-111)/Issue #10
- **フレームワーク**:標準ライブラリ argparse を採用(依存最小方針=D-2/D-23と一貫。サブコマンド:migrate / setup / check-config / run / status)。
- **単一ワーカー制約**:同一DATA_ROOTに対する取込ワーカーは同時1プロセスのみ。`<DATA_ROOT>/ingest.lock` へのOSレベル排他ロック(Windows: msvcrt.locking、POSIX: fcntl.flock)で保証し、二重起動は起動時にエラー。プロセス終了・クラッシュ時はOSがロックを自動解放する。
- **Ctrl+C安全停止**:T-110の全状態遷移がDBコミット済みのため、任意時点の中断が安全。KeyboardInterruptを捕捉して「再実行で再開される」旨を案内し正常終了する。
- **dry-run/statusの読み取り専用保証**:`worker.plan_inbox()`(list_files+DB照会のみ)を新設。dry-runはDrive・DBとも変更しない(ダウンロード・フォルダ作成・ジョブ作成なし。テストで検証)。
- **秘密情報の非表示**:check-configで受け箱フォルダIDは先頭4文字のみのマスク表示。OAuth情報はパスの存在確認のみ(内容を読まない・表示しない)。CLIはログファイルを作成しない(出力はコンソールのみ)。座標入力欄は設けない(setupの位置は丸め表現のみ。D-12)。
- **OAuth認可前の設定検査**:check-configはDriveへ接続せず、.env必須4項目・DATA_ROOT書込可否・FFmpeg/FFprobe・DB接続を検査する(tokenファイルは初回認可時に作成されるため未存在を許容)。
- **setupの再利用性**:Project(name)/Site(project,name)/Station(site,name)は一意制約に基づくget-or-create、SurveySessionは(station,survey_date)一致で再利用。同じコマンドの再実行が同じIDを返す(E2Eの再現性)。
- **テスト方針**:client_factory注入によりFake Driveで全コマンドを検証(実Drive・OAuth・位置情報を使わない)。Windows考慮:コンソールUTF-8化(cp932対策)、msvcrtロック、pathlib。
- 実行手順・E2Eチェックリストは docs/WINDOWS_E2E.md が正。

**追記(2026-08-09、T-111レビュー対応)**
- **dry-run・statusの完全読み取り専用化**:一覧確認モードはDBの新規作成・マイグレーションも行わない。SQLite読み取り専用接続(`file:…?mode=ro` URI。ファイルを作成しない)を使い、DB未初期化なら案内して終了する。スキーマ未適用は OperationalError を捕捉して migrate を案内。
- **排他ロックの取得順序**:非dry-runの run は「.env設定確認の直後・DB接続/マイグレーション/OAuth client生成の前」にロックを取得する。二重起動した後発プロセスは、拒否されるまでにDB・tokenへ一切触れない(テストで検証:client_factory不呼出し・DBファイル不作成)。
- **--interval の入力制約**:1以上の整数のみ受理(argparse型検証)。0・負数・非整数は引数エラーとし、API連打・実行時例外を防ぐ。

---

## 旧・判断待ち事項の決定(P-1〜P-8 → D-14〜D-21)

CodexレビューT-002の推奨に沿い、2026-08-09に決定済みへ移行。いずれも運用実績に基づく見直しは可(見直し時は本文書へ追記)。

### D-14(旧P-1):検証指標の数値目標
- 決定:初期は数値目標を設けず、実データの計測(ベンチマーク評価はReferenceObservation基準。D-11)から開始する。計測が2〜3セッション分蓄積した時点で目標値を設定する。

### D-15(旧P-2):位置情報の丸め粒度の既定値
- 決定:公開向け出力はメッシュまたは市区町村レベルを既定とする。報告書向けは用途ごとに調査責任者が指定する。希少種フラグつきデータは常に強い丸めを適用する。

### D-16(旧P-3):原データのバックアップ体制
- 決定:ローカル+外部1系統(外付けHDD/NAS等)の2重保管を標準運用とする。保管先パスは設定ファイルで管理し、ハッシュ照合で改変・欠損を検知する。

### D-17(旧P-4):解析時間の許容値
- 決定:4時間動画を一晩(実時間の2倍以内)を目安とする。原解像度解析(D-8)と両立しない場合はタイル分割・解像度選択・fps間引きのパラメータで調整し、実測後に目安を更新する。

### D-18(旧P-5):低信頼度・不明音の保存条件
- 決定:BirdNET信頼度の下限閾値を低め(Recall優先)に設定し、SED検出分(D-7)を含む全候補を保存する。ディスク逼迫時は再生成可能な DerivedAsset(D-9)のみ削除対象とし、原データ・確認済み判定は削除しない。

### D-19(旧P-6):候補動画の前後マージン既定値
- 決定:映像±10秒、音声±3秒を既定値とし、AnalysisRunパラメータとして記録・変更可能とする。確認作業のフィードバックで調整する。

### D-20(旧P-7):判定者の想定人数と再確認フロー
- 決定:1〜3名の少人数運用を想定する。「再確認必要」の最終判断は調査責任者が行う。判定者間一致率は定期的(モデル・閾値更新時を目安)に計測する。

### D-21(旧P-8):既存HTMLプロトタイプの扱い
- 決定:`bio-observer-firebase.html`・`bio-observer-research.html` は `archive/prototypes/` へ移動し、参考資料として保存する。MVP実装は本設計に従い新規に行う(Firebase前提部分は採用しない。D-2)。

---

## 判断待ち

現在、判断待ちの事項はない(P-1〜P-8はD-14〜D-21として決定済み)。
