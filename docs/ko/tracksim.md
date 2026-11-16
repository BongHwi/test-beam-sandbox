[English](../tracksim.md) | [Korean](../ko/tracksim.md) | [Japanese](../ja/tracksim.md)

# tracksim

이 시스템의 응답은 합성값이며 실제 장비의 측정값이 아닙니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 사용

패키지 설정, API, 형식과 물리적 한계는 [tracksim](../../tracksim/README.md)에 설명되어 있습니다. 다음 명령은 tracksim을 설치하거나 변경하지 않고 호스트와 컨테이너 검사를 실행합니다. 결과 디렉터리를 직접 생성하고 제공된 checksum과 소스 파일을 비교합니다.

```sh
tests/scripts/run_tracksim_e2e.sh
```
