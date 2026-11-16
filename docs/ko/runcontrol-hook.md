[English](../runcontrol-hook.md) | [Korean](../ko/runcontrol-hook.md) | [Japanese](../ja/runcontrol-hook.md)

# RunControl 관측과 제어

이 시스템의 응답은 합성값이며 실제 장비의 측정값이 아닙니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 옵션

공개 ITS3 RunControl은 원자적 JSON 상태를 위한 `--status-file PATH`, 로컬 JSON 제어를 위한 `--control-socket PATH`, 마지막 설정 후 종료하는 `--one-pass`를 받습니다. socket intent `stop_next`, `repeat`, `terminate`는 대응하는 키보드 제어와 같습니다. `accepted`는 요청 접수일 뿐 run 종료를 뜻하지 않습니다. `its3-runcontrol-status/v1` 상태는 끊어진 연결을 `connected=false`로 보존합니다. `run.number`는 현재 run의 번호입니다. 공개 API가 이 번호를 내주지 않으므로, run이 `RUNNING`이 될 때 상태 파일과 같은 디렉터리에서 data collector가 연 raw 파일의 이름(`run<number>.raw`)에서 읽습니다. 이 번호는 run이 멈춘 뒤에도 다음 run이 `RUNNING`이 될 때까지 그대로입니다. 첫 run 전이나 그 디렉터리에 raw 파일이 없으면 `null`입니다.

![제어 socket으로 요청을 보내는 R과 T 버튼이 있는 DAQ 화면 탭입니다.](../images/page-daq.png)

## 조작 이력

`control-history.jsonl`은 상태 파일과 같은 디렉터리에 있으며 세션에서는 `/tmp/session-run/control-history.jsonl`입니다. `S`, `R`, `T`는 누를 때마다 기록하고 수락된 socket 요청도 기록합니다. 거절되거나 형식이 틀린 요청과 `Q`는 기록하지 않습니다. 상태 파일이 없으면 이력도 쓰지 않습니다.

| 항목 | 뜻 |
|---|---|
| `format` | 형식 버전 `1` |
| `seq` | 1부터 시작하는 이력 순번 |
| `utc` | ISO 8601 UTC 시각 |
| `monotonic_ns` | 나노초 단위 단조 시각 |
| `kind` | 요청: `stop_next`, `repeat`, `terminate` |
| `source` | 출처: `keyboard`, `socket` |
| `id` | 요청 식별자 |
| `status_seq` | 상태 파일의 `last_intent` 순번 |
| `rc_state` | 요청 때의 RunControl 상태 |

같은 `format`에서는 이 항목의 이름과 뜻을 유지하며 항목이 추가될 수 있습니다. 뜻이 바뀌면 `format`을 올립니다.


hook은 RunControl이 각 run에서 읽은 설정도 raw 파일 옆에 `run<number>.conf`로 보존합니다. 설정 변경과 종료한 producer는 이 파일에 없으며 `scripts/session_control.sh`가 `command-history.jsonl`에 기록합니다.

## launcher

launcher는 `--extra-dut`, `--rc-args`, `--rc-address`, `--producer-env`, `--no-attach`를 지원합니다. 환경 변수 값이 아니라 이름을 전달합니다. msensor producer는 성공적으로 보낸 data event 수를 run마다 0부터 세어 `DataEventN`으로 직접 보고하며 BORE와 EORE는 세지 않습니다. 검사는 `tests/scripts/run_runcontrol_e2e.sh`로 실행합니다.

수락된 요청은 run 시작 중에도 보관되며 run이 시작되면 적용됩니다. 다른 요청이 대기 중이거나 적용할 run이 없으면 이유와 함께 거절합니다. 키보드와 socket은 같은 처리를 사용합니다.

launcher는 client를 시작하기 전에 RunControl 프로세스가 요청된 TCP listener를 소유할 때까지 기다립니다. 포트가 이미 사용 중이거나 listener가 준비되지 않으면 오류를 냅니다.
