[English](../runcontrol-hook.md) | [Korean](../ko/runcontrol-hook.md) | [Japanese](../ja/runcontrol-hook.md)

# RunControl の観測と制御

このシステムの応答は合成値であり、実機の測定値ではありません。[公式 Corryvreckan リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドします。

## オプション

公開 ITS3 RunControl は、原子的な JSON 状態の `--status-file PATH`、ローカル JSON 制御の `--control-socket PATH`、最後の設定後に終了する `--one-pass` を受け付けます。ソケット intent の `stop_next`、`repeat`、`terminate` は対応するキーボード操作と同じです。`accepted` は受付を意味し、run の停止完了を意味しません。`its3-runcontrol-status/v1` は切断された接続を `connected=false` で保持します。`run.number` は現在の run の番号です。公開 API はこの番号を提供しないため、run が `RUNNING` になったとき、状態ファイルと同じディレクトリで data collector が開いた raw ファイルの名前（`run<number>.raw`）から読み取ります。この番号は run が止まった後も、次の run が `RUNNING` になるまでそのままです。最初の run の前や、そのディレクトリに raw ファイルがない場合は `null` です。

![制御ソケットを通して要求を送る R と T のボタンがある DAQ 画面タブです。](../images/page-daq.png)

## 操作履歴

`control-history.jsonl` は状態ファイルと同じディレクトリにあり、セッションでは `/tmp/session-run/control-history.jsonl` です。`S`、`R`、`T` は押すたびに記録し、受け付けたソケット要求も記録します。拒否された要求、形式が不正な要求、`Q` は記録しません。状態ファイルがない場合は履歴を書きません。

| 項目 | 意味 |
|---|---|
| `format` | 形式のバージョン `1` |
| `seq` | 1 から始まる履歴の連番 |
| `utc` | ISO 8601 の UTC 時刻 |
| `monotonic_ns` | ナノ秒単位の単調時刻 |
| `kind` | 要求: `stop_next`、`repeat`、`terminate` |
| `source` | 出所: `keyboard`、`socket` |
| `id` | 要求の識別子 |
| `status_seq` | 状態ファイルの `last_intent` の連番 |
| `rc_state` | 要求時の RunControl 状態 |

同じ `format` ではこれらの項目の名前と意味を維持し、項目を追加する場合があります。意味を変更するときは `format` を上げます。


hook は RunControl が各 run で読み込んだ設定も raw ファイルの隣に `run<number>.conf` として保存します。設定変更と終了した producer はこのファイルになく、`scripts/session_control.sh` が `command-history.jsonl` に記録します。

## ランチャー

ランチャーは `--extra-dut`、`--rc-args`、`--rc-address`、`--producer-env`、`--no-attach` に対応します。環境変数の値ではなく名前を渡します。msensor producer は送信に成功したデータイベント数を run ごとに 0 から数え、`DataEventN` として直接報告します。BORE と EORE は数えません。検査は `tests/scripts/run_runcontrol_e2e.sh` で実行します。

受け付けた要求は run の開始中も保持され、開始後に適用されます。他の要求が保留中の場合や適用できる run がない場合は理由を付けて拒否します。キーボードとソケットは同じ処理を使います。

ランチャーはクライアントを起動する前に RunControl プロセスが指定された TCP リスナーを所有するまで待ちます。ポートが使用中、またはリスナーが準備できない場合はエラーを返します。
