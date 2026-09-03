# データモデル(DATA_MODEL.md)

- 文書区分:Fable管理文書(上位ルール)
- 版:1.0
- 作成日:2026-08-09
- 前提:PROJECT_CHARTER.md の原則を上位ルールとする。実装スキーマ(DDL)は本定義に従い、乖離が必要な場合は DECISIONS.md に記録する。

---

## 1. 設計原則

1. AI出力(候補・信頼度)と人の判定(Review)は**別エンティティ**に保存する。AI出力を人の判定で上書きしない。
2. 原データ(MediaAsset)は不変。派生物はすべて AnalysisRun に紐づけ、系譜を保持する。
3. AnalysisRun・Review・AccessLog は**追記のみ**(上書き・物理削除をしない)。
4. 調査地点(Site)と設置点(Station)を分離する。同一地点への複数台設置、設置方向の変更に対応する。
5. 位置情報は機微情報として、丸め済み値と正確な値を分離して保持する(SECURITY.md)。

## 2. エンティティ一覧と関係

```
Project 1─* Site 1─* Station 1─* SurveySession 1─* MediaAsset 1─* AnalysisRun
AnalysisRun 1─* VisualDetection / AudioDetection
AnalysisRun 1─* JobStep / RunEvent(実行中状態・再開情報。Run本体は完了後凍結)
AnalysisRun 1─* DerivedAsset(クリップ・WAV・スペクトログラム・サムネイル・軌跡画像・プロキシ・プレビュー画像・レポート)
DerivedAsset *─* Detection(DerivedAssetDetection 経由の多対多:1クリップに複数track、1trackが複数クリップ)
VisualDetection / AudioDetection 1─* Review
DetectionLink *─* (VisualDetection, AudioDetection)
SurveySession 1─* ReferenceObservation(精査済み評価データ)
SurveySession 1─* IngestJob 1─* IngestEvent(Drive受け箱からの取込状態。D-27)
Species, Individual, Behavior はマスター/参照系
Export, AccessLog は横断系
```

## 3. エンティティ定義

各エンティティ共通:`id`、`created_at`、`updated_at`、`note`(備考)。

### 3.1 Project(調査プロジェクト)
- name、目的、期間(開始・終了)、調査責任者、状態

### 3.2 Site(調査地点)
- project_id、名称、丸め位置(公開粒度)、環境記述(植生・地形)、機微レベル
- 営巣地等の機微属性フラグ
- **正確な位置は、T-303(アクセス制御・AccessLog)実装まではDBへ保存しない(D-12)。** DBには地点ID・丸め位置のみを保持し、正確位置は調査責任者がDB外(オフライン管理簿等)で地点IDと対応づけて管理する。T-303実装後に、権限管理・閲覧記録つきでDB保持へ移行する

### 3.3 Station(カメラ・録音機の設置点)
- site_id、名称、機材種別(カメラ/録音機/一体型)、機材モデル、設置位置(Siteからの相対でも可)、設置方向(方位・俯仰角)、画角、設置期間
- 方向・画角の**変更履歴**(変更日時つき。変更時は新Stationレコードまたは履歴行として記録し、過去の検出との対応を保つ)
- 既定の除外領域マスク(画角に紐づくため Station 単位で保持)
- 既定の解析パラメータ(画角ごとに検出特性が大きく異なるため、Station単位の既定値を保持。実際に使った値は常に AnalysisRun のスナップショットが正)
- 機器時刻オフセット(将来の複数機器同期用)

### 3.4 SurveySession(1回の調査)
- station_id、調査日、調査者、天候、気温、風、目的、現地野帳メモ
- 現地野帳記録(調査者が現地で記録した種・時刻。速報的・時刻精度は粗い。見逃しスクリーニング用の照合に使う)
- 精査済みの正解データは ReferenceObservation(3.18)として別管理する(D-11。野帳照合とベンチマーク評価の分離)

### 3.5 MediaAsset(元動画・元音声)
- survey_session_id、種別(video/audio)、ファイルパス(設定基準の相対パス)、ハッシュ(SHA-256:改変検知)、コーデック、解像度、fps、サンプルレート、チャンネル、長さ、ファイル録画日時メタデータ
- **撮影開始日時(確定値)**、算出根拠(埋め込みメタデータ/ファイル作成・更新時刻からの推定/人の入力/補正)、**確実性(確定/推定/不明)**、タイムゾーン
- 自動推定の優先順位(T-112):①動画内メタデータ creation_time → ②取込元(Drive等)の更新時刻 → ③ローカルファイル時刻。自動推定は常に「推定」、人の入力・補正のみ「確定」。採用根拠の詳細はIngestEvent・結果ファイルに記録(D-26追記)
- 原データは読み取り専用。**物理DELETEはDBが拒否し、削除は論理削除(deleted_at)のみ。原本同一性のsha256は登録後変更禁止(トリガーで拒否)。** relative_pathは保管先再編成時のみ変更可(ハッシュが同一性の根拠)

### 3.6 AnalysisRun(解析実行)
- media_asset_id、解析種別(visual/audio)、状態(待機/実行中/完了/失敗/中断)
- 使用モデル、モデルバージョン、ソースコードのコミットID、解析パラメータ(JSON)、信頼度閾値、除外領域(使用したマスクのスナップショット)、解析日時、実行環境(OS・CPU/GPU・ライブラリ版)、処理時間、エラー内容
- **再解析元となった過去解析(parent_run_id)**。過去Runの結果は上書きしない
- **完了後は凍結(イミュータブル)とする(D-10)。** 実行中の状態変化・進捗・エラー・再開情報は AnalysisRun 本体を書き換えず、JobStep/RunEvent(3.17)へ追記する。AnalysisRun の「状態」列は JobStep/RunEvent からの導出値(または完了時に一度だけ確定する値)とする

### 3.7 VisualDetection(映像検出)
- analysis_run_id、検出開始・終了時刻(タイムライン時刻とメディア内オフセットの両方)
- 画面内座標(バウンディングボックス系列)、飛翔軌跡、移動方向、移動速度、見かけの大きさ
- 羽ばたきの有無・周期、飛行様式候補(滑翔/旋回/帆翔/波状飛行等)
- 林内への出入り、止まり・飛び立ちの候補フラグ
- 候補分類(鳥/虫/葉/雲/航空機/不明 等)、**猛禽類候補度**、種候補(複数、信頼度つき)、AI信頼度、判定根拠(どの特徴が寄与したか)
- **候補区分(positive/insurance)と区分理由**:Recall優先の保険的候補(Insurance)を通常候補と区別して保持する(判定閾値未満・境界マスク接触等の理由つき)
- **生の特徴量**:主要検索項目(時刻・座標・方向・速度・大きさ・羽ばたき・候補分類・信頼度・候補区分)は固定列、Optical Flow統計等のその他の特徴量はスキーマバージョン付き構造化データ(features_json + feature_schema_version)として保持する(D-24)
- 派生物への対応:DerivedAssetDetection 経由の多対多(3.19。1クリップに複数track、1trackが複数クリップにまたがる場合に対応)
- 現在の確認状態(最新Reviewから導出。導出値であり原本はReview)

### 3.8 AudioDetection(音声検出)
- analysis_run_id、鳴声開始・終了時刻(タイムライン時刻とメディア内オフセット)
- 種候補(複数、信頼度つき)、信頼度、周波数帯、音声品質
- 背景音(風/雨/流水/車両/昆虫等)、同時に鳴いている種の候補
- 不明音フラグ、繁殖関連音声の可能性(候補として)
- 検出由来(音声イベント検出/種分類/両方。D-7。SED由来は種候補なしでも保存する)
- **SEDと種分類の検出を統合した場合も、各モデルの生スコア・モデル版・判定根拠を統合前のまま保持する**(統合結果だけを残してはならない。再現可能性と閾値再調整のため)
- 候補区分(positive/insurance)と区分理由(低信頼度の保険的保存。D-18)
- 派生物への対応:DerivedAssetDetection 経由の多対多(3.19)
- 現在の確認状態(最新Reviewから導出)

### 3.9 Review(人による確認)
- 対象(visual_detection_id または audio_detection_id)、判定者、判定日時
- 確認状態(SURVEY_METHOD.md 第3章の区分に厳密に従う)
- 人による種判定(species_id、確定粒度:種/属/科/猛禽類/鳥類、属・科の場合は分類群名 confirmed_taxon)、年齢・性別・個体識別の可否、individual_id(任意)
- 確認状態と判定内容の整合はDB制約で強制する(許容組合せは SURVEY_METHOD.md 3.2.1)
- 行動判定(behavior_id)、繁殖関連行動の判定
- 判定根拠(自由記述+選択式)、備考
- **追記のみ**。修正は新しいReview行として記録し、履歴を保持する

### 3.10 Species(種マスター)
- 和名、学名、英名、分類(目・科・属)、猛禽類フラグ、希少種フラグ(機微出力制御に使用)、外部コード(BirdNETラベル等との対応)

### 3.11 Individual(識別可能な個体)
- species_id、識別名、識別特徴(換羽・欠損・標識等)、初認日、備考
- 個体識別は常に人の判定として記録(自動確定しない)

### 3.12 Behavior(行動分類)
- 分類コード、名称(飛翔/帆翔/旋回/止まり/飛び立ち/林内出入り/ディスプレイ飛翔/巣材運搬/餌運搬/交尾/餌渡し 等)、繁殖関連フラグ、定義文

### 3.13 DetectionLink(関連候補)
- 対象検出のペア(映像—音声、映像—映像等)、関連種別(時間近接/同方向 等)、提示根拠、AI提示か人の確定か、確定した場合の判定者・日時
- **自動確定禁止**:AIが作るのは「関連候補」まで。同一個体・同一事象の確定は人の判定

### 3.14 Export(報告用出力)
- 実行者、日時、出力形式(CSV等)、対象範囲、適用した位置丸め粒度、出力ファイルのハッシュ、用途

### 3.15 AccessLog(機微情報の閲覧履歴)
- 利用者、日時、操作(閲覧/出力/変更)、対象(Site正確位置、営巣地関連データ等)、結果
- 追記のみ・改変不可

### 3.16 DerivedAsset(派生物)
- 種別(候補動画クリップ/抽出WAV/音声クリップ/スペクトログラム/サムネイル/軌跡画像/プロキシ/**プレビュー画像(マスク・閾値確認用)/レポート(集計CSV等)** 等)
- 系譜:media_asset_id(元の原データ)、analysis_run_id(生成したRun)。**Runと元データの組合せ整合はトリガーで強制**(別動画のRunとの誤接続を拒否)。検出との対応は DerivedAssetDetection(3.19)で多対多に保持
- ファイルパス(設定基準の相対パス)、ハッシュ(SHA-256。**実体が存在する状態=presentではハッシュ必須**、生成途中はregenerating等)、サイズ、形式
- 生成条件(使用ツール・パラメータ:前後マージン、解像度、コーデック等のスナップショット)
- 再生成可否フラグ、再生成状態(存在/削除済み・再生成可/再生成中/再生成失敗)
- ディスク逼迫時は再生成可能な DerivedAsset のみ削除対象とできる(原データ・確認済み判定は削除しない)

### 3.17 JobStep / RunEvent(解析実行の進捗・イベント)
- analysis_run_id、ステップ名(パイプライン段階に対応)、順序
- 状態(待機/実行中/完了/失敗/スキップ)、開始・終了日時、処理範囲(再開用オフセット:処理済み時間位置・フレーム位置等)、エラー内容、リトライ回数
- RunEvent:Run実行中の状態変化・警告・環境イベントの追記ログ(追記のみ)
- **AnalysisRun 本体は完了後凍結し、実行中の状態変化と再開情報はすべて本エンティティへ追記する(D-10)**

### 3.18 ReferenceObservation(精査済み評価データ)
- survey_session_id、種(species_id)、開始・終了時刻(タイムライン時刻。精査済みの高精度)、個体数、行動、距離帯、記録方法(目視/聴取/映像精査/音声精査)、根拠(参照した記録・メディア)
- **精査者、精査方法、精査日時、確信度(精査者の確からしさ評価)、二重確認の有無(第二精査者・日時)**
- 現地野帳記録(SurveySession保持。速報的・粗い)とは別エンティティとする(D-11)
- 用途:**ベンチマーク評価の正解データ**。検出率・見逃し率等の定量指標(SURVEY_METHOD.md 第5章)は ReferenceObservation に対して算出する
- 野帳照合は見逃しスクリーニング(粗い突合)、ReferenceObservation照合は定量評価と役割を分ける

### 3.19 DerivedAssetDetection(派生物と検出の対応)
- derived_asset_id、対応する検出(visual_detection_id または audio_detection_id のどちらか一方)、役割(primary=その派生物の主対象/member=含まれる)
- 動体Trackと抽出クリップの**多対多**関係を表す:1つのクリップに複数のtrackが含まれる、1つのtrackが複数のクリップにまたがる、の両方に対応
- 同一の(派生物, 検出)組の重複登録は一意制約で拒否
- **対応づける検出は、その派生物を生成したAnalysisRunの検出に限る**(別Runの検出との誤接続はトリガーで拒否。再現性の保護)

### 3.20 IngestJob(Drive受け箱からの取込ジョブ。D-27)
- source(google_drive/local)、drive_file_id(source内で一意=同じFile IDを二重取込しない)、**original_file_name(Drive上の表示名。本エンティティでのみ保持し、MediaAsset・保存パスへ露出させない。D-26)**、mime_type、size_bytes、modified_time
- stable_probe_json(アップロード完了判定用の直近観測:サイズ・modifiedTime・連続確認回数)
- survey_session_id、media_asset_id(登録成功時)、duplicate_of_media_asset_id(同一ハッシュ既登録時の参照=二重解析防止)
- status(discovered/waiting_for_upload/downloading/downloaded/registered/queued/analyzing/uploading_results/completed/failed/retry_required)、resume_status(再試行時の復帰先)、retry_count、error
- results_folder_name(Drive結果フォルダ名=job id。表示用フォルダ名と不透明IDを混同しない)

### 3.21 IngestEvent(取込状態変化の追記ログ)
- ingest_job_id、occurred_at、from_status、to_status、message、detail_json
- **追記のみ**(状態は上書きだけでなく必ず本ログへ履歴として残す)

## 4. 状態遷移(検出候補の確認状態)

原本は Review の追記列。検出エンティティ上の「現在の確認状態」は最新Reviewからの導出値とする。

```
未解析 → 解析中 → AI候補 → 未確認 →(人の確認)→ 確認済み系(種確定/属・科まで確認/猛禽類まで確認/鳥類まで確認/種不明/誤検出/判定不能)
                                        └→ 再確認必要 →(再確認)→ 確認済み系
```

区分の定義は SURVEY_METHOD.md 第3章を正とする。

## 5. 実装上の注意(Claude Code向け)

- スキーマはSQLiteで開始するが、PostgreSQL移行を阻害する型・機能に依存しない
- 時刻はUTCで保存し、表示時にローカルタイムゾーンへ変換。タイムゾーンを必ず保持
- ファイルは相対パス+設定ファイルのルートで解決(リポジトリに絶対パス・座標を含めない)
- マイグレーションは追記的に管理し、Codexがレビューする
