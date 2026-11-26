[English](../daq-modes.md) | [Korean](../ko/daq-modes.md) | [Japanese](../ja/daq-modes.md)

# DAQ モード

セッションはセットアップファイルで選んだ二つの公開データ取得フレームワークの一つでデータを取得します:
- EUDAQ（`[run] daq = eudaq`、既定）は公開 EUDAQ で仮想 ALPIDE 基準面、msensor、仮想 trigger ボードを読みます。ALPIDE producer、trigger producer、data collector はそのまま使い、RunControl はこのリポジトリの状態・制御の拡張とともに動きます。[公式リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドした公開解析ソフトウェア Corryvreckan が面をアライメントし、粒子のトラックを再構成して DUT がそれをどの程度効率よく検出するかを測定します。
- nestDAQ（`[run] daq = nestdaq`）は nestDAQ デバイスですべての msensor を continuous または triggered stream として読みます。msensor ごとの入力デバイス、frame を保存する FileSink、hit map を埋めて msensor の trigger を対応づけるモニター（DQM）を使います。daq-webctl は run を制御し、SlowDash は時系列を表示します。仮想 trigger ボード（`trigger_board = on`、任意）は、すべての msensor に周期 trigger を送って時間 frame の境界を示し、ビームラインの物理 trigger を専用のボード stream に記録します。

## 比較

両モードは同じ模擬ビーム、セットアップファイル、スクリプト、結果ページを使います。セットアップファイルでモードを選びます:

| | EUDAQ モード | nestDAQ モード |
|---|---|---|
| セットアップファイル | `[run] daq = eudaq`（既定） | `[run] daq = nestdaq` |
| イメージ | `scripts/build_session.sh` | さらに `scripts/build_session.sh --nestdaq` |
| `configs/setups/` のセットアップ例 | `default.ini`, `small-msensor.ini`, `msensor-telescope.ini` | `nestdaq.ini`, `nestdaq-clocks.ini`, `nestdaq-missed.ini`, `nestdaq-telescope.ini`, `nestdaq-board.ini` |
| 読むデバイス | ALPIDE 基準面、msensor、trigger ボード | msensor; ALPIDE 面はビーム中の物質として残ります |
| Trigger | シンチレーターとセットアップファイルの trigger 条件 | 模擬ビームラインの trigger; `trigger_board = on` ではシンチレーターと `[trigger]` を使え、周期 trigger を出す仮想 trigger ボードが加わります |
| Run 制御 | 公開 RunControl 画面と `scripts/session_control.sh` | daq-webctl と `scripts/session_control.sh NAME nestdaq` |
| 監視 | RunControl 画面; ページの実験と DAQ 画面タブ | DQM と SlowDash; ページの nestDAQ と QA タブ |
| 保存データ | EUDAQ raw ファイル | FileSink ファイルの msraw frame; `python3 -m nestdaq_mode.extract` が msensor ごと、および trigger ボードの msraw stream を別ファイルに書きます |
| 解析 | Corryvreckan によるアライメントと QA | アライメントはまだありません; `python3 -m nestdaq_mode.framebuilder` が周期 trigger ごとの時間 frame をオフラインで作ります; DQM は hit map、correlation、trigger の対応づけを示し、`nestdaq qa` と QA タブはその数値を表示します |

## 実際のテストビームでは

| 段階 | サンドボックスでは | 実際のテストビームでは |
|---|---|---|
| セットアップ | セットアップファイル一つ | デバイスを設置し、EUDAQ の設定ファイルを用意します |
| 取得 | `scripts/start_session.sh` が公開 RunControl 画面を表示します | RunControl と producer を起動し、その画面で操作します |
| nestDAQ での取得 | `scripts/session_control.sh NAME nestdaq start` と `stop`、または daq-webctl | nestDAQ デバイスを起動し、daq-webctl で run を制御します |
| ビーム | `scripts/session_control.sh NAME on`、`off` | 加速器から来ます |
| Trigger | セットアップファイルのシンチレーターと条件、仮想ボードと公開 trigger producer | trigger ボード付きシンチレーター、trigger producer が条件を設定します |
| アライメントと解析 | `scripts/align_session.sh`、`scripts/analyze_session.sh` | Corryvreckan を設定ファイルで実行します |
| 結果 | QA が書いた `histograms.root` | ROOT ファイルを開きます |
