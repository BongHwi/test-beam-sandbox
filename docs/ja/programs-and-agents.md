[English](../programs-and-agents.md) | [Korean](../ko/programs-and-agents.md) | [Japanese](programs-and-agents.md)

# プログラムとエージェント

この sandbox にはエージェント framework や MCP server は含まれません。プログラムやエージェントは人と同じコマンド、ファイル、ローカル socket で接続します。コマンドはすべてホストからセッションコンテナーに対して実行し、コンテナーにはネットワークがありません。

## プログラムが接続できるもの

| 目的 | インターフェース | 出力 | 詳細 |
|---|---|---|---|
| セッションの作成と終了 | `scripts/build_session.sh`, `scripts/start_session.sh --setup FILE --name NAME --no-attach [--example]`, `docker cp NAME:/tmp/session-run DIR`, `docker rm -f NAME` | テキスト、終了コード | [README](../../README.ja.md)、[自動実行](automation.md) |
| セットアップファイル | `scripts/setup_session.sh new\|check\|show FILE` | テキスト、終了コード | [セットアップファイル](setup-file.md) |
| ビーム | `scripts/session_control.sh NAME on\|off\|beam-status [--id ID]` | テキスト（最近のビーム変更。`--id` は on と off に適用） | [セッションコア](session-core.md)、[操作画面](operator-screen.md) |
| run 制御 | `scripts/session_control.sh NAME stop\|repeat\|terminate [--id ID]` | JSON 一行（`id`、`accepted`、`seq`、`rc_state`、拒否時は理由） | [RunControl 拡張](runcontrol-hook.md)、[自動実行](automation.md) |
| 状態 | `scripts/session_control.sh NAME status` | 状態ファイルの JSON（`rc_state`、`run.number`、接続） | [自動実行](automation.md) |
| 次の run の設定 | `scripts/session_control.sh NAME settings --json`, `scripts/session_control.sh NAME set SECTION ITEM VALUE [--id ID]`, `scripts/session_control.sh NAME end-producer PRODUCER [--id ID]` | settings は JSON、set と end-producer はテキスト、終了コード 0、1、2 | [自動実行](automation.md) |
| 待機 | `scripts/wait_session.sh NAME ready\|closed` | 終了コード、180 s | [自動実行](automation.md) |
| 解析 | `scripts/align_session.sh NAME [--run N]`, `scripts/analyze_session.sh NAME [--run N]` | テキストの要約と QA 結果ファイル | [アライメント](alignment.md)、[セッション run](session-run.md)、[操作画面](operator-screen.md) |
| 記録 | `/tmp/session-run/status.json`, `control-history.jsonl`, `command-history.jsonl`, `run<N>.conf`, `source.sock.beam.log` | JSON、JSON lines、テキスト | [RunControl 拡張](runcontrol-hook.md)、[自動実行](automation.md) |
| 操作画面のテキスト | `docker exec NAME tmux capture-pane -p -t ITS3:WINDOW` (`rc`, `rp`, `dp`, `sp`, `dc`, `log`) | テキスト | [操作画面](operator-screen.md) |
| nestDAQ run 制御 | `scripts/session_control.sh NAME nestdaq start\|stop\|status\|qa --json` | JSON | [nestDAQ](nestdaq.md) |
| msraw 抽出 | `python3 -m nestdaq_mode.extract` | テキスト | [nestDAQ](nestdaq.md) |
| nestDAQ 画面テキスト | `docker exec NAME tmux capture-pane -p -t NESTDAQ:WINDOW` (`redis`, `webctl`, `slowdash`, `runners`, `sources`, `sink`, `dqm`) | テキスト | [nestDAQ](nestdaq.md) |

`--json` オプションは `status` にのみ適用し、`start` と `stop` は既に JSON を出力します。

結果ページサーバーはブラウザ用であり、プログラム用インターフェースとして定めていません。

## プログラムの規則

run 制御はキーではなく `session_control.sh` で送ります。画面の再描画中に押したキーが公開 RunControl を停止させることがあります（[操作画面](operator-screen.md)）。例外は RunControl を抜ける `Q` を `TERMINATED` 後に送ることです。run 制御の要求ごとに `--id` を付けると応答と記録に残ります。ビーム変更、`set`、`end-producer` も記録のために `--id` を受け取ります。`STOPPED` のような短い状態を期待せず、`run.number` が変わるまで待ちます。`stop` はこの run だけを終了し、次の run が始まります。終了するには `terminate` の後に `Q` を送ります。

## プログラムが読むべきでないもの

運用プログラムを公平に試すため、実際のオペレーターには見えないシミュレーションの正解を読まないでください。コンテナーの `/tmp/session-private/` にはデバイスの実際の位置、セッションプロセスの環境変数にはビームの非公開値と seed があります。`actual_offsets` は `session_setup/setupfile.py` にあり、セットアップから隠れた位置の差を計算します。


nestDAQ モードでも同様で、より多くのセッションプロセスがこれらの環境変数を継承します。

## コマンドをラップする

MCP server や function call などのエージェント用ツール層で上のコマンドを一つずつラップすれば、セッションを変更する必要はありません。対応する要求ごとに `--id` を残し、画面のテキストより JSON 出力を読みます。

既定のセットアップの `public-session` が実行中のとき、リポジトリの最上位でこの Python 例を実行します。

```python
import json
import subprocess

name = "public-session"
status = json.loads(subprocess.check_output(
    ["scripts/session_control.sh", name, "status"], text=True))
print(status["rc_state"], status["run"]["number"])
reply = json.loads(subprocess.check_output(
    ["scripts/session_control.sh", name, "stop", "--id", "agent-stop-1"], text=True))
print(reply["accepted"])
```
