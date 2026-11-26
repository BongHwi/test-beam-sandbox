[영어](../nestdaq.md) | [한국어](../ko/nestdaq.md) | [일본어](../ja/nestdaq.md)

# nestDAQ 모드

## 소개

nestDAQ 모드에서는 세션이 EUDAQ 대신 nestDAQ로 모든 msensor를 읽습니다. msensor마다 실행기가 continuous 또는 triggered stream을 씁니다. msensor마다 입력 장치가 파일이나 TCP 연결에서 stream을 읽고 FileSink가 frame을 저장하며 msensor DQM이 hit map을 채우고 msensor의 trigger를 짝짓습니다. daq-webctl은 run을 제어하고 SlowDash는 시계열을 보여 줍니다. 빔, 셋업 파일과 결과 페이지는 EUDAQ 모드와 같이 동작하며 EUDAQ 모드 자체는 그대로입니다.

EUDAQ 모드와 비교하면:

- msensor만 읽고 ALPIDE 평면은 빔라인의 물질로 남습니다.
- 신틸레이터, `[trigger]`, `--example`은 사용할 수 없으며 `events`는 무시합니다.
- 정렬과 분석은 아직 사용할 수 없습니다. QA를 위해 DQM은 hit map, correlation과 trigger 짝짓기를 보여 주고 `nestdaq qa`와 페이지의 QA 탭은 그 숫자를 출력하며 추출 도구는 msensor마다 msraw stream을 별도 파일에 씁니다.

## 빌드

`scripts/build_session.sh --nestdaq`로 빌드합니다. ARM64에서 nestDAQ 이미지를 세션 이미지 위에 처음 빌드하는 데 약 10~13분이 걸렸습니다. 이미지 크기는 약 1.4 GB로 세션 이미지보다 약 0.2 GB 큽니다. 구성 요소와 라이선스는 [공개 스택과 소스 버전](public-stack.md)에 있습니다.

## 셋업 파일

예제 셋업은 EUDAQ 모드의 셋업과 함께 `configs/setups/`에 있으며 페이지의 셋업 탭에 나옵니다:

| 파일 | 보여 주는 것 |
|---|---|
| `nestdaq.ini` | nestDAQ 모드로 읽는 msensor 두 개 |
| `nestdaq-clocks.ini` | 자기 시계로 동작하는 msensor |
| `nestdaq-missed.ini` | 일부 trigger를 놓치는 msensor |
| `nestdaq-telescope.ini` | 기준 msensor 여섯 개와 DUT msensor 한 개로 된 telescope |
| `nestdaq-board.ini` | 가상 trigger 보드를 쓰는 msensor 두 개: 주기 trigger와 보드 stream |

`[run] daq = nestdaq`로 설정합니다. `[nestdaq] input = file | socket`으로 입력을 고르고 `frame_ms`로 frame을 설정하며 `send_buffer_mb`는 socket 입력에만 적용합니다. 모든 항목은 [셋업 파일 설명](setup-file.md)을 참조합니다.

msensor마다 자기 시계로 동작하게 할 수 있습니다. `[msensor N]` 섹션에서 `clock_offset_ns`는 세션 시계가 0일 때 그 msensor 시계가 가리키는 값이고, `clock_drift_ppm`은 그 시계가 얼마나 빨리 가는지를 ppm(백만분율) 단위로 정합니다. 두 키는 `daq = nestdaq`에서만 쓰며 기본값 0은 세션과 공유하는 시계입니다. 이때 msensor는 window, frame, trigger 시각을 자기 시계로 세므로 msensor끼리 window 번호가 맞지 않습니다. trigger 번호는 모두에게 공통입니다. 예시는 `configs/setups/nestdaq-clocks.ini`에 있습니다.

msensor가 일부러 trigger를 놓치게 할 수도 있습니다. `[msensor N]` 섹션의 `missed_triggers`에는 이 msensor가 보지 못하는 세션 trigger 번호를 적으며, 번호는 run마다 0부터 셉니다. 그러면 msensor는 그 trigger의 레코드를 쓰지 않고, 놓친 trigger 하나마다 뒤의 trigger 번호를 하나씩 낮춰 붙입니다. trigger를 스스로 세는 센서가 그렇게 하기 때문입니다. 그래서 처음 놓친 trigger부터는 그 msensor의 trigger 번호가 다른 msensor와 맞지 않습니다. 이 키는 `daq = nestdaq`에서만 씁니다. trigger 보드를 켜면(아래 참조) 이 번호는 주기 trigger의 번호입니다. 예시는 `configs/setups/nestdaq-missed.ini`에 있습니다.

nestDAQ 모드에서는 msensor가 노이즈 hit도 내게 할 수 있습니다. `[msensor N]` 섹션에 `noise = on`을 쓰면 msensor의 기본 응답을 쓰며, 그 노이즈 발생률은 `threshold_dac`를 낮출수록 커집니다. 기본값 `noise = off`에서는 EUDAQ 모드와 같이 노이즈 hit가 없습니다.

데이터 경로의 부하 시험에는 `[nestdaq]` 섹션의 `generator_mb_s`를 씁니다. 0보다 크면 모든 msensor 실행기를 발생기로 바꾸며, 발생기는 빔 시뮬레이션 없이 주기적인 trigger와 함께 msensor당 약 그만큼의 MB/s로 올바른 msensor raw frame을 씁니다. 모든 msensor는 continuous readout이어야 합니다. 데이터에 물리적 의미가 없으므로 이 모드에서는 DQM correlation이 의미가 없습니다.
`configs/setups/nestdaq-telescope.ini`의 telescope에서는 가장 상류의 msensor를 뺀 나머지가 각자 자기 시계로 동작합니다. msensor마다 자기 실행기와 입력 장치가 있고 모두 같은 FileSink와 DQM으로 데이터를 보냅니다. DQM은 msensor마다 가장 상류의 msensor와 짝을 짓습니다.

`[nestdaq]` 섹션에 `trigger_board = on`을 쓰면 가상 trigger 보드를 더할 수 있습니다. 기본값 `off`에서는 위의 내용이 모두 그대로입니다. 보드를 켜면 모든 msensor가 입자마다 하나씩 받던 trigger 대신 주기 trigger를 받습니다. run마다 0부터 세는 k번째 주기 trigger는 run 시작 후 100.5 MHz 보드 시계로 (k + 1) × `periodic_clk` 사이클째에 옵니다. `periodic_clk`는 10000에서 1005000000까지이며 기본값 1005000은 10 ms입니다. 주기는 모든 msensor의 window 하나 이상이어야 합니다. hit는 여전히 모든 입자에서 나옵니다. 각 msensor는 주기 trigger를 자기 시계로 TRIGGER 레코드에 쓰므로, 시계가 다르면 번호가 같은 주기 trigger도 시각이 다릅니다. 모든 msensor는 continuous readout이어야 하며 `generator_mb_s`는 보드와 함께 쓸 수 없습니다. 이 모드에서는 scintillator와 `[trigger]`를 쓸 수 있으며, 어느 입자가 물리 trigger인지를 이것들이 정합니다. scintillator가 없으면 모든 입자가 패턴 0인 물리 trigger입니다. 보드 실행기 `TRIGGERBOARD_0`는 세션이 받아들인 물리 trigger를 읽어 주기 trigger와 함께 보드 stream으로 씁니다. 이 stream은 자기 입력 장치를 거쳐 DQM 복사본 없이 FileSink에 도달합니다. 예시는 `configs/setups/nestdaq-board.ini`에 있습니다.

## 세션 시작

`scripts/start_session.sh --setup FILE --name public-session`으로 시작하면 이미지를 자동으로 고릅니다. `--no-attach`를 쓰면 README처럼 터미널을 그대로 쓸 수 있습니다. tmux의 `NESTDAQ`에는 일곱 창이 있습니다. `redis`는 Redis, `webctl`은 run 제어, `slowdash`는 시계열, `runners`는 msensor 실행기, `sources`는 입력 장치, `sink`는 FileSink, `dqm`은 모니터링을 담당합니다. `scripts/attach_session.sh public-session`은 `NESTDAQ:runners`에 붙습니다. `Ctrl-b`를 누르고 `d`를 누르면 나갑니다. 실행기가 종료되면 2초 후 다시 시작합니다.

## Run

`scripts/session_control.sh NAME nestdaq start|stop|status|qa [--json]`을 사용합니다. `--json` 옵션은 `status`와 `qa`에만 적용하며 `start`와 `stop`은 이미 JSON을 출력합니다. 종료 코드는 성공 0, 실패 1, 거부 2, stop의 drain 미완료 3입니다. 페이지 탭의 Start와 Stop 및 daq-webctl 페이지도 같은 run 제어를 사용합니다. Drain은 STOP 전에 남은 데이터를 보냅니다. 명령과 페이지 탭은 이미 데이터가 있는 run 번호를 거부합니다. daq-webctl의 RUN은 다음 빈 번호로 넘어가지만 새로 고치기 전까지 이전 번호가 보일 수 있습니다. 빔 명령은 `on`, `off`, `beam-status` 그대로이며 다른 EUDAQ 명령은 거부됩니다.

`qa`는 DQM에서 msensor마다 현재 run의 frame, trigger와 hit를 읽고, 나머지 msensor마다 가장 상류 msensor와의 trigger 짝짓기(짝지음, 둘 중 하나에만 있음, 건너뜀), 열과 행의 차이(픽셀), 시계 drift(ppm), 시간 offset(마이크로초)을 읽습니다. `--json`이 없으면 표를 출력합니다. 첫 짝이 생기기 전에도 DQM이 응답하면 0, DQM을 읽을 수 없으면 1을 반환합니다. 페이지의 QA 탭도 같은 숫자를 보여 줍니다.

## 화면

`scripts/qa_page.sh public-session`으로 페이지를 엽니다. nestDAQ 탭에서 run 제어, 장치, 스트림, 화면 링크, 상태와 저장 파일을 봅니다.

![nestDAQ 탭에 실행 중인 세션과 화면 링크가 보입니다.](../images/page-nestdaq.png)

daq-webctl은 run을 제어하고 SlowDash의 `slowplot-msensor`는 시계열을 보여 줍니다. DQM은 columns × rows bin의 `hitmap`, `hitmap_tiles`, tiles_x × tiles_y bin의 `tile_hits`, 2048 bin이며 범위가 −0.5 이상 2047.5 미만인 `hits_per_frame`과 `triggers_per_frame`을 보여 줍니다. 포트 8080, 18881, 8090은 컨테이너 안에 있습니다. DQM 링크는 히스토그램 목록을 엽니다. `MSENSOR_0/hitmap`은 `MSENSOR_0`의 hit map을, `correlation/summary`는 다른 msensor 전체를 보여 줍니다.

DQM은 다른 msensor마다 그 trigger를 가장 상류의 msensor(z가 가장 작은 것)의 trigger와 짝짓기도 합니다. 아래의 DEV는 다른 msensor의 이름입니다. 짝은 시각으로 짓습니다. 처음 몇 trigger로 기준을 잡은 뒤, 시계 drift 때문에 천천히만 변하는 두 trigger 시각의 차이를 따라가며 그 차이가 이어지는 trigger끼리 짝짓습니다. 그래서 한 msensor가 trigger를 놓쳐도 뒤의 짝이 밀리지 않습니다. `correlation/col_DEV`와 `correlation/row_DEV`는 짝지은 trigger의 column과 row를 비교하고, `dcol_DEV`와 `drow_DEV`는 그 차이를 보여 주며, `trigger_dt_DEV`는 run이 진행되면서 trigger 시각의 차이를, `trigger_dn_DEV`는 짝의 trigger 번호 차이를 보여 줍니다. 번호 차이는 놓친 trigger마다 계단처럼 바뀝니다. `match_DEV`는 짝지은 trigger, 짝짓지 못한 trigger, 건너뛴 trigger의 수를 세고, `lost_DEV`는 상류 msensor에만 있거나 DEV에만 있는 trigger의 수를 셉니다. continuous readout에서는 trigger 하나가 그 window의 hit를 모두 가져오므로 correlation이 같은 window에 든 다른 입자의 hit가 만드는 넓은 배경 위에 나타납니다. 차이 히스토그램에서 가장 뚜렷하게 보이며, 그 봉우리의 위치가 두 msensor 사이의 어긋남입니다.

`correlation/summary`는 다른 msensor 모두를 한 번에, msensor마다 bin 하나로 보여 줍니다. `summary_dcol`과 `summary_drow`는 각 msensor의 차이 히스토그램의 봉우리 위치, 즉 그 msensor가 상류 msensor에서 어긋난 정도(픽셀)를 보여 주고, `summary_drift`와 `summary_offset`은 그 msensor의 trigger 시각 차이를 지나는 직선의 기울기(ppm)와 절편(마이크로초)을 보여 주므로 모든 msensor의 시계를 상류 msensor와 비교할 수 있으며, `summary_counts`는 짝지은 trigger, 두 msensor 중 한쪽만 본 trigger, 건너뛴 trigger의 수를 보여 줍니다.

화면은 네트워크가 없는 컨테이너 안에서 실행되며 페이지 서버가 `127.0.0.1`에서 중계합니다. nestDAQ 탭의 링크에는 ticket이 있으며 첫 방문에서 해당 포트의 cookie로 교환하고 주소에서 ticket을 제거합니다. 연결의 첫 요청만 검사하므로 페이지 조작의 token보다 약합니다. 혼자 사용하는 컴퓨터에서 사용하고 끝나면 `scripts/qa_page.sh --stop`으로 페이지 서버를 중지합니다.

## 데이터

데이터는 실행기에서 입력 장치를 거쳐 FileSink로 갑니다. `socket`에서는 실행기가 컨테이너 포트 `18600 + N`으로 보내며 수신이 느리면 frame을 버리고 그 수를 셉니다. `file`은 실행기의 파일을 읽습니다. 저장 파일은 컨테이너의 `/tmp/session-run/nestdaq/data/`에 run마다 하나씩 있으며 `run000001.dat`와 같은 이름입니다. `docker cp NAME:/tmp/session-run ./session-output`으로 복사한 뒤 호스트의 저장소 맨 위 디렉터리에서 `python3 -m nestdaq_mode.extract session-output/nestdaq/data/run000001.dat OUTDIR`을 실행합니다. msensor마다 msraw stream을 OUTDIR의 별도 파일에 쓰고 JSON 요약을 출력합니다. Extract는 완전한 데이터에 0, 불완전하거나 누락된 frame에 3, 형식이나 입출력 오류에 2를 반환합니다. 호스트의 Python 3, 버전 3.9 이상으로 실행하며 추가 패키지가 필요하지 않습니다.

msraw stream에는 헤더와 다섯 레코드 종류 `TILE_DATA`, `TRIGGER`, `TICK`, `TILE_STATUS`, `END`가 있으며 `TICK`이 frame을 끝냅니다. 메시지는 SubTimeFrame 헤더와 `META`, frame, `END` 데이터를 담습니다.

trigger 보드를 쓰면 run은 보드 stream도 저장하며, `extract`가 이를 `TRIGGERBOARD_0_runN.msraw`로 씁니다. 이 stream은 msraw stream의 컨테이너를 쓰며, 메타데이터 형식은 `msraw-board-metadata-1`, plane은 1000입니다. 레코드는 `PERIODIC`(주기 trigger 번호와 보드 시계), `PHYSICS`(물리 trigger 번호, 보드 시계, 그 앞의 마지막 주기 trigger 번호, 그 trigger 또는 run 시작 이후의 시계 사이클 수, n번 비트가 채널 n인 scintillator 패턴), `TICK`, `END`입니다. 보드 시계는 run 시작부터 100.5 MHz 사이클을 세며, `TICK`은 `frame_ms` 길이의 frame을 끝냅니다. 주기 trigger는 아래 오프라인 frame builder가 쓰는 논리적 frame 경계이며, 모든 stream의 frame은 지금도 각자의 `TICK`에서 끝납니다. `python3 -m msensor.boardstream verify FILE`은 보드 stream을 검사하며 완전하면 0, 불완전하면 2, 손상되었으면 3을 반환합니다. DQM은 보드를 보여 주지 않습니다.

오프라인 frame builder는 한 run의 stream을 시간 frame으로 묶습니다. `python3 -m nestdaq_mode.framebuilder --dir DIR --out frames.jsonl`로 실행하며, DIR에는 추출한 `*.msraw` 파일이 있어야 합니다. FileSink run 파일은 `--dat FILE --work DIR`로 먼저 추출할 수 있습니다. frame f는 보드 사이클 f × `periodic_clk`부터 (f + 1) × `periodic_clk`까지이며 f번째 주기 trigger에서 끝납니다. 출력의 각 줄이 frame 하나이며, 그 frame의 물리 trigger와 msensor별 window, tile, hit 수를 담습니다. `--hits`를 주면 hit도 씁니다. msensor의 window는 그 msensor의 TRIGGER 레코드로 알아낸 자기 시계 기준 시작 시각의 frame에 속하므로, 시계 오프셋과 drift가 데이터를 다른 frame으로 옮기지 않습니다. msensor가 놓친 주기 trigger는 시각으로 짝을 맞추고 그 경계를 보간하며, 그 frame에는 `estimated_border`가 표시됩니다. 시계 오프셋이 주기의 절반보다 작아야 합니다. 마지막 주기 trigger 뒤의 데이터는 표준 출력의 요약에만 세고 쓰지 않습니다. 종료 코드는 완전하면 0, 입력을 쓸 수 없으면 2, 불완전하면 3입니다.

## 끝내기

`scripts/session_control.sh NAME nestdaq stop`으로 run을 멈춥니다. run이 멈추기 전에 남은 데이터를 저장합니다. `docker cp NAME:/tmp/session-run ./session-output`으로 출력을 복사하고 `docker rm -f NAME`으로 컨테이너를 지우면 저장 파일이 `session-output/nestdaq/data/`에 있습니다. `terminate`와 `T`, `Q` 키는 EUDAQ RunControl에 속하며 nestDAQ 세션은 `terminate`를 거부합니다. 페이지에서는 nestDAQ 탭이 run을 멈추고 실험 탭이 출력을 복사하고 컨테이너를 지웁니다.

## 오류와 한계

실행기는 2초 후 다시 시작합니다. DQM이 없어도 run이 진행되며 DQM은 새 세션에서만 돌아옵니다. 누락된 frame과 버린 DQM 사본을 셉니다. 이 모드에는 정렬과 분석이 없습니다. FileSink는 STOP 후에 도착한 메시지를 버립니다. 터널은 연결의 첫 요청만 검사합니다. nestDAQ 모드는 arm64와 x86_64 Linux 및 arm64 macOS에서 확인되었습니다.

각 입력 장치에서 DQM으로 가는 대기열에는 frame이 몇 개만 들어갑니다. DQM이 뒤처지면 입력 장치가 DQM 사본을 버리고 `dqm_dropped`로 세므로, 느린 DQM이 메모리를 다 쓰거나 저장 데이터를 늦추지 않습니다.
