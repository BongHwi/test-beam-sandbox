[English](setup-file.md) | [Korean](ko/setup-file.md) | [Japanese](ja/setup-file.md)

# Setup file

A setup file describes one session: which devices sit where, their settings, the run length and the beam. It is a text file with `[section]` headers and `item = value` lines; lines starting with `#` are comments. Items that are left out take the default values below. `configs/setups/default.ini` is the default setup; to choose another run length, copy it and change the `[run]` item `events`.

![A setup file open in the setup tab of the result page.](images/page-setup.png)

`configs/setups/default.ini` has ALPIDE reference planes and an msensor DUT. `configs/setups/small-msensor.ini` uses a smaller msensor DUT. `configs/setups/msensor-telescope.ini` uses six msensor reference planes and one msensor DUT.

## Commands

- `scripts/setup_session.sh new FILE` asks for the number and spacing of the reference planes, the DUT kind and position, the run length and the beam energy. Options such as `--reference-planes`, `--spacing-mm`, `--dut`, `--dut-z`, `--events`, `--energy-gev`, `--rate-hz`, `--alpide-ithr`, `--msensor-threshold` and `--msensor-readout` set the answers, and `--no-input` skips the questions. An existing file is replaced only with `--force`.
- `scripts/setup_session.sh show FILE` prints the devices in beam order with their positions and main settings, the run length, the beam, and a side view along the beam.
- `scripts/setup_session.sh check FILE` names every wrong item with the values it accepts and exits with a code other than 0. It also says whether QA is possible.
- `scripts/start_session.sh --setup FILE` checks the setup, generates the session files and starts. The session keeps the setup as given (`setup-source.ini` or `setup-source.json`) and as `setup.json` in `/tmp/session-run`; alignment and QA use the devices of that copy.
 `--scintillators 0` omits the plates; `--scintillators 2` (default) adds the two default plates.

## Items

| Section | Item | Default | Accepted values |
|---|---|---|---|
| `[run]` | `events` | `60000` | events per run before the next run starts, `1` to `1000000000` |
| `[beam]` | `particle` | `e-` | `e-` |
| `[beam]` | `energy_gev` | `4` | `3` to `6` |
| `[beam]` | `position_mm` | `1.1, -2.1, -20` | nominal x, y and source z; the source must be upstream of every device |
| `[beam]` | `spread_mm2` | `25, 25` | nominal variance in x and y, `0.000001` to `10000` |
| `[beam]` | `rate_hz` | `220` | nominal rate, `1` to `1000` |
| `[positions]` | `offset_max_mm` | `5` | largest hidden x and y offset of each device, `0` to `10` mm; see [alignment](alignment.md) |
| `[positions]` | `offset_draw` | `0` | another number draws other offsets for the same devices, `0` to `1000000` |
| `[alpide N]`, `[msensor N]` | `plane` | required | unique, `0` to `63` |
| `[alpide N]`, `[msensor N]` | `position_mm` | required | x, y, z of the sensor centre |
| `[alpide N]` | `pitch_um` | `30, 30` | `1` to `1000` |
| `[alpide N]` | `pixels` | `1024, 512` | `1024, 512` |
| `[alpide N]` | `ithr` | `0` | `0` to `255` |
| `[alpide N]` | `strobe_length` | `40` | `1` to `4000`, in 25 ns ticks |
| `[msensor N]` | `role` | `dut` | `dut` or `reference`; a reference needs `triggered` readout |
| `[msensor N]` | `pitch_um` | `23, 21` | `1` to `1000` |
| `[msensor N]` | `tiles` | `6, 2` | tiles along x and y, `1` to `64` |
| `[msensor N]` | `tile_pixels` | `156, 444` | columns and rows of one tile; the matrix is at most `4096` per side |
| `[msensor N]` | `tiles_off` | empty | tile numbers to switch off |
| `[msensor N]` | `pixels` | not set | a single-tile matrix instead of `tiles` and `tile_pixels` |
| `[msensor N]` | `threshold_dac` | `100` | `0` to `255`, one value or one per tile |
| `[msensor N]` | `readout` | `continuous` | `continuous` or `triggered` |
| `[msensor N]` | `window_length_ns` | `1000000` | `1000` to `10000000`, continuous readout only |
| `[msensor N]` | `strobe_delay_ns` | `0` | `0` to `1000000`, triggered readout only |
| `[msensor N]` | `strobe_length_ns` | `2000` | `1` to `1000000`, triggered readout only |

`N` counts from `0` for each device kind without gaps. ALPIDE planes are reference planes; msensor planes follow their `role`. The beam position, spread and rate are nominal: each session varies them privately by a small amount, so the analysis has to find the actual beam. The finite example (`--example`) ends when its source finishes and does not use `events`.

The session follows each electron with a constant momentum from the beam source through the planes. It adds multiple scattering in every plane (100 µm of silicon each) and in the air between the source and the last plane, as Gaussian scattering angles from the Highland formula. Energy loss, bremsstrahlung and secondary particles are not simulated. The scintillators only decide the trigger and add no material.

In a running session started with `--setup`, the run length and the device settings `ithr`, `strobe_length`, `threshold_dac`, `readout`, `window_length_ns`, `strobe_delay_ns` and `strobe_length_ns` can be changed for the next run with `scripts/session_control.sh NAME set` ([operator screen guide](operator-screen.md)); the other items stay as the session started.

The default msensor is `6 x 2` tiles of `156 x 444` pixels with pitch `23 x 21` µm, as described in [the msensor document](msensor.md). Tiles are numbered from `0` at column `0`, row `0`, along x first: tile `t` is tile column `t mod 6` and tile row `t div 6` in the default grid. A switched-off tile gives no hits and no noise. The default beam (variance `25, 25` mm², sigma `5` mm in x and y) is wide enough to reach every tile; in one measurement `70.3` % of events have a track with the default [trigger](trigger.md). `configs/setups/small-msensor.ini` is the earlier single-tile `128 x 128` msensor with pitch `30` µm, the narrow beam `0.003, 0.004` and no position offsets.

| Section | Item | Default | Accepted values |
|---|---|---|---|
| `[scintillator N]` | `channel` | required | unique `0` to `3` |
| `[scintillator N]` | `position_mm` | required | centre x, y, z in mm, downstream of the beam source |
| `[scintillator N]` | `shape` | `rectangle` | `rectangle` or `disc` |
| `[scintillator N]` | `size_mm` | `30, 20` | rectangle width and height or disc diameter, `1` to `200` mm |
| `[scintillator N]` | `hole_mm` | `0` | central hole width and height or diameter, smaller than the plate |
| `[trigger]` | `trg` | `trg0&trg3&dt_trg>10000&dt_veto>5000&!bsy` | nonempty trigger expression using existing channels |
| `[trigger]` | `veto` | `ntrg>0` | trigger expression, or empty to disable veto |
| `[trigger]` | `count` | `1xxR Rxx1 xxxR Rxxx` | one to sixteen four-character counter conditions |

## QA

QA reconstructs tracks from the reference planes. It needs at least 3 of them; a track needs hits on 5 planes, or on one plane fewer than the number of reference planes when there are fewer than 6, but never fewer than 3. Without an msensor DUT, QA reports the number of tracks only. Switched-off tiles are masked in the analysis, so the efficiency uses only the tiles that are on; QA names the switched-off tiles in one line. The finite example needs an msensor, because the msensor producer reports when the example source has finished.

## Not in the setup file

Settings that the virtual devices do not react to are not offered: other ALPIDE registers such as `VCASN`, `RDOMODE`, `minspacing` and `fixedbusy`, msensor `regions` (per-tile thresholds replace them) and window guards, and the internal source and clock settings. The beam direction and the private variations stay hidden in the session.

## Track resolution

The setup tab opens the external telescope optimiser by Magnus Mager (CERN) for either x or y; it requires internet access. The link sends the beam momentum `sqrt(T² + 2 T m)` in GeV/c, with `T` the kinetic energy and `m = 0.00051099895` GeV, and the device planes in z order. Each plane has resolution pitch/√12 for the selected axis and material 100 µm of silicon, or 0.107 % X/X0. The DUT scatters without a measurement; scintillators are omitted. Endpoints sit 10 mm before and after the devices, without material or measurement.

[Open the default setup in x](https://mmager.web.cern.ch/telescope/tracking.html#part=e&p=4.0005&x=[-1,0,2,4,6,8,10,12,13]&XX0=[,0.00107,0.00107,0.00107,0.00107,0.00107,0.00107,0.00107,]&sy=[,0.000866,0.000866,0.000866,0.000658,0.000866,0.000866,0.000866,]&en=[0,1,1,1,0,1,1,1,0]).

This sandbox's simulation does not include multiple scattering, so the optimiser's value estimates a real telescope with the same arrangement.
