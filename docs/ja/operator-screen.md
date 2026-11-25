[English](../operator-screen.md) | [Korean](../ko/operator-screen.md) | [Japanese](operator-screen.md)

# オペレーター画面

セッションはビームがオフの状態で始まります。`scripts/start_session.sh`で起動するか、`--no-attach`で起動してから`scripts/attach_session.sh`で引き継ぎます。画面から離れてもセッションは続きます。

![run 中の操作画面の RunControl ウィンドウです。](../images/runcontrol-screen.png)

## ウィンドウと操作

公開 launcher は `rc`（RunControl）、`rp`（基準面 producer）、`dp`（DUT producer）、`sp`（補助プロセス）、`dc`（collector）、`log`（ログ出力）、`perf`（性能出力）のウィンドウを作成します。`log` ウィンドウでは公開 EUDAQ の log collector が動き、RunControl、producer、collector のログ行が表示されます。run の開始ごとに `RUN #NUMBER is started` の行が表示され、同じ内容は `EUDAQLog*.log` ファイルに保存され、このファイルは `/tmp/session-run` にあります。`perf` ウィンドウでは `htop` が動きます。`Ctrl-b n`と`Ctrl-b p`で移動し、`Ctrl-b 0`で RunControl に戻り、`Ctrl-b d`で画面から離れます。RunControl 画面には各プロセスの接続と状態、現在の設定、イベントの進行状況が表示されます。producer や collector の行のイベント数は、数秒間変わらずにその後まとめて増えることがあります。公開 EUDAQ がこの状態を不規則な間隔で伝えるためで、その間もデータは記録され続けます。データが届いているかを確かめるには、下の解析を再度実行して完全なイベント数を比べます。

`S` はこの run だけを停止して次の run をすぐに開始し、終了するには `T` の後に `Q` を送ります。

RunControl では `S` で現在の run を停止して次の設定に進み、`R` で現在の設定を繰り返し、`T` で一連の処理を終了し、終了後に `Q` で画面を閉じます。公開 RunControl のキーをそのまま使います。

画面が状態の行を描き直す瞬間にキーを押すと、公開 RunControl が完全に止まることがあります。画面が変わらなくなり、状態ファイルと raw ファイルも書かれなくなり、状態ファイルの `updated_monotonic_ns` が増えず、制御 socket への要求に応答がなくなります。その場合は `docker rm -f public-session` でセッションを削除し、起動し直します。`scripts/session_control.sh public-session stop`、`repeat`、`terminate` のコマンドは画面を経由しないため、この影響を受けません。


nestDAQ セッションでは `NESTDAQ:runners` に接続します。七つのウィンドウは `redis`、`webctl`、`slowdash`、`runners`、`sources`、`sink`、`dqm` です。[nestDAQ モード](nestdaq.md)を参照してください。

## ビームと解析

別のターミナルで `scripts/session_control.sh public-session on`、`off`、`beam-status`を使います。各コマンドは直近の変更五件を UTC の時刻とともに表示し、コンテナー内の全履歴のパスを示します。`on` または `off` が受け付けられてビームの状態が変わった場合、一覧の最後の行がその変更です。プログラム用の応答には `--json` を付けます。ビームは run の切り替えに関係なく維持されます。

`scripts/session_control.sh public-session stop`、`repeat`、`terminate` は RunControl の要求 `stop_next`、`repeat`、`terminate` を送ります。画面で `S`、`R`、`T` を押すのと同じです。各コマンドは RunControl の JSON 応答を表示し、要求が受け付けられると終了コード 0、そうでなければ 1 で終わります。`--id ID` は要求の id を決め、応答と `/tmp/session-run/status.json` の `last_intent` にその id がそのまま現れます。指定しない場合の id は `operator-stop`、`operator-repeat`、`operator-terminate` です。id は英字、数字、`.`、`-`、`_` からなる 1–64 文字です。それ以外の id は送らず、終了コード 2 で終わります。同じ `--id ID` を `on` と `off` にも使えます。

`scripts/analyze_session.sh public-session`で現在の run のその時点までを解析します。繰り返すと後のデータを解析でき、`--run NUMBER`を付けると終了した run を選べます。結果はコンテナー内の別々のディレクトリに保存されます。コマンド出力の最後に、公開解析がログに書いた値をそのまま表示します。スナップショットの完全なイベント数、最後のイベント進行行、トラックの行、DUT ごとの `Total efficiency of detector ...` の行です。解析後に一時的な raw のコピーは削除し、`snapshot.json` と結果は残します。`scripts/analyze_session.sh public-session --list` は raw ファイルの run 番号を古い順に表示します。run の開始時には `log` ウィンドウにも run 番号が表示されます。解析中も収集は続きます。制限と復旧方法は[セッションソースとデバイス喪失](session-core.md)を参照してください。

QA とアライメントは、それぞれ `Corryvreckan configuration: PATH (in the container)` という行を一行表示します。QA の最初の行はコンテナー内の結果ディレクトリを示し、そこの `analysis/output/histograms.root` にヒストグラムがあります。`docker cp` でコピーして ROOT で開きます。実際のテストビームで解析した後と同じです。アライメントの設定ファイルは `/tmp/session-run/alignment/corryvreckan/` に残り、アライメントが実行した段階を示します。それらが読むスナップショットはアライメントが終わると削除されます。[公式 Corryvreckan リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドします。

## 次の run の設定

公開 RunControl は run の前に毎回設定ファイルを読み直します。`--setup` で開始したセッションでは、`scripts/session_control.sh public-session set` が次の run の設定を一つ変更し、現在の run は従来の設定を使います。変更できるのは `events`（`[run]`）、ALPIDE plane の `ithr` と `strobe_length`、msensor の `threshold_dac`、`readout`、`window_length_ns`、`strobe_delay_ns`、`strobe_length_ns` です。名前と値はセットアップファイルと同じで同じ規則で検査し、他の項目はセッション開始時のままです。

```sh
# 次の run の三番目の基準面の threshold を設定します。
scripts/session_control.sh public-session set alpide 2 ithr 60

# 現在の run と次の run の設定を表示します。
scripts/session_control.sh public-session settings
```

変更は現在の run が設定されたイベント数に達したときか、`stop` (`S`) または `repeat` (`R`) の後に RunControl が次の run を設定するときに適用されます。各 run は読み込んだ設定を raw ファイルの隣の `run<number>.conf` に残し、QA はセットアップファイルと異なる設定を示し、各変更はセッションディレクトリの `command-history.jsonl` に一行で記録されます。テストビームでも担当者は run の間に同じように設定ファイルを編集します。

`[trigger]` の `trg`、`veto`、`count` も次の run から変更できます:

```sh
scripts/session_control.sh public-session set trigger count "1xx1 xxx1"
```

## producer が応答を止めたとき

producer が接続されたまま応答を止めると、RunControl 画面ではそのイベント数が止まり、他の数は増え続け、状態は `RUNNING` のままになることがあります。data collector はその producer を待ちます。

テストビームでは担当者がそのウィンドウで producer を終了します。基準面は `rp` ウィンドウ、DUT producer は `dp` にあり、各 pane に producer のタイトルとプロセス番号が表示されます。その pane で Ctrl-C を押します。Ctrl-C に反応しない producer は次のコマンドで終了します。

```sh
# 停止した producer ALPIDE_plane_2 を終了します: SIGTERM、5 s 後に SIGKILL。
scripts/session_control.sh public-session end-producer ALPIDE_plane_2
```

コマンドは RunControl 画面に表示される producer 名を受け取り、操作を `command-history.jsonl` に記録します。その後 RunControl は producer が切断されたことを示します。

ある試験では残りの producer と collector は続き、`stop` 要求は受け付けられましたが、完了しませんでした。終了した producer は pane で再起動すると再接続しましたが、`UNINIT` のままで次の run には参加しませんでした。すべてのデバイスを再び使うには、`docker cp` で出力を保存し、新しいセッションを開始します。

## run 内の時間制の停滞

session source に 10 秒応答しない producer はその run から除外され、戻れません（[session source とデバイス喪失](session-core.md)）。したがって同じ run で続く停滞は、producer が応答し続ける場合だけ可能です。一時的な停滞の練習用に、`stall` は msensor を 1 台、接続と `RUNNING` 状態、ビームをそのままに、指定した時間だけ event を作らせません。

```sh
# MSENSOR_0 は現在の run で 90 秒間 event を作らず、同じ run で続けます。
scripts/session_control.sh public-session stall MSENSOR_0 90 --id practice-stall

# 停滞を早く終えます。
scripts/session_control.sh public-session stall MSENSOR_0 cancel
```

`SECONDS` は 0 より大きく 120 以下の数です。対象は実行中の run で接続され `RUNNING` の msensor（`MSENSOR_N`）で、ALPIDE プレーンとトリガーボードは対象外です。停滞中はその producer の data event 数と `run.events` は増えず、他の producer は数え続け、producer の状態項目 `stalled` は `1` です。停滞中の event は捨てず、順に保持して停滞後に送るため、その後は数が急に増え、run の raw ファイルは 1 つの run として閉じます。stop 要求、run の終了、`cancel` が停滞を終え、停滞は次の run に持ち越されません。120 秒の制限は、source が最も遅い参加者より先行できる上限（200 秒）に収めるためです。collector は遅い読み出しと同様に停滞した producer を待ちます。上限に近い停滞は公開イメージで最初から最後まで実行していません。

コマンドは停滞が始まれば `0`、始まらなければ（実行中でない、すでに停滞中、5 秒以内に応答なし）`1`、不正な引数に `2` を返します。操作は `command-history.jsonl` に種類 `stall`、producer、run、秒、id（`--id` がなければ `operator-stall`）、producer の開始時刻 `started_monotonic_ns` とともに 1 行で残ります。終了はそこではなく `/tmp/session-run/stall/PRODUCER.state.json`（`state` が `ended`、`reason` が `elapsed`、`cancelled` または `run_ended`）にあります。

## QA の結果ファイル

QA は実行のたびにセッションのコンテナーの `/tmp/session-run/qa/latest.json`（最後の QA）を書き、`status` が `ok` なら `/tmp/session-run/qa/history.jsonl` に一行を加えます。次の項目が保持されます:

- `format`: `1`、これらの項目の版番号です。同じ `format` の中では下の項目の名前と意味は変わりません。項目が加わることはあります。意味を変えるときは `format` を上げます。
- `status`: `ok`（解析が終わった）、`not_aligned`（セットアップに位置のずれがあり、セッションにアライメントがないため解析の前に止まった）、`failed`（解析がエラーで終わった）。
- `started_utc`、`finished_utc`: QA の開始と終了、UTC の ISO 8601 時刻。
- `id`: `qa/` の下の結果ディレクトリ（有限の例は `example-...`）。
- `run`: 解析した run の raw ファイル名。
- `data_events`: 解析した完全なデータイベントの数（有限の例は最後の解析進行行の数）。
- `tracks`: 再構成したトラックの数。解析が報告しない場合は `null`。
- `efficiency`: DUT ごとに一つ、`dut`（解析名）、`percent`（%）、`plus` と `minus`（上下の誤差、%）、`matched` と `total`（ヒットが結び付いたトラックの数と数えたトラックの数）。DUT がなければ空です。
- `alignment.id`: 使ったアライメント。アライメントがなければ `alignment` は `null` です。
- `position_warning`: アライメントの後にデバイスが動いたようだと QA が判断したときだけあります。

`not_aligned` のとき `latest.json` にはこれらの項目のうち `format`、`status`、`started_utc`、`finished_utc`、`alignment`（`null`）だけがあります。`ok` と `failed` のときはすべてあり、`position_warning` は該当するときだけです。`failed` のときは `tracks` が `null` で `efficiency` が空のことがあります。`history.jsonl` の各行には `format`、`id`、`run`、`data_events`、`tracks`、`efficiency`、`started_utc`、`finished_utc` があります。二つのファイルのほかの項目は変わることがあります。QA コマンドはこれまでと同じ行を表示し、結果ページはこれらのファイルを読みます。

## Corryvreckan を自分で実行する

QA の設定は完全なイベントからなる run のスナップショットを読み、QA は解析の後にそれを削除します。終了した run の解析（`scripts/analyze_session.sh public-session --run NUMBER`）を Corryvreckan で直接繰り返すには、スナップショットを作り直し、新しいディレクトリでその設定を使って `corry` を実行します。`NUMBER` は run 番号に、`RESULT` は QA が表示した結果ディレクトリに置き換えます。

```sh
# 設定が読むパスに、終了した run のスナップショットを作り直します。
docker exec public-session python -m session_setup.snapshot /tmp/session-run/runNUMBER.raw RESULT/snapshot.raw

# その QA の設定で、新しいディレクトリで Corryvreckan を実行します。
docker exec public-session mkdir -p /tmp/corry-again
docker exec -w /tmp/corry-again public-session corry -c RESULT/analysis/corry.conf
```

`corry` はその run の QA と同じトラックと効率の行を表示します。
