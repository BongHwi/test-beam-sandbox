[English](msensor.md) | [Korean](ko/msensor.md) | [Japanese](ja/msensor.md)

# Synthetic pixel device

This system produces synthetic responses, not measurements from real equipment. The build uses the [official Corryvreckan repository](https://gitlab.cern.ch/corryvreckan/corryvreckan) at master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`.

## Configuration

Initialisation requires `plane`, `columns`, and `rows`; `regions` defaults to 1 and must divide the columns. `tiles_x` and `tiles_y` (default 1) divide the matrix into a tile grid; they must divide `columns` and `rows`. Tiles are numbered from 0 at column 0, row 0, along the columns first, so tile `t` covers tile column `t mod tiles_x` and tile row `t div tiles_x`. With more than one tile, `regions` must be 1 and `threshold_dac` is one integer or one per tile. The run setting `tiles_off` lists tiles that are switched off; they produce no hits and no noise. Tiles touch each other without gaps; this is a simplification. The default setup uses `6 x 2` tiles of `156 x 444` pixels with pitch `23 x 21` µm. `clock_mode` is `virtual` or `paced`. The run requires `EUDAQ_DC`, `EUDAQ_ID`, `readout_mode`, and `threshold_dac`. `EUDAQ_ID` must equal `plane`. In `triggered` mode supply `strobe_delay_ns` and `strobe_length_ns`; in `continuous` mode supply `stream_dir` and `window_length_ns`. Unknown or mode-incompatible keys are rejected. `response_file` defaults to `response_default.json`.

![Hit map of the msensor DUT (936 x 888 pixels) in the Histograms tab.](images/page-histograms.png)

For `source=synthetic`, pass `seed` and exactly one trigger specification in `MSENSOR_SOURCE_CONFIG_JSON`; keep source conditions out of ini files because EUDAQ copies configuration into BORE. A reproducible run sets `sim_end_ns` and waits for `source_finished=1` before stopping. `paced` early stopping depends on wall-clock time. Run `tests/scripts/run_msensor_e2e.sh` for end-to-end checks. For `source=session`, set `clock_mode=virtual` and pass `seed` in `MSENSOR_SOURCE_CONFIG_JSON` without a trigger specification or `sim_end_ns`; the session supplies triggers and the clock.

Each msensor decides for itself whether a particle leaves a hit: the draw depends on `seed`, on the name of the msensor and on the particle, so the msensors of one session miss different particles.

## Data formats

The payload starts with ASCII `MSENSOR`, version byte 1, a 4-byte big-endian JSON length and canonical UTF-8 JSON; hits are `[col,row]`. The stream starts with ASCII `MSWINDOW` and version byte 1; each record has a type, 4-byte big-endian length, canonical JSON and CRC32. The FOOTER contains a SHA-256. `python -m msensor.stream verify FILE` returns 0 for valid, 2 for truncated and 3 for corrupt streams. Triggered mode uses a half-open gate; continuous mode records fixed windows, including empty windows. The final window is truncated at the actual end time.


For the msraw stream used in nestDAQ mode, see [nestDAQ mode](nestdaq.md).

## Parameter ranges and source

`regions` defaults to 1. `sim_step_ns` defaults to 1,000,000 and `pace` to 1; `deterministic_ids` defaults to 0. `threshold_dac` is one integer or one per region or tile, each in 0..255. `tiles_x` and `tiles_y` are 1..64 and `tiles_off` holds distinct tile numbers. In `triggered` mode `strobe_delay_ns` is 0..1,000,000 and `strobe_length_ns` is 1..1,000,000. In `continuous` mode `window_length_ns` is 1,000..10,000,000 and `window_guard_before` and `window_guard_after` are 0..8, default 1. The converter defaults `msensor_window_offset` to 0 and `msensor_window_span` to 1; a missing requested window is an error.

For `source=synthetic`, `MSENSOR_SOURCE_CONFIG_JSON` requires `seed` in 0..2^63−1 and exactly one of `trigger_rate_hz`, `trigger_period_ns` with `trigger_first_ns`, or increasing `trigger_times_ns`. A rate of 0 disables triggers. `beam_center_col`, `beam_center_row`, `beam_sigma_px`, and `sim_end_ns` describe the synthetic source. A continuous stream is named `<producer name>_run<run number>.msws` by default and never overwrites an existing file.

The stream record types are HEADER=1, WINDOW=2, EMPTY_RUN=3, TRIGGER=4 and FOOTER=5, with a 16 MiB body limit. The FOOTER SHA-256 covers bytes from the magic through the preceding record; the complete file SHA-256 includes the FOOTER. The triggered gate is `[trigger+strobe_delay_ns, trigger+strobe_delay_ns+strobe_length_ns)` and window k is `[k·W,(k+1)·W)`. A truncated final window receives missing guard flags. Same-time crossings precede triggers in identifier order; closed windows precede triggers in the stream.
