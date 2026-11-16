[English](../public-stack.md) | [Korean](../ko/public-stack.md) | [Japanese](../ja/public-stack.md)

# 공개 스택과 소스 버전

이 시스템의 응답은 합성값이며 실제 장비의 측정값이 아닙니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 빌드

`scripts/build_session.sh`로 빌드합니다. EUDAQ 설치 경로는 `/opt/eudaq`, 소스는 `/opt/eudaq-src`, 빌드는 `/opt/eudaq-build`이며 Corryvreckan은 `/opt/corry`에 있습니다. 고정 의존성은 `docker/versions.lock`에 있습니다. `tests/scripts/smoke_public_stack.sh`로 공개 스택을 검사합니다.

## 공개 출처와 변경

EUDAQ v2.8.1은 https://github.com/eudaq/eudaq 의 commit `bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e`에서 가져옵니다(LGPL-3.0). ITS3 producer와 변환기를 직접 사용합니다. Corryvreckan은 위의 공식 master commit을 사용합니다(MIT).

`docker/base/corry-root-eigen.patch`는 Eigen track 불확도 벡터의 ROOT dictionary를 추가합니다. 11줄을 추가하고 0줄을 삭제합니다. `runcontrol/ITS3RunControl.patch`는 관측과 제어 hook을 추가합니다. 17줄을 추가하고 3줄을 삭제합니다. `runcontrol/ITS3start.patch`는 launcher 옵션, 통합과 listener 준비 확인을 추가합니다. 126줄을 추가하고 10줄을 삭제합니다. 별도의 hook 구현은 `runcontrol/`에 있습니다. ROOT, Ubuntu, conda, Python과 jsoncons의 소스 버전과 digest는 `docker/versions.lock`, `docker/architectures.lock`, `docker/base/` 잠금 파일에 있습니다.

## 고정 의존성

빌드에 고정된 공개 출처와 버전입니다. 패키지별 고정값과 checksum은 표의 잠금 파일에 있습니다.

| 출처 | 버전과 잠금 |
|---|---|
| ROOT ARM64 | https://hub.docker.com/r/rootproject/root, `6.30.02-conda`, `sha256:200ae4092d3dd079dc9dc131e743634f01eeb4a88fc4b50f149a0154bfdabb54` |
| ROOT amd64 base | https://hub.docker.com/r/rootproject/root, `6.26.10-conda`, `sha256:b07bf891c90a3f3a0be8dc0e36689815192aa03cfdefb74014a309305498a105` |
| ROOT amd64 conda | https://conda.anaconda.org/conda-forge/, `root=6.30.2=py311h320fe9a_1`, `sha256:3b0d3649a5099c9fb8d65c2f3bed49f32265c85790074e97643c115677f11962` |
| Python amd64 conda | https://www.python.org/, `python=3.11.6=hab00c5b_0_cpython`, `sha256:84f13bd70cff5dcdaee19263b2d4291d5793856a718efc1b63a9cfa9eb6e2ca1` |
| ROOT base amd64 conda | https://conda.anaconda.org/conda-forge/, `root_base=6.30.2=py311he0b698f_1`, `sha256:949f1ba92f64f91fc2524cf2474c8f68a87d97862f0a8cf53122d7945af2d6a4` |
| Ubuntu | http://ports.ubuntu.com/ubuntu-ports/, `20.04`, `docker/base/apt-packages.lock` |
| Python, GCC, CMake, Eigen | https://www.python.org/, https://gcc.gnu.org/, https://cmake.org/, https://eigen.tuxfamily.org/, `3.11.6`, `12.3.0`, `3.16.3-1ubuntu1.20.04.1`, `3.3.7-2` |
| jsoncons | https://github.com/danielaparker/jsoncons, `v1.5.0`, `95a220dff2d927f15e1fe0c7dac2821b7134e4c9` |
| Python packages | https://pypi.org/, `docker/base/pip-constraints.lock` |
| conda packages | https://conda.anaconda.org/conda-forge/, `docker/base/conda-amd64.explicit`, `docker/base/conda-amd64.sha256`, `279` |
