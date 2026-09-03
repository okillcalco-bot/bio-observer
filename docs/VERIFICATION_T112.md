# T-112 検証記録:撮影開始時刻の根拠優先順位(IMG_3355.MOV / IMG_3356.MOV)

- 対象:Google Drive受け箱の短尺テスト動画2本
- 方針:**既存のcompletedレコード(取込済みIngestJob・MediaAsset)は変更しない。** 本記録は読み取り専用の観測と、登録を伴わない `bio-observer inspect-time` による評価で構成する
- 秘密情報の扱い:Drive File ID・フォルダIDは先頭4文字のみ記載

## 1. Drive側メタデータ(Drive API読み取り、2026-08-09 実測)

| 項目 | IMG_3355.MOV | IMG_3356.MOV |
|---|---|---|
| Drive File ID(マスク) | 1DcK… | 1F-Z… |
| サイズ | 14,776,146 B | 14,806,303 B |
| MIME | video/quicktime | video/quicktime |
| **modifiedTime(候補②に使用)** | 2026-08-09T11:35:51Z | 2026-08-09T11:35:45Z |
| createdTime(アップロード時刻。**候補として使用しない**) | 2026-08-09T11:36:29.974Z | 2026-08-09T11:36:33.566Z |

観察:modifiedTime は createdTime(アップロード)より約40〜50秒前で、端末側のファイル時刻(撮影終了・保存時刻に近い値)を反映していると考えられる。createdTime は撮影時刻の根拠にならないため採用対象から除外している(D-26追記)。

## 2. 動画内メタデータ・ローカル時刻・新ロジックの採用値(実機で実測)

本リモート環境では動画本体を取得できない(約15MB×2のバイナリ取得が実用外)ため、以下はWindows解析PCで `bio-observer inspect-time` を実行して記入する。

```powershell
bio-observer inspect-time "<ローカルのIMG_3355.MOV>" --origin-modified-time 2026-08-09T11:35:51Z
bio-observer inspect-time "<ローカルのIMG_3356.MOV>" --origin-modified-time 2026-08-09T11:35:45Z
```

| 項目 | IMG_3355.MOV | IMG_3356.MOV |
|---|---|---|
| 動画内 creation_time(raw値 / タグ所在) | | |
| creation_time の timezone(explicit / timezone_unknown) | | |
| Drive modifiedTime(候補②) | 2026-08-09T11:35:51Z | 2026-08-09T11:35:45Z |
| ローカル mtime(候補③。コピーした時刻になる) | | |
| **新ロジックの採用値** | | |
| **recording_start_source** | | |
| 実撮影時刻との差(端末の撮影記録と比較できる場合) | | |

期待値(iPhone撮影のMOVの一般的な特性に基づく事前予測。実測で確認する):format tags に `creation_time`(UTC・Z表記)と `com.apple.quicktime.creationdate`(+09:00 オフセット付き)があり、いずれも timezone=explicit のため候補①が採用される見込み。creation_time が存在しない・表記なしで解釈条件未設定の場合は候補②(Drive modifiedTime)へフォールバックし、その理由が候補記録に残る。

## 3. 記録の残し方

- 実測後、本表を埋めてPR #13(またはマージ後の追記コミット)へ反映する
- T-112マージ前後の比較:マージ前のE2E(main 89b8050)では `recording_start_source=local_file_mtime`(ダウンロード時刻に近い値)、マージ後は上記の採用値になることを確認する。**既存の取込結果は再計算・上書きしない**(比較は新規取込または inspect-time で行う)
