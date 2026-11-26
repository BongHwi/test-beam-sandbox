[English](../walkthrough.md) | [Korean](../ko/walkthrough.md) | [Japanese](../ja/walkthrough.md)

# ウォークスルー

このガイドは、ビルドからコンテナーの削除まで、ターミナルの EUDAQ、ターミナルの nestDAQ、またはブラウザでセッションを進めます。README の[クイックスタート](../../README.ja.md#クイックスタート)が最短の方法です。

## イメージのビルド

コードを取得し、イメージを一度ビルドします。コマンドはすべてリポジトリの最上位ディレクトリで実行します:

```sh
# コードを取得します。
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# 公開ベースイメージとセッションイメージをビルドします（一度だけ）。
scripts/build_session.sh

# nestDAQ モードでは nestDAQ イメージもビルドします（一度だけ）。
scripts/build_session.sh --nestdaq
```

最初のビルドは EUDAQ と Corryvreckan をコンパイルするため時間がかかります。24 コアの arm64 コンピューターでは約 4 分、nestDAQ イメージにはさらに約 10 分かかりました。以後のセッションはイメージを再利用します。

次にターミナルまたはブラウザで EUDAQ か nestDAQ の方法を使います。すべての方法は `public-session` という一つのセッションコンテナーを使い、ブラウザの各ボタンはターミナルと同じコマンドを実行するため、二つの方法を組み合わせられます。

#### ターミナルで EUDAQ セッション

##### 1. 取得

既定のセットアップでセッションを開始します。他のセットアップは `configs/setups/` にあり、[セットアップファイルの説明](setup-file.md)を参照します。RunControl、producer、data collector と模擬ビームは `public-session` という一つのコンテナーで動き、このターミナルに公開 RunControl 画面が表示されます。

```sh
# セッションを開始し、公開 RunControl 画面を表示します。
scripts/start_session.sh --setup configs/setups/default.ini --name public-session
```

セッションはビームがオフの状態で始まります。二つ目のターミナルで模擬ビームをオンにすると、RunControl 画面のイベント数が増え始めます。

```sh
# 模擬ビームをオンにします。
scripts/session_control.sh public-session on
```

run が終わると、`T` で一連の処理を終了するまで RunControl は次の run を開始します。何も止めずに画面を離れるには `Ctrl-b`、次に `d` を押します。`scripts/attach_session.sh public-session` で画面を再表示できます。

![セッションの公開 RunControl 画面: run の進行状況、producer と collector](../images/runcontrol-screen.png)

実行中のセッションの RunControl 画面です。セッションは [EUDAQ の ITS3 RunControl](https://github.com/eudaq/eudaq/blob/bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e/user/ITS3/python/ITS3RunControl.py) を使います。ウィンドウのキーは[操作画面ガイド](operator-screen.md)にあります。

##### 2. 解析

セットアップファイルの位置は測量した値です。実際の位置は設置後のように最大 `5` mm ずれていて隠されているため、QA が Corryvreckan でトラックを再構成して DUT の効率を測る前に、アライメントがデータから位置を求めます。その間も取得は続きます（[アライメントガイド](alignment.md)）。アライメントは現在の run のデータイベントが `3000` に達するまで待ちます。

```sh
# 現在の run のデータからデバイスの位置を求めます。
scripts/align_session.sh public-session

# 現在の run を解析します: イベント、トラック、DUT の効率。
scripts/analyze_session.sh public-session
```

QA は次のような行で終わります（数値は変わります）:

```text
  Events: Ev: 10.1k Px: 113.3k Tr: 7.1k (0.703/ev) t = 47.6141s
  [F:AnalysisEfficiency:MSENSOR_6] Total efficiency of detector MSENSOR_6: 98.4446(+0.149964 -0.164889)%, measured with 6709/6815 matched/total tracks
```

アライメントは求めた補正量と、アライメントの前後のイベントあたりのトラック数を表示します。QA はイベント数、トラック数、DUT ごとの効率を表示します。最初の行はコンテナー内の結果ディレクトリを示し、その中の `analysis/output/histograms.root` にヒストグラムがあります。どちらも使った Corryvreckan の設定を示します（[操作画面ガイド](operator-screen.md)）。DUT は DAQ では producer 名に従って `MSENSOR_0`、解析では plane 番号に従って `MSENSOR_6` と呼ばれます。

##### 3. 終了

RunControl 画面で `T` を押して一連の処理を終え、次に `Q` を押して RunControl を抜けます。画面にはコンテナーの shell が表示され、`Ctrl-b`、次に `d` を押すと離れます。別のターミナルから `scripts/session_control.sh public-session terminate` を実行すると `T` と同じ要求を送ります。画面の再描画中に押したキーが公開 RunControl をフリーズさせることがあるため、プログラムはこのコマンドを使うべきです（[操作画面ガイド](operator-screen.md)）。

```sh
# 出力をホストにコピーします。
docker cp public-session:/tmp/session-run ./session-output

# セッションのコンテナーとその中のすべてを削除します。
docker rm -f public-session
```

ウィンドウを移るキー、`S` と `R`、切り離しと再接続、ビームの履歴は[操作画面ガイド](operator-screen.md)に、run の長さは[セットアップファイルの説明](setup-file.md)にあります。

#### ターミナルで nestDAQ セッション

##### 1. 取得

nestDAQ のセットアップでセッションを開始すると、セットアップファイルから nestDAQ イメージが選ばれます。nestDAQ プロセスはコンテナーのバックグラウンドで動くため、同じターミナルでビームをオンにして run を開始できます。`scripts/attach_session.sh public-session` は msensor 実行器を表示し、`Ctrl-b`、次に `d` を押すと画面を離れます。

```sh
# 画面を表示せずに nestDAQ セッションを開始します。
scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach

# 模擬ビームをオンにします。
scripts/session_control.sh public-session on

# run を開始します。応答は run 番号を含む JSON です。
scripts/session_control.sh public-session nestdaq start
```

仮想 trigger ボードを加えるには、`configs/setups/nestdaq-board.ini` から始めるか、`[nestdaq]` に `trigger_board = on` と `periodic_clk` を設定します。すると、すべての msensor が 100.5 MHz のボード時計で `periodic_clk` サイクルごとに周期 trigger を受け取り、`TRIGGERBOARD_0` 実行器が周期 trigger と物理 trigger をそれぞれのシンチレーターパターンとともに専用の stream に保存します。この設定がなければ、セッションはこれまでどおり動きます。

##### 2. 監視

状態は run、デバイス、msensor ごとに送った frame と破棄された DQM コピーを表示します。ページの nestDAQ タブはこれらと実行器ごとに書いた frame を表示し、daq-webctl、SlowDash、DQM へのリンクを示します。`scripts/qa_page.sh public-session` で開きます。`nestdaq qa` は DQM の数値を表示し、ページの QA タブにも同じ数値が表示されます。DQM の hit map と trigger の対応づけは [nestDAQ モード](nestdaq.md)に説明されています。

```sh
# run、デバイスと msensor stream を表示します。
scripts/session_control.sh public-session nestdaq status --json

# DQM の frame、trigger、hit、trigger の対応づけの数値を表示します。
scripts/session_control.sh public-session nestdaq qa
```

![結果ページの nestDAQ タブ: run 制御、デバイス、msensor stream と画面リンク](../images/page-nestdaq.png)

##### 3. 終了

run を止めると、残りのデータは run が止まる前に保存され、次の `nestdaq start` は新しい run を開始します。出力をコピーしてコンテナーを削除し、このコンピューターで各 msensor と trigger ボードの msraw stream を抽出します。trigger ボードを使った場合は時間 frame も作ります。各ツールは JSON の要約を表示します。

```sh
# run を止めます。
scripts/session_control.sh public-session nestdaq stop

# 出力をホストにコピーします。
docker cp public-session:/tmp/session-run ./session-output

# セッションのコンテナーとその中のすべてを削除します。
docker rm -f public-session

# run 1 の各 msensor と trigger ボードの msraw stream を抽出します。
python3 -m nestdaq_mode.extract session-output/nestdaq/data/run000001.dat extracted

# trigger ボードを使った場合: 周期 trigger ごとの時間 frame を作ります。frame ごとに 1 行の JSON です。
python3 -m nestdaq_mode.framebuilder --dir extracted --out frames.jsonl
```

`terminate` と `T`、`Q` キーは EUDAQ RunControl に属し、nestDAQ セッションは `terminate` を拒否します。セットアップ項目、run、画面、データ、制約は [nestDAQ モード](nestdaq.md)にあります。

#### ブラウザで

```sh
# ページサーバーを起動し、結果ページを開きます（セッションはまだありません）。
scripts/qa_page.sh
```

EUDAQ セッション:

1. セットアップタブで `default.ini` を選び、"セッションを開始" を押します（`scripts/start_session.sh --setup configs/setups/default.ini --name public-session --no-attach`）。
2. 実験タブで "ビームをオン" を押すと（`scripts/session_control.sh public-session on --id page-on`）、イベント数が増え始めます。
3. QA タブで "アライメント"（`scripts/align_session.sh public-session`）、次に "QA を実行"（`scripts/analyze_session.sh public-session`）を押します。
4. 実験タブで "データ取得を終了"（`scripts/session_control.sh public-session terminate --id page-terminate`）、次に "RunControl を終了 (Q)"（`docker exec public-session tmux send-keys -t ITS3:rc Q`）を押します。
5. 同じカードで "出力をコピー"（`docker cp public-session:/tmp/session-run outputs/public-session-UTCTIME`）、次に "コンテナーを削除"（`docker rm -f public-session`）を押します。DAQ 画面タブでも同じ手順を実行できます。
nestDAQ セッション:

1. セットアップタブで `nestdaq.ini` を選び（trigger ボードを使う場合は `nestdaq-board.ini`）、"セッションを開始" を押します（`scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach`）。
2. 実験タブで "ビームをオン" を押します（`scripts/session_control.sh public-session on --id page-on`）。
3. nestDAQ タブで "Start" を押します（`scripts/session_control.sh public-session nestdaq start`）。画面リンクは daq-webctl、SlowDash、DQM を開きます。run を終えるには "Stop" を押します（`scripts/session_control.sh public-session nestdaq stop`）。
4. 実験タブで EUDAQ セッションと同じように "出力をコピー"、次に "コンテナーを削除" を押します。

`scripts/qa_page.sh --stop` でページサーバーを停止します。ターミナルで開始したセッションを見るには `scripts/qa_page.sh public-session` を実行します。各タブは[結果ページガイド](qa-page.md)に説明されています。
