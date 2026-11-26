[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md)

# Test-beam sandbox

이 저장소는 빔 테스트의 데이터 취득과 분석을 연습하는 환경입니다. 모의 빔이 가상 픽셀 센서로 이루어진 telescope(기준면)와 시험할 합성 픽셀 센서(msensor, DUT: 시험 대상 장치)를 통과합니다. 장치, 설정과 빔은 셋업 파일 하나에 지정하며 빔은 데이터 취득과 독립적으로 켜고 끌 수 있습니다.

세션은 셋업 파일에서 고른 두 공개 데이터 취득 프레임워크 중 하나로 데이터를 취득합니다. 기본값인 EUDAQ는 Corryvreckan으로 정렬, track, DUT 효율을 다루고, nestDAQ는 모든 msensor를 stream으로 읽으며 가상 trigger 보드를 더할 수 있습니다. 둘의 비교는 [DAQ 모드](docs/ko/daq-modes.md)에 있습니다.

사람이 취득과 분석 절차를 익히거나, 취득을 조작하는 자동화 프로그램을 개발하고 시험할 때 사용할 수 있습니다. 모든 응답은 합성값이며 실제 장비의 측정값이 아닙니다.

![run 중 결과 페이지의 실험 탭: 빔을 따라 3D로 놓인 장치와 켜진 빔, run 상태](docs/images/experiment-view.png)

## 필요한 것

Linux 컨테이너를 실행하는 Docker가 동작하는 Linux 또는 macOS, Python 3와 40 GiB 이상의 여유 공간이 필요합니다. 빌드에는 인터넷이 필요하며 세션은 network 없이 실행합니다. 빌드는 Docker daemon의 아키텍처(arm64 또는 amd64)를 선택하며 최대 4개 작업과 8 GiB 메모리를 사용합니다. 호스트에서는 Python 3.9 이상으로 추가 패키지 없이 결과 페이지와 nestDAQ 모드의 추출 도구 및 frame builder를 실행합니다. macOS에서 처음 `python3`을 실행할 때 명령줄 개발자 도구 설치를 요청할 수 있습니다.

## 빠른 시작

명령은 모두 저장소 맨 위 디렉터리에서 실행합니다. 첫 빌드는 EUDAQ와 Corryvreckan을 컴파일하므로 시간이 걸립니다(24코어 arm64 컴퓨터에서 약 4분). 이후 세션은 이미지를 재사용합니다.

```sh
# 코드를 받습니다.
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# 공개 기반 이미지와 세션 이미지를 빌드합니다(한 번).
scripts/build_session.sh

# 기본 셋업으로 세션을 시작하고 공개 RunControl 화면을 보여 줍니다.
scripts/start_session.sh --setup configs/setups/default.ini --name public-session

# 두 번째 터미널에서 모의 빔을 켜고, 현재 run을 정렬하고 분석합니다.
scripts/session_control.sh public-session on
scripts/align_session.sh public-session
scripts/analyze_session.sh public-session
```

세션을 끝내는 방법, nestDAQ 모드(`scripts/build_session.sh --nestdaq`)와 브라우저는 [따라 하기](docs/ko/walkthrough.md)에서 이어집니다.

## 설명서

### 운영

- [따라 하기](docs/ko/walkthrough.md): 터미널(EUDAQ, nestDAQ)과 브라우저에서 세션을 단계별로 따라 합니다.
- [DAQ 모드](docs/ko/daq-modes.md): EUDAQ 모드와 nestDAQ 모드의 비교, 그리고 샌드박스와 실제 빔 테스트의 대응.
- [참조](docs/ko/reference.md): 켜져 있는 것, 스크립트, 각 부분이 맞물리는 방식과 저장소 구성.
- [운영 화면](docs/ko/operator-screen.md): 창과 키, 빔과 run 명령, QA 출력과 결과 파일.
- [셋업 파일](docs/ko/setup-file.md): 장치, 설정, run 길이, 빔과 `scripts/setup_session.sh`.
- [nestDAQ 모드](docs/ko/nestdaq.md): nestDAQ로 msensor를 읽는 세션의 셋업, trigger 보드(선택 사항), run, 화면, 데이터, frame builder와 한계.
- [정렬](docs/ko/alignment.md): 장치의 위치를 왜, 어떻게 찾는지.
- [결과 페이지](docs/ko/qa-page.md): 브라우저 페이지.
- [trigger](docs/ko/trigger.md): trigger 신틸레이터와 trigger 조건.
- [msensor](docs/ko/msensor.md): 끌 수 있는 타일로 나뉜 합성 DUT와 데이터·stream 형식. 기본 타일 배치는 `6 x 2` 타일, 타일마다 `156 x 444` 픽셀, pitch `23 x 21` µm입니다.
- [자동 실행 예](docs/ko/automation.md): 프로그램을 위해 화면 없이 도는 세션과 상태 파일의 상태.
- [프로그램과 에이전트](docs/ko/programs-and-agents.md): 프로그램이나 에이전트가 연결할 수 있는 곳.


### 내부 구성

- [세션 source와 빔 제어](docs/ko/session-core.md): 공유 모의 빔, 타이밍, 장치 조정과 독립적인 on/off 조작.
- [가상 ALPIDE 보드](docs/ko/virtual-alpide-board.md): 공개 producer가 사용하는 보드 인터페이스.
- [가상 trigger 보드](docs/ko/trigger-board-surface.md): 공개 producer의 조건과 계수기.
- [공개 보드 호출 표면](docs/ko/alpide-board-surface.md): 공개 producer가 사용하는 보드 인터페이스.
- [RunControl 확장](docs/ko/runcontrol-hook.md): 상태 파일과 제어 socket.
- [세션 취득과 분석](docs/ko/session-run.md): 배치, 만들어지는 파일, 분석.
- [tracksim](docs/ko/tracksim.md): 세션 코어의 입자 수송.
- [공개 스택과 소스 버전](docs/ko/public-stack.md): 공개 소프트웨어 버전과 patch.

## 라이선스

Copyright (C) 2026 Bong-Hwi Lim (CNS, the University of Tokyo)

이 저장소는 `LGPL-3.0-or-later`로 배포합니다. LGPL은 GPL 본문에 조건을 더하는 형태이므로 전문은 [COPYING.LESSER](COPYING.LESSER)와 [COPYING](COPYING)에 있습니다. EUDAQ (`LGPL-3.0`)와 Corryvreckan (`MIT`)은 빌드할 때 받아 오며 이 저장소에는 그 사본이 들어 있지 않습니다. `runcontrol/ITS3RunControl.patch`와 `runcontrol/ITS3start.patch`는 EUDAQ에, `docker/base/corry-root-eigen.patch`는 Corryvreckan에 적용되며, 각 patch에는 적용 대상 소프트웨어의 줄이 들어 있습니다. 결과 페이지는 JSROOT `7.5.2`(`MIT`)로 그리고, 실험 화면의 3D는 그 JSROOT에 든 three.js `r155`(`MIT`)로 그립니다. JSROOT는 세션 이미지에 ROOT와 함께 들어 있으며 페이지 서버가 이미지에서 꺼내 쓰고, 이 저장소에는 그 사본이 없습니다.

nestDAQ 모드에서만 빌드하는 nestDAQ 이미지는 빌드 중 추가 공개 소프트웨어를 내려받습니다. [공개 스택과 소스 버전](docs/ko/public-stack.md)을 참조합니다.
