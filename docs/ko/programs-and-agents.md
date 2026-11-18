[English](../programs-and-agents.md) | [Korean](programs-and-agents.md) | [Japanese](../ja/programs-and-agents.md)

# 프로그램과 에이전트

이 sandbox에는 에이전트 framework나 MCP server가 없습니다. 프로그램이나 에이전트는 사람과 같은 명령, 파일, 로컬 socket으로 연결합니다. 명령은 모두 호스트에서 세션 컨테이너를 상대로 실행하며 컨테이너에는 network가 없습니다.

## 프로그램이 연결할 수 있는 곳

| 목적 | 인터페이스 | 출력 | 자세한 내용 |
|---|---|---|---|
| 세션 만들기와 끝내기 | `scripts/build_session.sh`, `scripts/start_session.sh --setup FILE --name NAME --no-attach [--example]`, `docker cp NAME:/tmp/session-run DIR`, `docker rm -f NAME` | 글, 종료 코드 | [README](../../README.ko.md), [자동 실행](automation.md) |
| 셋업 파일 | `scripts/setup_session.sh new\|check\|show FILE` | 글, 종료 코드 | [셋업 파일](setup-file.md) |
| 빔 | `scripts/session_control.sh NAME on\|off\|beam-status [--id ID]` | 글(최근 빔 변경; `--id`는 on과 off에 적용) | [세션 코어](session-core.md), [운영 화면](operator-screen.md) |
| run 제어 | `scripts/session_control.sh NAME stop\|repeat\|terminate [--id ID]` | JSON 한 줄(`id`, `accepted`, `seq`, `rc_state`, 거절이면 이유) | [RunControl 확장](runcontrol-hook.md), [자동 실행](automation.md) |
| 상태 | `scripts/session_control.sh NAME status` | 상태 파일 JSON(`rc_state`, `run.number`, 연결) | [자동 실행](automation.md) |
| 다음 run 설정 | `scripts/session_control.sh NAME settings --json`, `scripts/session_control.sh NAME set SECTION ITEM VALUE [--id ID]`, `scripts/session_control.sh NAME end-producer PRODUCER [--id ID]` | settings는 JSON, set과 end-producer는 글; 종료 코드 0, 1, 2 | [자동 실행](automation.md) |
| 기다리기 | `scripts/wait_session.sh NAME ready\|closed` | 종료 코드; 180 s | [자동 실행](automation.md) |
| 분석 | `scripts/align_session.sh NAME [--run N]`, `scripts/analyze_session.sh NAME [--run N]` | 글 요약과 QA 결과 파일 | [정렬](alignment.md), [세션 run](session-run.md), [운영 화면](operator-screen.md) |
| 기록 | `/tmp/session-run/status.json`, `control-history.jsonl`, `command-history.jsonl`, `run<N>.conf`, `source.sock.beam.log` | JSON, JSON lines, 글 | [RunControl 확장](runcontrol-hook.md), [자동 실행](automation.md) |
| 운영 화면의 글 | `docker exec NAME tmux capture-pane -p -t ITS3:WINDOW` (`rc`, `rp`, `dp`, `sp`, `dc`, `log`) | 글 | [운영 화면](operator-screen.md) |
| nestDAQ run 제어 | `scripts/session_control.sh NAME nestdaq start\|stop\|status\|qa --json` | JSON | [nestDAQ](nestdaq.md) |
| msraw 추출 | `python3 -m nestdaq_mode.extract` | 글 | [nestDAQ](nestdaq.md) |
| nestDAQ 화면 텍스트 | `docker exec NAME tmux capture-pane -p -t NESTDAQ:WINDOW` (`redis`, `webctl`, `slowdash`, `runners`, `sources`, `sink`, `dqm`) | 글 | [nestDAQ](nestdaq.md) |

`--json` 옵션은 `status`에만 적용되며 `start`와 `stop`은 이미 JSON을 출력합니다.

결과 페이지 서버는 브라우저용이며 프로그램용 인터페이스로 정해 두지 않았습니다.

## 프로그램의 규칙

run 제어는 키 대신 `session_control.sh`로 보냅니다. 화면이 다시 그려질 때 누른 키가 공개 RunControl을 멈출 수 있습니다([운영 화면](operator-screen.md)). 예외는 RunControl을 나가는 `Q`를 `TERMINATED` 뒤에 보내는 것입니다. run 제어 요청마다 `--id`를 붙이면 응답과 기록에 남습니다. 빔 변경, `set`, `end-producer`도 기록을 위해 `--id`를 받습니다. `STOPPED` 같은 짧은 상태를 기대하지 말고 `run.number`가 바뀌기를 기다립니다. `stop`은 이 run만 끝내고 다음 run이 시작됩니다. 끝내려면 `terminate` 다음 `Q`를 보냅니다.

## 프로그램이 읽지 말아야 할 곳

운영 프로그램을 공정하게 시험하려면 실제 운영자가 볼 수 없는 시뮬레이션 정답을 읽지 말아야 합니다. 컨테이너의 `/tmp/session-private/`에는 장치의 실제 위치가 있고, 세션 프로세스의 환경 변수에는 빔의 비공개 값과 seed가 있습니다. `actual_offsets`는 `session_setup/setupfile.py`에 있으며 셋업에서 숨은 위치 차이를 계산합니다.


nestDAQ 모드에서도 같으며 더 많은 세션 프로세스가 이 환경 변수를 물려받습니다.

## 명령 감싸기

MCP server나 function call 같은 에이전트용 도구 층에서 위 명령을 하나씩 감싸면 세션을 바꿀 필요가 없습니다. 지원하는 요청마다 `--id`를 남기고 화면 글보다 JSON 출력을 읽습니다.

기본 셋업의 `public-session` 세션이 실행 중일 때 저장소 맨 위 디렉터리에서 이 Python 예를 실행합니다.

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
