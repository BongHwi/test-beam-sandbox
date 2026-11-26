[English](../setup-file.md) | [Korean](../ko/setup-file.md) | [Japanese](../ja/setup-file.md)

# 셋업 파일

셋업 파일은 세션 하나를 설명합니다: 어떤 장치를 어디에 두는지, 장치 설정, run 길이와 빔. `[section]` 헤더와 `item = value` 줄로 된 텍스트 파일이며 `#`로 시작하는 줄은 주석입니다. 적지 않은 항목은 아래의 기본값을 씁니다. `configs/setups/default.ini`가 기본 셋업입니다. 다른 run 길이를 쓰려면 이 파일을 복사해 `[run]`의 `events`를 바꿉니다.

![결과 페이지의 셋업 탭에서 연 셋업 파일입니다.](../images/page-setup.png)

`configs/setups/default.ini`는 ALPIDE 기준면과 msensor DUT를 씁니다. `configs/setups/small-msensor.ini`는 더 작은 msensor DUT를 씁니다. `configs/setups/msensor-telescope.ini`는 msensor 기준면 여섯 개와 msensor DUT 하나를 씁니다. 네 셋업 `configs/setups/nestdaq.ini`, `nestdaq-clocks.ini`, `nestdaq-missed.ini`, `nestdaq-telescope.ini`는 nestDAQ 모드를 사용합니다([nestDAQ 모드](nestdaq.md)).

## 명령

- `scripts/setup_session.sh new FILE`은 기준면의 수와 간격, DUT의 종류와 위치, run 길이와 빔 에너지를 묻습니다. `--reference-planes`, `--spacing-mm`, `--dut`, `--dut-z`, `--events`, `--energy-gev`, `--rate-hz`, `--alpide-ithr`, `--msensor-threshold`, `--msensor-readout` 같은 옵션으로 답을 정하고 `--no-input`으로 질문을 건너뜁니다. 이미 있는 파일은 `--force`가 있을 때만 바꿉니다.
- `scripts/setup_session.sh show FILE`은 빔 순서의 장치와 위치, 주요 설정, run 길이, 빔, 그리고 빔 축을 따라 옆에서 본 그림을 출력합니다.
- `scripts/setup_session.sh check FILE`은 잘못된 항목마다 받는 값을 알려 주고 0이 아닌 코드로 끝납니다. QA가 가능한지도 알려 줍니다.
- `scripts/start_session.sh --setup FILE`은 셋업을 검사하고 세션 파일을 생성해 시작합니다. 세션은 셋업을 받은 그대로(`setup-source.ini` 또는 `setup-source.json`), 그리고 `setup.json`으로 `/tmp/session-run`에 남깁니다. 정렬과 QA는 그 사본의 장치를 씁니다.
 `--scintillators 0`은 판을 빼고 `--scintillators 2`(기본)는 기본 판 두 개를 넣습니다.


nestDAQ에서는 `[run] daq = nestdaq`을 설정하고 `[nestdaq] input = file | socket`, `frame_ms`, `send_buffer_mb`를 선택합니다. `send_buffer_mb`는 socket 입력에만 적용됩니다. 각 `[msensor N]`은 `clock_offset_ns`와 `clock_drift_ppm`으로 자기 시계를, `missed_triggers`로 놓치는 trigger를, `noise`로 노이즈 hit를 설정할 수도 있습니다. `trigger_board = on`과 `periodic_clk`(100.5 MHz 보드 시계의 사이클 수)는 가상 trigger 보드를 더합니다. [nestDAQ 모드](nestdaq.md)를 참조합니다.

## 항목

| 절 | 항목 | 기본값 | 받는 값 |
|---|---|---|---|
| `[run]` | `events` | `60000` | 다음 run이 시작되기 전까지의 run당 event 수, `1`–`1000000000` |
| `[beam]` | `particle` | `e-` | `e-` |
| `[beam]` | `energy_gev` | `4` | `1`–`6` |
| `[beam]` | `profile` | `gaussian` | `gaussian`, `kek-2gev`, `kek-4gev` 중 하나 |
| `[beam]` | `position_mm` | `1.1, -2.1, -20` | 명목 x, y와 source z, source는 모든 장치보다 상류 |
| `[beam]` | `spread_mm2` | `25, 25` | x와 y의 명목 분산, `0.000001`–`10000` |
| `[beam]` | `rate_hz` | `220` | 명목 rate, `1`–`1000` |
| `[positions]` | `offset_max_mm` | `5` | 장치마다 감춰진 x, y 어긋남의 최댓값, `0`–`10` mm, [정렬](alignment.md) 참조 |
| `[positions]` | `offset_draw` | `0` | 다른 수는 같은 장치에 다른 어긋남을 뽑음, `0`–`1000000` |
| `[alpide N]`, `[msensor N]` | `plane` | 필수 | 고유, `0`–`63` |
| `[alpide N]`, `[msensor N]` | `position_mm` | 필수 | 센서 중심의 x, y, z |
| `[alpide N]` | `pitch_um` | `30, 30` | `1`–`1000` |
| `[alpide N]` | `pixels` | `1024, 512` | `1024, 512` |
| `[alpide N]` | `ithr` | `0` | `0`–`255` |
| `[alpide N]` | `strobe_length` | `40` | `1`–`4000`, 25 ns 단위 |
| `[msensor N]` | `role` | `dut` | `dut` 또는 `reference`; 기준면은 `triggered` 읽기가 필요 |
| `[msensor N]` | `pitch_um` | `23, 21` | `1`–`1000` |
| `[msensor N]` | `tiles` | `6, 2` | x와 y 방향의 타일 수, `1`–`64` |
| `[msensor N]` | `tile_pixels` | `156, 444` | 타일 하나의 열과 행 수, matrix는 한 변에 최대 `4096` |
| `[msensor N]` | `tiles_off` | 비어 있음 | 끌 타일 번호 |
| `[msensor N]` | `pixels` | 없음 | `tiles`와 `tile_pixels` 대신 쓰는 타일 하나짜리 matrix |
| `[msensor N]` | `threshold_dac` | `100` | `0`–`255`, 값 하나 또는 타일마다 하나 |
| `[msensor N]` | `readout` | `continuous` | `continuous` 또는 `triggered` |
| `[msensor N]` | `window_length_ns` | `1000000` | `1000`–`10000000`, continuous 읽기 전용 |
| `[msensor N]` | `strobe_delay_ns` | `0` | `0`–`1000000`, triggered 읽기 전용 |
| `[msensor N]` | `strobe_length_ns` | `2000` | `1`–`1000000`, triggered 읽기 전용 |

`N`은 장치 종류마다 `0`부터 빠짐없이 셉니다. ALPIDE는 기준면이고 msensor는 `role`을 따릅니다. 빔의 위치, 퍼짐과 rate는 명목값입니다. 세션마다 비공개로 조금씩 달라지므로 분석에서 실제 빔을 찾아야 합니다. 유한 예제(`--example`)는 source가 끝나면 종료되며 `events`를 쓰지 않습니다.

세션은 전자 하나하나를 빔 source에서 면들을 지나 일정한 운동량으로 따라갑니다. 모든 면(면마다 실리콘 100 µm)과 source에서 마지막 면까지의 공기에서 Highland 공식에 따른 가우스 분포의 산란각을 더해 다중 산란을 넣습니다. 에너지 손실, 제동 복사, 이차 입자는 시뮬레이션하지 않습니다. 신틸레이터는 trigger만 정하고 물질을 더하지 않습니다.

`profile`은 빔의 각도를 정합니다. 기본값인 `gaussian`은 모든 전자를 빔 축 방향으로 보냅니다. `kek-2gev`와 `kek-4gev`는 KEK 빔 테스트에서 2 GeV와 4 GeV로 잰 폭의 가우스 분포 각도를 더하며, 빔 축에서 멀어질수록 각도가 조금 커지는 것도 넣습니다. 평균 각도는 0입니다. 위치의 퍼짐은 그대로 `spread_mm2`가 정합니다. 세션은 각도를 `energy_gev`에 맞춰 바꾸지 않으므로 프로파일은 잰 에너지에서 씁니다. 각도의 퍼짐은 x와 y 각각 7.5 mrad까지만 됩니다. `check`는 이를 넘는 `profile`과 `spread_mm2`를 거절합니다.

`--setup`으로 시작해 실행 중인 세션에서는 run 길이와 장치 설정 `ithr`, `strobe_length`, `threshold_dac`, `readout`, `window_length_ns`, `strobe_delay_ns`, `strobe_length_ns`를 `scripts/session_control.sh NAME set`으로 다음 run부터 바꿀 수 있습니다([운영자 화면 설명](operator-screen.md)). 다른 항목은 세션을 시작한 그대로입니다.

기본 msensor는 `6 x 2` 타일, 타일마다 `156 x 444` 픽셀, pitch `23 x 21` µm이며 [msensor 문서](msensor.md)에 설명이 있습니다. 타일 번호는 열 `0`, 행 `0`에서 `0`으로 시작해 x 방향으로 먼저 셉니다. 기본 격자에서 타일 `t`는 타일 열 `t mod 6`, 타일 행 `t div 6`입니다. 끈 타일은 hit도 잡음도 내지 않습니다. 기본 빔(분산 `25, 25` mm², x와 y 모두 sigma `5` mm)은 모든 타일에 닿을 만큼 넓습니다. 한 측정에서 기본 [trigger](trigger.md)를 쓴 event의 `70.3` %에 track이 있었습니다. `configs/setups/small-msensor.ini`는 예전의 타일 하나짜리 `128 x 128` msensor(pitch `30` µm), 좁은 빔 `0.003, 0.004`이며 위치 어긋남이 없습니다.

| 절 | 항목 | 기본값 | 받는 값 |
|---|---|---|---|
| `[scintillator N]` | `channel` | 필수 | 중복 없는 `0`–`3` |
| `[scintillator N]` | `position_mm` | 필수 | 판 중심 x, y, z (mm); 빔 source보다 하류 |
| `[scintillator N]` | `shape` | `rectangle` | `rectangle` 또는 `disc` |
| `[scintillator N]` | `size_mm` | `30, 20` | 직사각형 폭과 높이 또는 원판 지름, `1`–`200` mm |
| `[scintillator N]` | `hole_mm` | `0` | 중앙 구멍의 폭과 높이 또는 지름, 판보다 작음 |
| `[trigger]` | `trg` | `trg0&trg3&dt_trg>10000&dt_veto>5000&!bsy` | 있는 channel을 쓰는 비어 있지 않은 trigger 식 |
| `[trigger]` | `veto` | `ntrg>0` | trigger 식; 비우면 veto 없음 |
| `[trigger]` | `count` | `1xxR Rxx1 xxxR Rxxx` | 네 글자로 된 계수 조건 하나에서 열여섯 개 |

## QA

QA는 기준면으로 track을 재구성합니다. 기준면이 적어도 3개 필요합니다. track은 5개 면의 hit가 필요하며, 기준면이 6개보다 적으면 기준면 수보다 하나 적은 수가 필요하지만 3보다 적지는 않습니다. msensor DUT가 없으면 QA는 track 수만 보고합니다. 끈 타일은 분석에서 mask되므로 효율은 켜진 타일만으로 계산합니다. QA는 끈 타일을 한 줄로 알립니다. 유한 예제에는 msensor가 필요합니다. 예제 source가 끝났다는 것을 msensor producer가 알리기 때문입니다.

## 셋업 파일에 없는 것

가상 장치가 반응하지 않는 설정은 두지 않습니다: `VCASN`, `RDOMODE`, `minspacing`, `fixedbusy` 같은 다른 ALPIDE 레지스터, msensor의 `regions`(타일별 threshold가 대신합니다)와 window guard, 내부 source와 clock 설정. 빔 방향과 비공개 변동은 세션 안에 감춰 둡니다.

## track 해상도

`scripts/setup_session.sh show`와 셋업 탭의 요약은 각 DUT에서 track 위치를 얼마나 정확히 아는지 추정합니다. 추정은 이 sandbox의 시뮬레이션과 분석을 따릅니다.

- 축마다의 고유 해상도. msensor 면은 픽셀 하나에만 hit를 내므로 해상도가 pitch/√12입니다. `6.640` x `6.062` µm이며 기본 pitch는 `23 x 21` µm입니다. ALPIDE 면은 입자가 픽셀 경계 주변의 띠 안을 지나면 양쪽 픽셀 모두에 hit를 냅니다. 이 띠는 `ithr`가 낮을수록 넓어지므로(cluster 평균 `2 − ithr/255`) pitch `30` µm의 해상도는 `4.517` µm (`ithr = 0`), `8.660` µm (`ithr = 255`)입니다.
- 모든 면(실리콘 100 µm, `0.00106724` X0)과 빔 source에서 마지막 면까지의 공기에서 일어나는 다중 산란. Highland 공식을 쓰며 로그 항은 시뮬레이션처럼 전체 물질에서 한 번 정합니다.
- 분석처럼 기준면을 지나는 직선을 고유 해상도로 가중해 맞춥니다. DUT는 fit에 들어가지 않습니다.

`show`는 DUT마다 x와 y의 track 해상도(DUT에서 맞춘 위치의 퍼짐)와 예상 residual(track과 DUT 해상도를 합친 것)을 보여 줍니다. 4 GeV의 `default.ini`에서 추정은 track `2.97 x 2.97` µm, residual `7.27 x 6.75` µm이고, 한 run에서 잰 residual은 `7.21 x 6.84` µm였습니다. 이 추정은 이 시뮬레이션을 설명하며 실제 telescope를 설명하지 않습니다. 면과 공기를 합한 물질은 시뮬레이션의 한계인 `0.01998` X0까지만 됩니다. `check`는 이를 넘는 셋업을 거절합니다.

셋업 탭은 Magnus Mager(CERN)의 외부 telescope optimiser도 x 또는 y 중 한 축으로 열며 인터넷이 필요합니다. 링크는 빔 운동량 `sqrt(T² + 2 T m)` GeV/c (`T`는 운동 에너지, `m = 0.00051099895` GeV)와 z 순서의 장치 면을 전달합니다. 각 면의 해상도는 선택한 축의 위 고유 해상도이고 물질은 100 µm 실리콘, 즉 0.107 % X/X0입니다. DUT는 측정 없이 산란만 하는 면이며 신틸레이터는 넣지 않습니다. 장치 앞뒤 10 mm의 끝점에는 물질과 측정이 없습니다. optimiser는 최적 fit을 쓰고 공기를 넣지 않으므로 값이 `show`의 추정보다 조금 작을 수 있습니다.

[기본 셋업의 x 축 열기](https://mmager.web.cern.ch/telescope/tracking.html#part=e&p=4.0005&x=[-1,0,2,4,6,8,10,12,13]&XX0=[,0.00107,0.00107,0.00107,0.00107,0.00107,0.00107,0.00107,]&sy=[,0.000452,0.000452,0.000452,0.000664,0.000452,0.000452,0.000452,]&en=[0,1,1,1,0,1,1,1,0])

