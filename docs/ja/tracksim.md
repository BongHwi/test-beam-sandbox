[English](../tracksim.md) | [Korean](../ko/tracksim.md) | [Japanese](../ja/tracksim.md)

# tracksim

このシステムの応答は合成値であり、実機の測定値ではありません。[公式 Corryvreckan リポジトリ](https://gitlab.cern.ch/corryvreckan/corryvreckan)の master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe` からビルドします。

## 使い方

パッケージの設定、API、形式、物理的な制限は [tracksim](../../tracksim/README.md) に記載しています。次のコマンドは tracksim をインストールまたは変更せず、ホストとコンテナーの検査を実行します。結果ディレクトリを生成し、提供されたチェックサムとソースファイルを比較します。

```sh
tests/scripts/run_tracksim_e2e.sh
```
