[English](tracksim.md) | [Korean](ko/tracksim.md) | [Japanese](ja/tracksim.md)

# tracksim

This system produces synthetic responses, not measurements from real equipment. The build uses the [official Corryvreckan repository](https://gitlab.cern.ch/corryvreckan/corryvreckan) at master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`.

## Use

The package configuration, API, formats and physical limits are described in [tracksim](../tracksim/README.md). The following command runs the host and container checks without installing or changing tracksim. It generates its own result directories and compares the source files with the supplied checksums.

```sh
tests/scripts/run_tracksim_e2e.sh
```
