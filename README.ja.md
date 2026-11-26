[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md)

# Test-beam sandbox

このリポジトリはビームテストのデータ取得と解析を練習する環境です。模擬ビームは仮想ピクセルセンサーの telescope（基準面）と、試験対象の合成ピクセルセンサー（msensor、DUT: 試験対象デバイス）を通過します。デバイス、その設定、ビームを一つのセットアップファイルで指定し、ビームはデータ取得と独立してオンとオフにできます。

セッションは、セットアップファイルで選んだ二つの公開データ取得フレームワークのどちらかでデータを取得します。既定の EUDAQ は Corryvreckan でアライメント、トラック、DUT の効率を扱い、nestDAQ はすべての msensor を stream として読み、仮想 trigger ボードを加えられます。両者の比較は [DAQ モード](docs/ja/daq-modes.md)にあります。

オペレーターがデータ取得と解析のフローを習得する用途だけでなく、取得を制御する自動化プログラムの開発・テストにも利用できます。すべての応答はシミュレーションによる合成データであり、実機の測定値ではありません。

![run 中の結果ページの実験タブ: ビームに沿った 3D のデバイス、オンのビームと run の状態](docs/images/experiment-view.png)

## 動作環境

Linux コンテナーを実行する Docker が動いている Linux または macOS、Python 3、40 GiB 以上の空き容量が必要です。ビルドにはインターネットが必要で、セッションはネットワークなしで動きます。ビルドは Docker デーモンのアーキテクチャ（arm64 または amd64）を選び、最大 4 ジョブと 8 GiB のメモリを使います。ホストでは Python 3.9 以降で追加パッケージなしに結果ページと nestDAQ モードの抽出ツールおよび frame builder を実行します。macOS では最初の `python3` がコマンドライン開発者ツールのインストールを求めることがあります。

## クイックスタート

コマンドはすべてリポジトリの最上位ディレクトリで実行します。最初のビルドは EUDAQ と Corryvreckan をコンパイルするため時間がかかります（24 コアの arm64 コンピューターで約 4 分）。以後のセッションはイメージを再利用します。

```sh
# コードを取得します。
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# 公開ベースイメージとセッションイメージをビルドします（一度だけ）。
scripts/build_session.sh

# 既定のセットアップでセッションを開始し、公開 RunControl 画面を表示します。
scripts/start_session.sh --setup configs/setups/default.ini --name public-session

# 別のターミナルで模擬ビームをオンにし、現在の run をアライメントして解析します。
scripts/session_control.sh public-session on
scripts/align_session.sh public-session
scripts/analyze_session.sh public-session
```

セッションの終了、nestDAQ モード（`scripts/build_session.sh --nestdaq`）、ブラウザは[ウォークスルー](docs/ja/walkthrough.md)に続きます。

## ドキュメント

### 操作

- [ウォークスルー](docs/ja/walkthrough.md): ターミナル（EUDAQ、nestDAQ）とブラウザでセッションを手順どおりに進めます。
- [DAQ モード](docs/ja/daq-modes.md): EUDAQ モードと nestDAQ モードの比較、サンドボックスと実際のテストビームの対応。
- [リファレンス](docs/ja/reference.md): 動いているもの、スクリプト、各部分の連携とリポジトリの構成。
- [操作画面](docs/ja/operator-screen.md): ウィンドウとキー、ビームと run のコマンド、QA の出力と結果ファイル。
- [セットアップファイル](docs/ja/setup-file.md): デバイス、設定、run の長さ、ビームと `scripts/setup_session.sh`。
- [nestDAQ モード](docs/ja/nestdaq.md): nestDAQ で msensor を読むセッションのセットアップ、trigger ボード（任意）、run、画面、データ、frame builder、制約。
- [アライメント](docs/ja/alignment.md): デバイスの位置をなぜ、どう求めるか。
- [結果ページ](docs/ja/qa-page.md): ブラウザのページ。
- [trigger](docs/ja/trigger.md): trigger シンチレーターと trigger 条件。
- [msensor](docs/ja/msensor.md): オフにできるタイルに分かれた合成 DUT と、そのデータ形式およびストリーム形式。既定のタイル配置は `6 x 2` タイル、タイルごとに `156 x 444` ピクセル、ピッチ `23 x 21` µm です。
- [自動実行の例](docs/ja/automation.md): プログラム向けの、画面なしで動くセッション、および状態ファイルの状態。
- [プログラムとエージェント](docs/ja/programs-and-agents.md): プログラムやエージェントが接続できるもの。


### 内部構成

- [セッションソースとビーム制御](docs/ja/session-core.md): 共有の模擬ビーム、タイミング、デバイス間の連携と独立した on/off 操作。
- [仮想 ALPIDE ボード](docs/ja/virtual-alpide-board.md): 公開版 producer 向けのボードインターフェース。
- [仮想 trigger ボード](docs/ja/trigger-board-surface.md): 公開 producer の条件とカウンター。
- [公開ボードの呼び出し範囲](docs/ja/alpide-board-surface.md): 公開 producer が使うボードインターフェース。
- [RunControl の拡張](docs/ja/runcontrol-hook.md): 状態ファイルと制御 socket。
- [セッションの取得と解析](docs/ja/session-run.md): 配置、作られるファイル、解析。
- [tracksim](docs/ja/tracksim.md): セッションコアの粒子輸送。
- [公開スタックとソースのバージョン](docs/ja/public-stack.md): 公開ソフトウェアのバージョンと patch。

## ライセンス

Copyright (C) 2026 Bong-Hwi Lim (CNS, the University of Tokyo)

このリポジトリは `LGPL-3.0-or-later` ライセンスのもとで配布されています。LGPL は GPL の本文に追加条件を定めるライセンス形態であるため、ライセンス全文は [COPYING.LESSER](COPYING.LESSER) および [COPYING](COPYING) にあります。EUDAQ（`LGPL-3.0`）および Corryvreckan（`MIT`）はビルド時に外部から取得されるため、このリポジトリにはそれらのソースコードのコピーは含まれていません。`runcontrol/ITS3RunControl.patch` と `runcontrol/ITS3start.patch` が EUDAQ に、`docker/base/corry-root-eigen.patch` が Corryvreckan にそれぞれ適用されます（各パッチファイルには適用先ソフトウェアの該当箇所のコードが含まれています）。結果ページは JSROOT `7.5.2`（`MIT`）で描画し、実験画面の 3D はその JSROOT に含まれる three.js `r155`（`MIT`）で描画します。JSROOT はセッションのイメージに ROOT とともに含まれ、ページサーバーがイメージから取り出して使います。このリポジトリにはそのコピーは含まれていません。

nestDAQ モードでのみビルドする nestDAQ イメージはビルド中に追加の公開ソフトウェアをダウンロードします。[公開スタックとソースのバージョン](docs/ja/public-stack.md)を参照します。
