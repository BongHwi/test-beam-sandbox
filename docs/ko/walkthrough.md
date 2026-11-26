[English](../walkthrough.md) | [Korean](../ko/walkthrough.md) | [Japanese](../ja/walkthrough.md)

# 따라 하기

이 안내는 빌드부터 컨테이너를 지울 때까지 세션을 따라갑니다: 터미널의 EUDAQ, 터미널의 nestDAQ, 또는 브라우저. README의 [빠른 시작](../../README.ko.md#빠른-시작)이 가장 짧은 경로입니다.

## 이미지 빌드

코드를 받고 이미지를 한 번 빌드합니다. 명령은 모두 저장소 맨 위 디렉터리에서 실행합니다:

```sh
# 코드를 받습니다.
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# 공개 기반 이미지와 세션 이미지를 빌드합니다(한 번).
scripts/build_session.sh

# nestDAQ 모드에서는 nestDAQ 이미지도 빌드합니다(한 번).
scripts/build_session.sh --nestdaq
```

첫 빌드는 EUDAQ와 Corryvreckan을 컴파일하므로 시간이 걸립니다. 24코어 arm64 컴퓨터에서 약 4분, nestDAQ 이미지는 약 10분이 더 걸렸습니다. 이후 세션은 이미지를 재사용합니다.

그다음 터미널이나 브라우저에서 EUDAQ 또는 nestDAQ 경로를 이용합니다. 모든 경로는 `public-session`이라는 세션 컨테이너 하나를 사용하며 브라우저의 각 버튼은 터미널과 같은 명령을 실행하므로 두 경로를 섞어 쓸 수 있습니다.

#### 터미널에서 EUDAQ 세션

##### 1. 취득

기본 셋업으로 세션을 시작합니다. 다른 셋업은 `configs/setups/`에 있으며 [셋업 파일 설명](setup-file.md)을 참조합니다. RunControl, producer, data collector와 모의 빔이 `public-session`이라는 컨테이너 하나에서 돌고, 이 터미널에 공개 RunControl 화면이 나옵니다.

```sh
# 세션을 시작하고 공개 RunControl 화면을 보여 줍니다.
scripts/start_session.sh --setup configs/setups/default.ini --name public-session
```

세션은 빔이 꺼진 채 시작합니다. 두 번째 터미널에서 모의 빔을 켜면 RunControl 화면의 event 수가 늘기 시작합니다.

```sh
# 모의 빔을 켭니다.
scripts/session_control.sh public-session on
```

run이 끝나면 `T`로 시퀀스를 끝낼 때까지 RunControl이 다음 run을 시작합니다. 아무것도 멈추지 않고 화면을 떠나려면 `Ctrl-b`를 누르고 `d`를 누릅니다. `scripts/attach_session.sh public-session`으로 화면을 다시 볼 수 있습니다.

![세션의 공개 RunControl 화면: run 진행 상황, producer와 collector](../images/runcontrol-screen.png)

run 중인 세션의 RunControl 화면입니다. 세션은 [EUDAQ의 ITS3 RunControl](https://github.com/eudaq/eudaq/blob/bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e/user/ITS3/python/ITS3RunControl.py)을 사용합니다. 창의 키는 [운영 화면 안내](operator-screen.md)에 있습니다.

##### 2. 분석

셋업 파일의 위치는 측량한 값입니다. 실제 위치는 설치한 뒤처럼 최대 `5` mm 다르고 감추어져 있으므로, QA가 Corryvreckan으로 track을 재구성하고 DUT 효율을 재기 전에 정렬이 데이터에서 위치를 찾습니다. 그동안 취득은 계속됩니다([정렬 안내](alignment.md)). 정렬은 현재 run의 데이터 event가 `3000`개가 될 때까지 기다립니다.

```sh
# 현재 run의 데이터에서 장치의 위치를 찾습니다.
scripts/align_session.sh public-session

# 현재 run을 분석합니다: event, track, DUT 효율.
scripts/analyze_session.sh public-session
```

QA는 다음과 같은 줄로 끝납니다(숫자는 달라집니다):

```text
  Events: Ev: 10.1k Px: 113.3k Tr: 7.1k (0.703/ev) t = 47.6141s
  [F:AnalysisEfficiency:MSENSOR_6] Total efficiency of detector MSENSOR_6: 98.4446(+0.149964 -0.164889)%, measured with 6709/6815 matched/total tracks
```

정렬은 찾은 보정량과 정렬 전후의 event당 track 수를 출력합니다. QA는 event 수, track 수, DUT마다의 효율을 출력합니다. 첫 줄은 컨테이너 안의 결과 디렉터리를 알려 주며, 그 안의 `analysis/output/histograms.root`에 histogram이 있습니다. 둘 다 쓴 Corryvreckan 설정을 알려 줍니다([운영 화면 안내](operator-screen.md)). DUT는 DAQ에서 producer 이름을 따라 `MSENSOR_0`, 분석에서는 plane 번호를 따라 `MSENSOR_6`으로 불립니다.

##### 3. 끝내기

RunControl 화면에서 `T`를 눌러 시퀀스를 끝내고, 그다음 `Q`를 눌러 RunControl을 나갑니다. 화면에는 컨테이너의 shell이 나오며 `Ctrl-b`를 누르고 `d`를 누르면 나갑니다. 다른 터미널에서 `scripts/session_control.sh public-session terminate`를 실행하면 `T`와 같은 요청을 보냅니다. 화면이 다시 그려질 때 누른 키가 공개 RunControl을 멈출 수 있으므로 프로그램은 이 명령을 사용해야 합니다([운영 화면 안내](operator-screen.md)).

```sh
# 출력을 호스트로 복사합니다.
docker cp public-session:/tmp/session-run ./session-output

# 세션 컨테이너와 그 안의 모든 것을 지웁니다.
docker rm -f public-session
```

창을 옮기는 키, `S`와 `R`, 떼었다 다시 붙기, 빔 기록은 [운영 화면 안내](operator-screen.md)에, run 길이는 [셋업 파일 설명](setup-file.md)에 있습니다.

#### 터미널에서 nestDAQ 세션

##### 1. 취득

nestDAQ 셋업으로 세션을 시작하면 셋업 파일에서 nestDAQ 이미지를 고릅니다. nestDAQ 프로세스는 컨테이너의 백그라운드에서 실행되므로 같은 터미널에서 빔을 켜고 run을 시작할 수 있습니다. `scripts/attach_session.sh public-session`은 msensor 실행기를 보여 주며 `Ctrl-b`를 누르고 `d`를 누르면 화면을 떠납니다.

```sh
# 화면을 표시하지 않고 nestDAQ 세션을 시작합니다.
scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach

# 모의 빔을 켭니다.
scripts/session_control.sh public-session on

# run을 시작합니다. 응답은 run 번호가 든 JSON입니다.
scripts/session_control.sh public-session nestdaq start
```

가상 trigger 보드를 더하려면 `configs/setups/nestdaq-board.ini`로 시작하거나 `[nestdaq]`에 `trigger_board = on`과 `periodic_clk`를 설정합니다. 그러면 모든 msensor가 100.5 MHz 보드 시계로 `periodic_clk` 사이클마다 주기 trigger를 받고, `TRIGGERBOARD_0` 실행기가 주기 trigger와 물리 trigger를 각각의 신틸레이터 패턴과 함께 별도의 stream에 저장합니다. 이 설정이 없으면 세션은 이전과 똑같이 동작합니다.

##### 2. 모니터링

상태는 run, 장치와 msensor마다 보낸 frame 및 버린 DQM 사본을 보여 줍니다. 페이지의 nestDAQ 탭은 이것과 실행기마다 쓴 frame을 보여 주고 daq-webctl, SlowDash와 DQM 링크를 제공합니다. `scripts/qa_page.sh public-session`으로 페이지를 엽니다. `nestdaq qa`는 DQM의 숫자를 출력하고 페이지의 QA 탭도 이를 보여 줍니다. DQM hit map과 trigger 짝짓기는 [nestDAQ 모드](nestdaq.md)에 설명되어 있습니다.

```sh
# run, 장치와 msensor stream을 보여 줍니다.
scripts/session_control.sh public-session nestdaq status --json

# DQM의 frame, trigger, hit와 trigger 짝짓기 숫자를 출력합니다.
scripts/session_control.sh public-session nestdaq qa
```

![결과 페이지의 nestDAQ 탭: run 제어, 장치, msensor stream과 화면 링크](../images/page-nestdaq.png)

##### 3. 끝내기

run을 멈추면 남은 데이터가 run이 멈추기 전에 저장되며 다음 `nestdaq start`는 새 run을 시작합니다. 출력을 복사하고 컨테이너를 지운 뒤 이 컴퓨터에서 각 msensor와 trigger 보드의 msraw stream을 추출합니다. trigger 보드를 사용한 경우 시간 frame도 만듭니다. 각 도구는 JSON 요약을 출력합니다.

```sh
# run을 멈춥니다.
scripts/session_control.sh public-session nestdaq stop

# 출력을 호스트로 복사합니다.
docker cp public-session:/tmp/session-run ./session-output

# 세션 컨테이너와 그 안의 모든 것을 지웁니다.
docker rm -f public-session

# run 1에서 각 msensor와 trigger 보드의 msraw stream을 추출합니다.
python3 -m nestdaq_mode.extract session-output/nestdaq/data/run000001.dat extracted

# trigger 보드를 사용한 경우: 주기 trigger마다 시간 frame을 만듭니다. frame당 JSON 한 줄입니다.
python3 -m nestdaq_mode.framebuilder --dir extracted --out frames.jsonl
```

`terminate`와 `T`, `Q` 키는 EUDAQ RunControl에 속하며 nestDAQ 세션은 `terminate`를 거부합니다. 셋업 항목, run, 화면, 데이터와 한계는 [nestDAQ 모드](nestdaq.md)에 있습니다.

#### 브라우저에서

```sh
# 페이지 서버를 시작하고 결과 페이지를 엽니다(아직 세션 없음).
scripts/qa_page.sh
```

EUDAQ 세션:

1. 셋업 탭에서 `default.ini`를 고르고 "세션 시작"을 누릅니다 (`scripts/start_session.sh --setup configs/setups/default.ini --name public-session --no-attach`).
2. 실험 탭에서 "빔 켜기"를 누르면 (`scripts/session_control.sh public-session on --id page-on`) event 수가 늘기 시작합니다.
3. QA 탭에서 "정렬" (`scripts/align_session.sh public-session`), 그다음 "QA 실행" (`scripts/analyze_session.sh public-session`)을 누릅니다.
4. 실험 탭에서 "취득 끝내기" (`scripts/session_control.sh public-session terminate --id page-terminate`), 그다음 "RunControl 나가기 (Q)" (`docker exec public-session tmux send-keys -t ITS3:rc Q`)를 누릅니다.
5. 같은 카드에서 "출력 복사" (`docker cp public-session:/tmp/session-run outputs/public-session-UTCTIME`), 그다음 "컨테이너 지우기" (`docker rm -f public-session`)를 누릅니다. DAQ 화면 탭에서도 같은 단계를 진행할 수 있습니다.
nestDAQ 세션:

1. 셋업 탭에서 `nestdaq.ini`를 고르고(trigger 보드를 사용하려면 `nestdaq-board.ini`) "세션 시작"을 누릅니다 (`scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach`).
2. 실험 탭에서 "빔 켜기"를 누릅니다 (`scripts/session_control.sh public-session on --id page-on`).
3. nestDAQ 탭에서 "Start"를 누릅니다 (`scripts/session_control.sh public-session nestdaq start`). 화면 링크는 daq-webctl, SlowDash와 DQM을 엽니다. run을 끝내려면 "Stop"을 누릅니다 (`scripts/session_control.sh public-session nestdaq stop`).
4. 실험 탭에서 EUDAQ 세션과 같이 "출력 복사", 그다음 "컨테이너 지우기"를 누릅니다.

`scripts/qa_page.sh --stop`으로 페이지 서버를 멈춥니다. 터미널에서 시작한 세션을 보려면 `scripts/qa_page.sh public-session`을 실행합니다. 각 탭은 [결과 페이지 안내](qa-page.md)에 설명되어 있습니다.
