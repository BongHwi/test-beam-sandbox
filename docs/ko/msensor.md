[English](../msensor.md) | [Korean](../ko/msensor.md) | [Japanese](../ja/msensor.md)

# 합성 픽셀 장치

이 시스템의 응답은 합성값이며 실제 장비의 측정값이 아닙니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 설정

초기화에는 `plane`, `columns`, `rows`가 필요합니다. `regions`의 기본값은 1이며 열 수가 이 값으로 나누어떨어져야 합니다. `tiles_x`와 `tiles_y`(기본값 1)는 matrix를 타일 격자로 나누며 `columns`와 `rows`를 나누어떨어지게 해야 합니다. 타일 번호는 열 0, 행 0에서 0으로 시작해 열 방향으로 먼저 세므로 타일 `t`는 타일 열 `t mod tiles_x`, 타일 행 `t div tiles_x`에 있습니다. 타일이 둘 이상이면 `regions`는 1이어야 하고 `threshold_dac`는 정수 하나 또는 타일별 정수입니다. run 설정 `tiles_off`는 끈 타일의 목록이며 끈 타일은 hit도 잡음도 내지 않습니다. 타일은 빈틈없이 붙어 있는 것으로 둡니다. 이것은 단순화입니다. 기본 셋업은 `6 x 2` 타일, 타일마다 `156 x 444` 픽셀, pitch `22.8 x 20.8` µm를 씁니다. `clock_mode`는 `virtual` 또는 `paced`입니다. run에는 `EUDAQ_DC`, `EUDAQ_ID`, `readout_mode`, `threshold_dac`가 필요합니다. `EUDAQ_ID`는 `plane`과 같아야 합니다. `triggered`에는 `strobe_delay_ns`와 `strobe_length_ns`, `continuous`에는 `stream_dir`과 `window_length_ns`를 지정합니다. 알 수 없거나 모드에 맞지 않는 key는 거절합니다. `response_file`의 기본값은 `response_default.json`입니다.

![Histograms 탭의 msensor DUT hit map(936 x 888 픽셀)입니다.](../images/page-histograms.png)

`source=synthetic`에서는 `seed`와 정확히 하나의 trigger 지정 방법을 `MSENSOR_SOURCE_CONFIG_JSON`에 전달합니다. EUDAQ가 설정을 BORE에 복사하므로 source 조건은 ini에 넣지 않습니다. 재현 가능한 run에는 `sim_end_ns`를 지정하고 `source_finished=1`을 확인한 뒤 멈춥니다. `paced` 조기 종료는 실제 시간에 영향을 받습니다. 전체 검사는 `tests/scripts/run_msensor_e2e.sh`로 실행합니다. `source=session`에서는 `clock_mode=virtual`을 지정하고 `seed`를 `MSENSOR_SOURCE_CONFIG_JSON`에 전달하고 trigger 지정과 `sim_end_ns`는 넣지 않습니다. trigger와 클록은 세션이 제공합니다.

## 데이터 형식

payload는 ASCII `MSENSOR`, 버전 byte 1, 4-byte big-endian JSON 길이와 정규 UTF-8 JSON으로 시작합니다. hit는 `[col,row]`입니다. stream은 ASCII `MSWINDOW`와 버전 byte 1로 시작하며 각 기록에 type, 4-byte big-endian 길이, 정규 JSON과 CRC32가 있습니다. FOOTER에는 SHA-256이 있습니다. `python -m msensor.stream verify FILE`은 정상 0, 잘림 2, 손상 3을 반환합니다. triggered 모드는 반열린 gate를 쓰고 continuous 모드는 빈 창을 포함한 고정 창을 기록합니다. 마지막 창은 실제 종료 시각에서 잘라냅니다.

## parameter 범위와 source

`regions`의 기본값은 1입니다. `sim_step_ns`의 기본값은 1,000,000, `pace`는 1이며 `deterministic_ids`는 0입니다. `threshold_dac`는 정수 하나 또는 영역별이나 타일별 정수이며 각각 0..255입니다. `tiles_x`와 `tiles_y`는 1..64이고 `tiles_off`는 서로 다른 타일 번호입니다. `triggered`에서 `strobe_delay_ns`는 0..1,000,000, `strobe_length_ns`는 1..1,000,000입니다. `continuous`에서 `window_length_ns`는 1,000..10,000,000이고 `window_guard_before`와 `window_guard_after`는 0..8, 기본값은 1입니다. 변환기의 `msensor_window_offset` 기본값은 0, `msensor_window_span`은 1이며 요청한 창이 없으면 오류입니다.

`source=synthetic`에서 `MSENSOR_SOURCE_CONFIG_JSON`에는 0..2^63−1 범위의 `seed`와 함께, `trigger_rate_hz`, `trigger_period_ns` 및 `trigger_first_ns`, 오름차순 `trigger_times_ns` 중 정확히 하나가 필요합니다. rate가 0이면 trigger가 없습니다. `beam_center_col`, `beam_center_row`, `beam_sigma_px`, `sim_end_ns`는 합성 source를 정합니다. continuous stream의 기본 이름은 `<producer name>_run<run number>.msws`이며 기존 파일을 덮어쓰지 않습니다.

stream 기록 type은 HEADER=1, WINDOW=2, EMPTY_RUN=3, TRIGGER=4, FOOTER=5이며 본문 상한은 16 MiB입니다. FOOTER SHA-256은 magic부터 직전 기록까지를 대상으로 하며, 전체 파일 SHA-256에는 FOOTER가 포함됩니다. triggered gate는 `[trigger+strobe_delay_ns, trigger+strobe_delay_ns+strobe_length_ns)`, 창 k는 `[k·W,(k+1)·W)`입니다. 잘린 마지막 창에는 빠진 guard flag를 설정합니다. 같은 시각에는 ID 순으로 crossing을 trigger보다 먼저 처리하고 stream에서는 닫힌 창을 trigger보다 먼저 기록합니다.
