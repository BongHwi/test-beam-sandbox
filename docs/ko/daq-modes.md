[English](../daq-modes.md) | [Korean](../ko/daq-modes.md) | [Japanese](../ja/daq-modes.md)

# DAQ 모드

세션은 셋업 파일에서 고른 두 공개 데이터 취득 프레임워크 중 하나로 데이터를 취득합니다:
- EUDAQ (`[run] daq = eudaq`, 기본값)는 공개 EUDAQ로 가상 ALPIDE 기준면, msensor와 가상 trigger 보드를 읽습니다. ALPIDE producer, trigger producer와 data collector는 그대로 사용하고 RunControl은 이 저장소의 상태·제어 확장과 함께 실행합니다. [공식 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`에서 빌드한 공개 분석 소프트웨어 Corryvreckan은 평면을 정렬하고 입자의 track을 재구성하며 DUT가 이를 얼마나 효율적으로 검출하는지 측정합니다.
- nestDAQ (`[run] daq = nestdaq`)는 nestDAQ 장치로 모든 msensor를 continuous 또는 triggered stream으로 읽습니다. msensor마다 입력 장치가 있고 FileSink가 frame을 저장하며 모니터(DQM)가 hit map을 채우고 msensor의 trigger를 짝짓습니다. daq-webctl은 run을 제어하고 SlowDash는 시계열을 보여 줍니다. 가상 trigger 보드(`trigger_board = on`, 선택 사항)는 모든 msensor에 주기 trigger를 보내 시간 frame의 경계를 표시하고, 빔라인의 물리 trigger는 별도의 보드 stream에 기록합니다.

## 비교

두 모드는 같은 모의 빔, 셋업 파일, 스크립트와 결과 페이지를 사용합니다. 셋업 파일에서 모드를 고릅니다:

| | EUDAQ 모드 | nestDAQ 모드 |
|---|---|---|
| 셋업 파일 | `[run] daq = eudaq` (기본값) | `[run] daq = nestdaq` |
| 이미지 | `scripts/build_session.sh` | 추가로 `scripts/build_session.sh --nestdaq` |
| `configs/setups/`의 예제 셋업 | `default.ini`, `small-msensor.ini`, `msensor-telescope.ini` | `nestdaq.ini`, `nestdaq-clocks.ini`, `nestdaq-missed.ini`, `nestdaq-telescope.ini`, `nestdaq-board.ini` |
| 읽는 장치 | ALPIDE 기준면, msensor와 trigger 보드 | msensor; ALPIDE 평면은 빔 안에 물질로 남습니다 |
| Trigger | 신틸레이터와 셋업 파일의 trigger 조건 | 모의 빔라인의 trigger; `trigger_board = on`이면 신틸레이터와 `[trigger]`를 쓰고, 주기 trigger를 내는 가상 trigger 보드가 더해집니다 |
| Run 제어 | 공개 RunControl 화면과 `scripts/session_control.sh` | daq-webctl과 `scripts/session_control.sh NAME nestdaq` |
| 모니터링 | RunControl 화면; 페이지의 실험 및 DAQ 화면 탭 | DQM과 SlowDash; 페이지의 nestDAQ와 QA 탭 |
| 저장 데이터 | EUDAQ raw 파일 | FileSink 파일의 msraw frame; `python3 -m nestdaq_mode.extract`가 msensor마다, 그리고 trigger 보드의 msraw stream을 별도 파일에 씁니다 |
| 분석 | Corryvreckan으로 정렬과 QA | 정렬은 아직 없습니다; `python3 -m nestdaq_mode.framebuilder`가 주기 trigger마다 시간 frame을 오프라인으로 만듭니다; DQM은 hit map, correlation과 trigger 짝짓기를 보여 주며 `nestdaq qa`와 QA 탭은 그 숫자를 출력합니다 |

## 실제 빔 테스트에서는

| 단계 | 샌드박스에서 | 실제 빔 테스트에서 |
|---|---|---|
| 셋업 | 셋업 파일 하나 | 장치를 설치하고 EUDAQ 설정 파일을 준비합니다 |
| 취득 | `scripts/start_session.sh`가 공개 RunControl 화면을 보여 줍니다 | RunControl과 producer를 띄우고 그 화면에서 조작합니다 |
| nestDAQ 취득 | `scripts/session_control.sh NAME nestdaq start`와 `stop`, 또는 daq-webctl | nestDAQ 장치를 시작하고 daq-webctl로 run을 제어합니다 |
| 빔 | `scripts/session_control.sh NAME on`, `off` | 가속기에서 옵니다 |
| Trigger | 셋업 파일의 신틸레이터와 조건, 가상 보드와 공개 trigger producer | trigger 보드가 있는 신틸레이터, trigger producer가 조건을 설정합니다 |
| 정렬과 분석 | `scripts/align_session.sh`, `scripts/analyze_session.sh` | Corryvreckan을 설정 파일로 실행합니다 |
| 결과 | QA가 작성한 `histograms.root` | ROOT 파일을 엽니다 |
