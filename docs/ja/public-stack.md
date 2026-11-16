[English](../public-stack.md) | [Korean](../ko/public-stack.md) | [Japanese](../ja/public-stack.md)

# 公開スタックとソースのバージョン

このシステムの応答は合成値であり、実機の測定値ではありません。[公式 Corryvreckan リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドします。

## ビルド

`scripts/build_session.sh` でビルドします。EUDAQ のインストール先は `/opt/eudaq`、ソースは `/opt/eudaq-src`、ビルド先は `/opt/eudaq-build`、Corryvreckan は `/opt/corry` です。固定依存関係は `docker/versions.lock` に記載しています。`tests/scripts/smoke_public_stack.sh` で公開スタックを確認します。

## 公開ソースと変更

EUDAQ v2.8.1 は https://github.com/eudaq/eudaq の commit `bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e` から取得します (LGPL-3.0)。ITS3 producer と変換器を直接使います。Corryvreckan は上記の公式 master commit を使用します (MIT)。

`docker/base/corry-root-eigen.patch` は Eigen トラック不確かさベクトルの ROOT dictionary を追加します。11 行追加、0 行削除です。`runcontrol/ITS3RunControl.patch` は観測と制御のフックを追加します。17 行追加、3 行削除です。`runcontrol/ITS3start.patch` はランチャーのオプション、統合、リスナー準備確認を追加します。126 行追加、10 行削除です。独立したフックの実装は `runcontrol/` にあります。ROOT、Ubuntu、conda、Python、jsoncons のソースバージョンと digest は `docker/versions.lock`、`docker/architectures.lock`、`docker/base/` のロックファイルに記載しています。

## 固定依存関係

ビルドで固定する公開ソースとバージョンです。パッケージごとの固定値とチェックサムは表のロックファイルにあります。

| ソース | バージョンとロック |
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
