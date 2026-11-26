[English](../reference.md) | [Korean](../ko/reference.md) | [Japanese](../ja/reference.md)

# リファレンス

プロセス、スクリプト、サンドボックスの構造です。

## 動いているもの

| 何 | 起動するもの | 止めるもの | 残るもの |
|---|---|---|---|
| イメージ | `scripts/build_session.sh`（動くものはありません） | | 再びビルドするまで残ります |
| セッション: 模擬ビームとセットアップファイルの DAQ を含むコンテナー一つ; EUDAQ モードでは RunControl、producer、data collector | `scripts/start_session.sh` | `docker rm -f NAME` | 削除の前に `docker cp` で取り出していない出力はなくなります |
| nestDAQ モードの nestDAQ プロセス: コンテナーの `NESTDAQ` tmux セッションにある Redis、daq-webctl、SlowDash、msensor 実行器、trigger ボード実行器（`trigger_board = on` のとき）、入力デバイス、FileSink、DQM | `scripts/start_session.sh` | `docker rm -f NAME` | コンテナーの `/tmp/session-run/nestdaq/data/` に保存されたファイルはコピーしなければ一緒になくなります |
| nestDAQ run | `scripts/session_control.sh NAME nestdaq start` | `scripts/session_control.sh NAME nestdaq stop` | |
| ビーム | `scripts/session_control.sh NAME on`（セッションはビームがオフの状態で始まります） | `scripts/session_control.sh NAME off` | |
| ページサーバー: コンテナーの外、このコンピューターのプロセス | `scripts/qa_page.sh` | `scripts/qa_page.sh --stop` | セッションを削除しても動き続けます |

- `docker ps -a` は動いているものと止まったものを含めてセッションのコンテナーを表示します。
- `pgrep -f session_setup.webpage` はページサーバーのプロセス番号を表示します。

## スクリプト

| 役割 | スクリプト | すること |
|---|---|---|
| セットアップ | `scripts/setup_session.sh` | セットアップファイルを作り、表示し、確認します |
| セッション | `scripts/build_session.sh` | イメージをビルドします |
| セッション | `scripts/start_session.sh` | セットアップファイルでセッションを開始します |
| セッション | `scripts/attach_session.sh` | 動いているセッションの RunControl 画面（EUDAQ モード）または msensor 実行器（nestDAQ モード）を再表示します |
| ビームと run | `scripts/session_control.sh` | ビームをオンとオフにして状態を表示します; EUDAQ モードでは run を止め、繰り返し、終え、次の run の設定を変更し、応答しなくなった producer を終了します; nestDAQ モードでは run を開始し、止め、DQM の数値を表示します |
| 解析 | `scripts/align_session.sh` | 解析のジオメトリをデータに合わせます（EUDAQ モード） |
| 解析 | `scripts/analyze_session.sh` | 現在の run か閉じた run の QA をします（EUDAQ モード） |
| データ | `python3 -m nestdaq_mode.extract` | nestDAQ モードで保存したファイルから各 msensor と trigger ボードの msraw stream を抽出します |
| データ | `python3 -m nestdaq_mode.framebuilder` | 抽出した stream または保存したファイルから、周期 trigger ごとの時間 frame を作ります（trigger ボード） |
| 結果ページ | `scripts/qa_page.sh` | ページサーバーを起動し、止めます |
| 自動化 | `scripts/wait_session.sh` | 有限の例が終わるか、セッションが終わるまで待ちます |

## 動作の仕組み

```mermaid
flowchart TB
    op{{"オペレーターまたは自動化プログラム"}}
    web["結果ページ（任意）<br/>状態と結果を表示し、同じコマンドをボタンで実行"]
    lay["セットアップツール<br/>セットアップファイルを検査し、デバイス、run、ジオメトリのファイルを生成"]

    subgraph BEAM["模擬ビームライン"]
        bc["ビーム操作<br/>DAQ から独立した on / off"]
        core["セッションコア<br/>全デバイスが共有するビームと trigger"]
        bc -.-> core
    end

    subgraph DEV["仮想デバイス"]
        vb["仮想 ALPIDE ボード<br/>基準面"]
        tb["仮想 trigger ボード<br/>trigger 条件とカウンター"]
        ms["msensor<br/>合成 DUT"]
    end

    subgraph DAQ["EUDAQ モード: 公開 EUDAQ によるデータ取得"]
        rc["RunControl<br/>状態・制御の拡張を含む"]
        ap(["ALPIDE producer<br/>公開版をそのまま使用"])
        mp["msensor producer"]
        tp(["Trigger producer<br/>公開版をそのまま使用"])
        dc(["ITS3 data collector<br/>公開版をそのまま使用"])
    end

    raw[("raw データ")]

    subgraph ANA["解析"]
        conv["コンバーター<br/>公開 ALPIDE コンバーターと msensor コンバーター"]
        corry["Corryvreckan 統合<br/>公開ソースと ROOT 互換 patch"]
        conv --> corry
    end

    res[("トラックと DUT 効率")]

    subgraph NDQ["nestDAQ モード: 公開 nestDAQ によるデータ取得"]
        ru["msensor 実行器<br/>msensor ごとに一つの stream"]
        br["trigger ボード実行器<br/>任意: 周期 trigger と物理 trigger"]
        bsrc["ボード入力デバイス<br/>ファイル、DQM コピーなし"]
        src["入力デバイス<br/>ファイルまたは TCP、msensor ごとに一つ"]
        fs(["FileSink<br/>公開版をそのまま使用"])
        dqm["msensor DQM<br/>hit map と trigger の対応づけ"]
        wc(["daq-webctl と SlowDash<br/>公開版をそのまま使用"])
    end

    frames[("msraw frame")]
    ext["抽出ツール<br/>各 msensor とボードに一つずつの msraw stream"]
    fb["frame builder<br/>周期 trigger ごとの時間 frame"]

    op -. "ビーム on / off" .-> bc
    op -. "開始・状態・停止" .-> rc
    op -. "開始・状態・停止" .-> wc
    op -. "ブラウザ" .-> web
    web -. "同じコマンド" .-> BEAM
    web -. "同じコマンド" .-> DAQ
    web -. "同じコマンド" .-> NDQ
    web -. "同じコマンド" .-> ANA
    lay -. "ファイル" .-> core
    lay -. "ファイル" .-> rc
    lay -. "ファイル" .-> ru
    core -- "frame と trigger" --> vb
    core -- "粒子と trigger" --> ms
    core <-- "条件とカウント" --> tb
    tb --> tp
    rc -. "run コマンド" .-> tp
    tp -- "カウンター状態" --> dc
    vb --> ap
    ms --> mp
    rc -. "run コマンド" .-> ap
    rc -. "run コマンド" .-> mp
    rc -. "run コマンド" .-> dc
    ap --> dc
    mp --> dc
    dc --> raw
    raw --> conv
    corry --> res
    ms --> ru
    ru --> src
    core -- "受理した trigger" --> br
    br --> bsrc
    bsrc --> fs
    wc -. "run コマンド" .-> bsrc
    wc -. "run コマンド" .-> src
    wc -. "run コマンド" .-> fs
    wc -. "run コマンド" .-> dqm
    src --> fs
    src --> dqm
    fs --> frames
    frames --> ext
    ext --> fb
```

四角形はこのリポジトリの構成要素、角丸の四角形は変更を加えずに使用する公開ソフトウェアを示します。実線はデータの流れ、破線は制御の流れを表します。セットアップファイルに従い、セッションは EUDAQ または nestDAQ の箱を使います。ビームライン、デバイス、結果ページは両方で同じです。結果ページは任意です。状態と結果を表示し、ターミナルと同じコマンドを実行します。セットアップファイルもセットアップツールと同じ検査を通して編集します。

ビルドは公開ソフトウェアのバージョンを固定します。その中には EUDAQ commit `bfe26c7`、Corryvreckan master commit `22fca3e`、nestDAQ commit `eda7287` が含まれます。すべてのバージョンと patch は[公開スタックとソースのバージョン](public-stack.md)にあります。

### リポジトリの構成

- `configs/`: 両モードのセットアップファイル、配置とセッションジオメトリの例。
- `scripts/`: [ウォークスルー](walkthrough.md)のコマンド。
- `session_setup/`: セットアップツール、セッション開始、解析コマンドと結果ページ。
- `session_core/`: 模擬ビームライン: ビーム、タイミングとデバイス間の調整。
- `virtual_alpide/`: 仮想 ALPIDE ボード。
- `virtual_trigger/`: 仮想 trigger ボード。
- `msensor/`: msensor デバイス、producer、実行器。
- `nestdaq_mode/`: nestDAQ モードのセッション開始、run 制御、入力デバイス、trigger ボード実行器、DQM、抽出ツール、frame builder。
- `runcontrol/`: RunControl 拡張と EUDAQ に適用する patch。
- `eudaq_module/`: EUDAQ 用 msensor コンバーター。
- `integration/`: 配置と Corryvreckan モジュール。
- `tracksim/`: セッションコアが使う粒子輸送。
- `docker/`: イメージのビルド。
- `docs/`: ドキュメント。
- `tests/`: 検査。
