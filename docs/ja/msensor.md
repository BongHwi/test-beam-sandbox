[English](../msensor.md) | [Korean](../ko/msensor.md) | [Japanese](../ja/msensor.md)

# 合成ピクセルデバイス

このシステムの応答は合成値であり、実機の測定値ではありません。[公式 Corryvreckan リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドします。

## 設定

初期化には `plane`、`columns`、`rows` が必要です。`regions` の既定値は 1 で、列数がこの値で割り切れる必要があります。`tiles_x` と `tiles_y`（既定値 1）は matrix をタイルの格子に分け、`columns` と `rows` を割り切る必要があります。タイル番号は列 0、行 0 から 0 で始まり、列方向に先に数えるため、タイル `t` はタイル列 `t mod tiles_x`、タイル行 `t div tiles_x` にあります。タイルが二つ以上ある場合、`regions` は 1 でなければならず、`threshold_dac` は整数一つ、またはタイルごとの整数です。run 設定の `tiles_off` はオフにしたタイルの一覧で、オフのタイルはヒットもノイズも出しません。タイルは隙間なく接しているものとします。これは単純化です。既定のセットアップは `6 x 2` タイル、タイルごとに `156 x 444` ピクセル、ピッチ `22.8 x 20.8` µm を使います。`clock_mode` は `virtual` または `paced` です。run には `EUDAQ_DC`、`EUDAQ_ID`、`readout_mode`、`threshold_dac` が必要です。`EUDAQ_ID` は `plane` と一致させます。`triggered` には `strobe_delay_ns` と `strobe_length_ns`、`continuous` には `stream_dir` と `window_length_ns` を指定します。不明なキーとモードに合わないキーは拒否されます。`response_file` の既定値は `response_default.json` です。

![Histograms タブの msensor DUT のヒットマップ（936 x 888 ピクセル）です。](../images/page-histograms.png)

`source=synthetic` では `seed` と trigger の指定方法をちょうど一つ `MSENSOR_SOURCE_CONFIG_JSON` に渡します。EUDAQ は設定を BORE にコピーするため、ソース条件は ini に入れません。再現可能な run では `sim_end_ns` を設定し、`source_finished=1` を確認してから停止します。`paced` の早期停止は実時間に依存します。全体の検査は `tests/scripts/run_msensor_e2e.sh` で実行します。`source=session` では `clock_mode=virtual` を設定し、`seed` を `MSENSOR_SOURCE_CONFIG_JSON` で渡して、trigger 指定と `sim_end_ns` は入れません。trigger とクロックはセッションが提供します。

## データ形式

ペイロードは ASCII `MSENSOR`、バージョン byte 1、4-byte big-endian JSON 長、正規 UTF-8 JSON で始まります。ヒットは `[col,row]` です。ストリームは ASCII `MSWINDOW` とバージョン byte 1 で始まり、各記録にはタイプ、4-byte big-endian 長、正規 JSON、CRC32 があります。FOOTER には SHA-256 があります。`python -m msensor.stream verify FILE` は正常なら 0、途中切れは 2、破損は 3 を返します。triggered は半開ゲートを使い、continuous は空の窓を含む固定窓を記録します。最後の窓は実際の終了時刻で切り詰めます。

## パラメーターの範囲とソース

`regions` の既定値は 1 です。`sim_step_ns` の既定値は 1,000,000、`pace` は 1、`deterministic_ids` は 0 です。`threshold_dac` は整数一つ、または領域ごとかタイルごとの整数で、それぞれ 0..255 です。`tiles_x` と `tiles_y` は 1..64、`tiles_off` は互いに異なるタイル番号です。`triggered` では `strobe_delay_ns` は 0..1,000,000、`strobe_length_ns` は 1..1,000,000 です。`continuous` では `window_length_ns` は 1,000..10,000,000、`window_guard_before` と `window_guard_after` は 0..8、既定値は 1 です。変換器の `msensor_window_offset` の既定値は 0、`msensor_window_span` は 1 で、要求した窓がなければエラーです。

`source=synthetic` では `MSENSOR_SOURCE_CONFIG_JSON` に 0..2^63−1 の `seed` と、`trigger_rate_hz`、`trigger_period_ns` と `trigger_first_ns`、昇順の `trigger_times_ns` のいずれか一つが必要です。rate が 0 の場合は trigger がありません。`beam_center_col`、`beam_center_row`、`beam_sigma_px`、`sim_end_ns` は合成ソースを定義します。continuous ストリームの既定名は `<producer name>_run<run number>.msws` で、既存ファイルを上書きしません。

ストリームの記録タイプは HEADER=1、WINDOW=2、EMPTY_RUN=3、TRIGGER=4、FOOTER=5 で、本文の上限は 16 MiB です。FOOTER SHA-256 は magic から直前の記録までを対象とし、ファイル全体の SHA-256 には FOOTER が含まれます。triggered ゲートは `[trigger+strobe_delay_ns, trigger+strobe_delay_ns+strobe_length_ns)`、窓 k は `[k·W,(k+1)·W)` です。切り詰めた最後の窓には不足する guard フラグを設定します。同時刻では ID 順に crossing を trigger より先に処理し、ストリームでは閉じた窓を trigger より先に記録します。
