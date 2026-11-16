[English](../operator-screen.md) | [Korean](operator-screen.md) | [Japanese](../ja/operator-screen.md)

# 운영자 화면

세션은 빔이 꺼진 상태로 시작합니다. `scripts/start_session.sh`로 시작하거나 `--no-attach`로 시작한 뒤 `scripts/attach_session.sh`로 넘겨받습니다. 화면에서 떨어져도 세션은 계속됩니다.

![run 중 운영 화면의 RunControl 창입니다.](../images/runcontrol-screen.png)

## 창과 조작

공개 launcher는 `rc`(RunControl), `rp`(기준면 producer), `dp`(DUT producer), `sp`(지원 프로세스), `dc`(collector), `log`(로그 출력), `perf`(성능 출력) 창을 만듭니다. `log` 창에서는 공개 EUDAQ log collector가 돌며 RunControl, producer와 collector의 log 줄이 보입니다. run이 시작될 때마다 `RUN #NUMBER is started` 줄이 나오며, 같은 내용은 `EUDAQLog*.log` 파일에 저장되며 이 파일은 `/tmp/session-run`에 있습니다. `perf` 창에서는 `htop`이 돕니다. `Ctrl-b n`과 `Ctrl-b p`로 창을 옮기고 `Ctrl-b 0`으로 RunControl로 돌아가며 `Ctrl-b d`로 화면에서 떨어집니다. RunControl 화면에는 각 프로세스의 연결과 상태, 현재 설정, event 진행 상황이 표시됩니다. producer 줄이나 collector 줄의 event 수는 몇 초 동안 그대로였다가 한꺼번에 오를 수 있습니다. 공개 EUDAQ가 이 상태를 불규칙한 간격으로 전달하기 때문이며, 그동안에도 데이터는 계속 기록됩니다. 데이터가 들어오는지 확인하려면 아래의 분석을 다시 실행해 완전한 event 수를 비교합니다.

`S`는 이 run만 멈추고 다음 run이 곧바로 시작하며, 끝내려면 `T` 다음에 `Q`를 보냅니다.

RunControl에서 `S`는 현재 run을 멈추고 다음 설정으로 넘어가며, `R`은 현재 설정을 반복하고, `T`는 시퀀스를 종료하며, `Q`는 종료 후 화면을 닫습니다. 공개 RunControl의 키를 그대로 사용합니다.

화면이 상태 줄을 다시 그리는 순간에 키를 누르면 공개 RunControl이 완전히 멈출 수 있습니다. 화면이 더 바뀌지 않고 상태 파일과 raw 파일도 더 쓰이지 않으며, 상태 파일의 `updated_monotonic_ns`가 늘지 않고 제어 socket 요청에 답이 오지 않습니다. 이때는 `docker rm -f public-session`으로 세션을 지우고 다시 시작합니다. `scripts/session_control.sh public-session stop`, `repeat`, `terminate` 명령은 화면을 거치지 않으므로 이 영향을 받지 않습니다.

## 빔과 분석

다른 터미널에서 `scripts/session_control.sh public-session on`, `off`, `beam-status`를 사용합니다. 각 명령은 최근 다섯 번의 변경을 UTC 시각과 함께 표시하고 컨테이너 안의 전체 기록 경로를 알려줍니다. `on`이나 `off`가 수락되어 빔 상태가 바뀌면 목록의 마지막 줄이 방금 한 변경입니다. 프로그램이 읽는 응답에는 `--json`을 붙입니다. 빔은 run 변경과 무관하게 유지됩니다.

`scripts/session_control.sh public-session stop`, `repeat`, `terminate`는 RunControl 요청 `stop_next`, `repeat`, `terminate`를 보냅니다. 화면에서 `S`, `R`, `T`를 누르는 것과 같습니다. 명령마다 RunControl의 JSON 응답을 출력하며, 요청이 받아들여지면 종료 코드 0, 아니면 1입니다. `--id ID`는 요청의 id를 정하며, 응답과 `/tmp/session-run/status.json`의 `last_intent`에 그 id가 그대로 나옵니다. 주지 않으면 id는 `operator-stop`, `operator-repeat`, `operator-terminate`입니다. id는 영문자, 숫자, `.`, `-`, `_`로 된 1–64자입니다. 다른 id는 보내지 않고 종료 코드 2로 끝납니다. 같은 `--id ID`를 `on`과 `off`에도 사용할 수 있습니다.

`scripts/analyze_session.sh public-session`으로 현재 run의 한 시점까지 분석합니다. 반복 실행하면 더 나중의 데이터를 분석하며, `--run NUMBER`를 붙이면 닫힌 run을 선택합니다. 결과는 컨테이너 안의 서로 다른 디렉터리에 저장됩니다. 명령 출력 끝에는 공개 분석이 log에 적은 값이 그대로 나옵니다: snapshot의 완전한 event 수, 마지막 event 진행 줄, track 줄과 DUT마다의 `Total efficiency of detector ...` 줄입니다. 분석이 끝나면 임시 raw 데이터 사본은 지우고 `snapshot.json`과 결과는 남깁니다. `scripts/analyze_session.sh public-session --list`는 raw 파일의 run 번호를 오래된 순서로 보여 줍니다. run이 시작될 때 `log` 창에도 run 번호가 나옵니다. 분석 중에도 취득은 계속됩니다. 한도와 복구 방법은 [세션 소스와 장치 상실](session-core.md)을 참고합니다.

QA와 정렬은 각각 `Corryvreckan configuration: PATH (in the container)` 한 줄을 출력합니다. QA의 첫 줄은 컨테이너 안의 결과 디렉터리를 알려 주며, 그곳의 `analysis/output/histograms.root`에 histogram이 있습니다. `docker cp`로 복사해 ROOT로 엽니다. 실제 빔 테스트에서 분석한 뒤와 같습니다. 정렬의 설정 파일은 `/tmp/session-run/alignment/corryvreckan/`에 남아 정렬이 실행한 단계를 보여 줍니다. 그 파일들이 읽는 snapshot은 정렬이 끝날 때 지워집니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 다음 run의 설정

공개 RunControl은 run마다 시작하기 전에 설정 파일을 다시 읽습니다. `--setup`으로 시작한 세션에서는 `scripts/session_control.sh public-session set`이 다음 run의 설정 하나를 바꾸며 현재 run은 기존 설정을 유지합니다. 바꿀 수 있는 항목은 `events`(`[run]`), ALPIDE plane의 `ithr`와 `strobe_length`, msensor의 `threshold_dac`, `readout`, `window_length_ns`, `strobe_delay_ns`, `strobe_length_ns`입니다. 이름과 값은 셋업 파일과 같고 같은 규칙으로 검사하며 다른 항목은 세션 시작 때의 값을 유지합니다.

```sh
# 다음 run의 세 번째 기준면 threshold를 설정합니다.
scripts/session_control.sh public-session set alpide 2 ithr 60

# 현재 run과 다음 run의 설정을 보여 줍니다.
scripts/session_control.sh public-session settings
```

변경은 현재 run이 정해진 event에 이르렀을 때나 `stop` (`S`) 또는 `repeat` (`R`) 뒤 RunControl이 다음 run을 설정할 때 적용됩니다. 각 run은 읽은 설정을 raw 파일 옆 `run<number>.conf`로 남기고 QA는 셋업 파일과 다른 설정을 알리며 각 변경은 세션 디렉터리의 `command-history.jsonl`에 한 줄로 남습니다. 실제 빔 테스트에서도 운영자는 run 사이에 같은 방법으로 설정 파일을 편집합니다.

`[trigger]`의 `trg`, `veto`, `count`도 다음 run부터 바꿀 수 있습니다:

```sh
scripts/session_control.sh public-session set trigger count "1xx1 xxx1"
```

## producer가 응답을 멈춤

producer가 연결된 채 응답을 멈추면 RunControl 화면에서 그 event 수는 멈추고 다른 수는 계속 늘어나며 상태는 `RUNNING`으로 남을 수 있습니다. data collector는 그 producer를 기다립니다.

실제 빔 테스트에서 운영자는 해당 창에서 producer를 끝냅니다. 기준면은 `rp` 창, DUT producer는 `dp` 창에 있으며 pane마다 producer 제목과 프로세스 번호가 보입니다. 해당 pane에서 Ctrl-C를 누릅니다. Ctrl-C에 반응하지 않는 producer는 다음 명령으로 끝냅니다.

```sh
# 멈춘 producer ALPIDE_plane_2를 끝냅니다: SIGTERM, 5 s 뒤 SIGKILL.
scripts/session_control.sh public-session end-producer ALPIDE_plane_2
```

명령은 RunControl 화면에 보이는 producer 이름을 받고 작업을 `command-history.jsonl`에 기록합니다. 그 뒤 RunControl은 producer의 연결이 끊긴 것으로 표시합니다.

한 테스트에서 남은 producer와 collector는 계속되었고 `stop` 요청은 받아들여졌지만 끝나지 않았습니다. 종료한 producer를 pane에서 다시 띄우자 연결됐으나 `UNINIT`로 남아 다음 run에 참여하지 못했습니다. 모든 장치를 다시 쓰려면 `docker cp`로 출력을 보존하고 새 세션을 시작합니다.

## QA 결과 파일

QA는 실행할 때마다 세션 컨테이너의 `/tmp/session-run/qa/latest.json`(마지막 QA)을 쓰고, `status`가 `ok`이면 `/tmp/session-run/qa/history.jsonl`에 한 줄을 더합니다. 다음 항목이 유지됩니다:

- `format`: `1`, 이 항목들의 판 번호입니다. 같은 `format` 안에서 아래 항목의 이름과 뜻은 바뀌지 않습니다. 항목이 더해질 수는 있습니다. 뜻이 바뀌면 `format`을 올립니다.
- `status`: `ok`(분석이 끝남), `not_aligned`(셋업에 위치 어긋남이 있고 세션에 정렬이 없어 분석 전에 멈춤), `failed`(분석이 오류로 끝남).
- `started_utc`, `finished_utc`: QA의 시작과 끝, UTC의 ISO 8601 시각.
- `id`: `qa/` 아래의 결과 디렉터리(유한 예제는 `example-...`).
- `run`: 분석한 run의 raw 파일 이름.
- `data_events`: 분석한 완료된 data event 수(유한 예제는 마지막 분석 진행 줄의 수).
- `tracks`: 재구성한 track 수. 분석이 알리지 않으면 `null`.
- `efficiency`: DUT마다 하나씩, `dut`(분석 이름), `percent`(%), `plus`와 `minus`(위아래 오차, %), `matched`와 `total`(hit가 연결된 track 수와 센 track 수). DUT가 없으면 비어 있습니다.
- `alignment.id`: 쓴 정렬. 정렬이 없으면 `alignment`는 `null`입니다.
- `position_warning`: 정렬 뒤 장치가 움직인 것 같다고 QA가 볼 때만 있습니다.

`not_aligned`이면 `latest.json`에는 이 항목 가운데 `format`, `status`, `started_utc`, `finished_utc`, `alignment`(`null`)만 있습니다. `ok`와 `failed`이면 모두 있고, `position_warning`은 해당할 때만 있습니다. `failed`이면 `tracks`가 `null`이고 `efficiency`가 비어 있을 수 있습니다. `history.jsonl`의 줄마다 `format`, `id`, `run`, `data_events`, `tracks`, `efficiency`, `started_utc`, `finished_utc`가 있습니다. 두 파일의 다른 항목은 바뀔 수 있습니다. QA 명령은 전과 같은 줄을 출력하고, 결과 페이지는 이 파일들을 읽습니다.

## Corryvreckan을 직접 실행하기

QA 설정은 완전한 event로 된 run의 snapshot을 읽으며, QA는 분석 뒤 그것을 지웁니다. 닫힌 run의 분석(`scripts/analyze_session.sh public-session --run NUMBER`)을 Corryvreckan으로 직접 되풀이하려면 snapshot을 다시 만들고, 새 디렉터리에서 그 설정으로 `corry`를 실행합니다. `NUMBER`는 run 번호로, `RESULT`는 QA가 출력한 결과 디렉터리로 바꿉니다.

```sh
# 설정이 읽는 경로에 닫힌 run의 snapshot을 다시 만듭니다.
docker exec public-session python -m session_setup.snapshot /tmp/session-run/runNUMBER.raw RESULT/snapshot.raw

# 그 QA의 설정으로 새 디렉터리에서 Corryvreckan을 실행합니다.
docker exec public-session mkdir -p /tmp/corry-again
docker exec -w /tmp/corry-again public-session corry -c RESULT/analysis/corry.conf
```

`corry`는 그 run의 QA와 같은 track과 효율 줄을 출력합니다.
