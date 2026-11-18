[English](programs-and-agents.md) | [Korean](ko/programs-and-agents.md) | [Japanese](ja/programs-and-agents.md)

# Programs and agents

This sandbox contains no agent framework or MCP server. A program or agent connects through the same commands, files and local sockets as a person. The commands run on the host against the session container, which has no network.

## What a program can connect to

| Purpose | Interface | Output | Details |
|---|---|---|---|
| Create and finish a session | `scripts/build_session.sh`, `scripts/start_session.sh --setup FILE --name NAME --no-attach [--example]`, `docker cp NAME:/tmp/session-run DIR`, `docker rm -f NAME` | Text, exit code | [README](../README.md), [automation](automation.md) |
| Setup file | `scripts/setup_session.sh new\|check\|show FILE` | Text, exit code | [Setup file](setup-file.md) |
| Beam | `scripts/session_control.sh NAME on\|off\|beam-status [--id ID]` | Text (recent beam changes; `--id` applies to on and off) | [Session core](session-core.md), [operator screen](operator-screen.md) |
| Run control | `scripts/session_control.sh NAME stop\|repeat\|terminate [--id ID]` | One JSON line (`id`, `accepted`, `seq`, `rc_state`, and a reason if rejected) | [RunControl extensions](runcontrol-hook.md), [automation](automation.md) |
| Status | `scripts/session_control.sh NAME status` | Status file JSON (`rc_state`, `run.number`, connections) | [Automation](automation.md) |
| Next run settings | `scripts/session_control.sh NAME settings --json`, `scripts/session_control.sh NAME set SECTION ITEM VALUE [--id ID]`, `scripts/session_control.sh NAME end-producer PRODUCER [--id ID]` | JSON for settings; text for set and end-producer; exit codes 0, 1, 2 | [Automation](automation.md) |
| Wait | `scripts/wait_session.sh NAME ready\|closed` | Exit code; 180 s | [Automation](automation.md) |
| Analysis | `scripts/align_session.sh NAME [--run N]`, `scripts/analyze_session.sh NAME [--run N]` | Text summary and QA result files | [Alignment](alignment.md), [session run](session-run.md), [operator screen](operator-screen.md) |
| Records | `/tmp/session-run/status.json`, `control-history.jsonl`, `command-history.jsonl`, `run<N>.conf`, `source.sock.beam.log` | JSON, JSON lines, text | [RunControl extensions](runcontrol-hook.md), [automation](automation.md) |
| Operator screen text | `docker exec NAME tmux capture-pane -p -t ITS3:WINDOW` (`rc`, `rp`, `dp`, `sp`, `dc`, `log`) | Text | [Operator screen](operator-screen.md) |
| nestDAQ run control | `scripts/session_control.sh NAME nestdaq start\|stop\|status\|qa --json` | JSON | [nestDAQ](nestdaq.md) |
| Extract msraw | `python3 -m nestdaq_mode.extract` | Text | [nestDAQ](nestdaq.md) |
| nestDAQ screen text | `docker exec NAME tmux capture-pane -p -t NESTDAQ:WINDOW` (`redis`, `webctl`, `slowdash`, `runners`, `sources`, `sink`, `dqm`) | Text | [nestDAQ](nestdaq.md) |

The `--json` option applies only to `status`; `start` and `stop` already print JSON.

The result page server is for browsers; it is not defined as a program interface.

## Rules for a program

Send run control with `session_control.sh`, not keys: keys pressed while the screen redraws can freeze public RunControl ([operator screen](operator-screen.md)). The exception is `Q` after `TERMINATED` to leave RunControl. Give each run control request an `--id`, which is recorded in its response and history. Beam changes, `set` and `end-producer` also accept an `--id` for their history. Do not rely on seeing a brief state such as `STOPPED`; wait for `run.number` to change. `stop` ends only this run and the next starts; to finish, send `terminate`, then `Q`.

## What a program should not read

For a fair test of an operating program, do not read simulation truth unavailable to an operator: `/tmp/session-private/` in the container (actual device positions), the session processes’ environment variables (private beam values and seeds), or `actual_offsets` in `session_setup/setupfile.py` (calculates the hidden position differences from the setup).


The same holds in the nestDAQ mode, where more of the session's processes inherit these variables.

## Wrapping the commands

An agent tool layer, such as an MCP server or function calls, can wrap these commands individually without changing the session. Give each supported request an `--id` and read JSON output rather than screen text.

Run this Python example from the repository root with a running default session named `public-session`:

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
