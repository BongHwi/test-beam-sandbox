[English](daq-modes.md) | [Korean](ko/daq-modes.md) | [Japanese](ja/daq-modes.md)

# DAQ modes

A session takes data with one of two public data-acquisition frameworks, chosen in the setup file:
- EUDAQ (`[run] daq = eudaq`, the default) reads the virtual ALPIDE reference planes, the msensors and the virtual trigger board with public EUDAQ: its ALPIDE producer, trigger producer and data collector are used unchanged, and its RunControl runs with the status and control extensions of this repository. The public analysis software Corryvreckan, built from its [official repository](https://gitlab.cern.ch/corryvreckan/corryvreckan) at master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`, then aligns the planes, reconstructs particle tracks and measures how efficiently the DUT detects them.
- nestDAQ (`[run] daq = nestdaq`) reads every msensor as a continuous or triggered stream with nestDAQ devices: an input device for each msensor, FileSink to store the frames and a monitor (DQM) that fills hit maps and pairs the triggers of the msensors. daq-webctl controls the runs, and SlowDash shows time series. An optional virtual trigger board (`trigger_board = on`) sends a periodic trigger to every msensor to mark time frame boundaries and writes the physics triggers from the beamline to a separate board stream.

## Comparison

Both modes use the same simulated beam, setup files, scripts and result page. The setup file chooses the mode:

| | EUDAQ mode | nestDAQ mode |
|---|---|---|
| Setup file | `[run] daq = eudaq` (the default) | `[run] daq = nestdaq` |
| Images | `scripts/build_session.sh` | also `scripts/build_session.sh --nestdaq` |
| Example setups in `configs/setups/` | `default.ini`, `small-msensor.ini`, `msensor-telescope.ini` | `nestdaq.ini`, `nestdaq-clocks.ini`, `nestdaq-missed.ini`, `nestdaq-telescope.ini`, `nestdaq-board.ini` |
| Devices read | ALPIDE reference planes, msensors and the trigger board | msensors; the ALPIDE planes stay in the beam as material |
| Trigger | scintillators and the trigger condition of the setup file | the triggers of the simulated beamline; scintillators and `[trigger]` with `trigger_board = on`, which adds a virtual trigger board with periodic triggers |
| Run control | public RunControl screen and `scripts/session_control.sh` | daq-webctl and `scripts/session_control.sh NAME nestdaq` |
| Monitoring | RunControl screen; Experiment and DAQ screen tabs of the page | DQM and SlowDash; nestDAQ and QA tabs of the page |
| Stored data | EUDAQ raw files | msraw frames in FileSink files; `python3 -m nestdaq_mode.extract` writes the msraw stream of each msensor, and of the trigger board, to its own file |
| Analysis | alignment and QA with Corryvreckan | no alignment yet; `python3 -m nestdaq_mode.framebuilder` builds time frames offline at the periodic triggers; the DQM shows hit maps, correlations and the trigger pairing, and `nestdaq qa` and the QA tab print its numbers |

## At a test beam

| Step | In the sandbox | At a test beam |
|---|---|---|
| Setup | one setup file | install the devices and prepare the EUDAQ configuration files |
| Acquisition | `scripts/start_session.sh` shows the public RunControl screen | start RunControl and the producers and operate them on that screen |
| Acquisition with nestDAQ | `scripts/session_control.sh NAME nestdaq start` and `stop`, or daq-webctl | start the nestDAQ devices and control the runs with daq-webctl |
| Beam | `scripts/session_control.sh NAME on`, `off` | comes from the accelerator |
| Trigger | scintillators and the condition in the setup file; the public trigger producer with a virtual board | scintillators with their trigger board; the trigger producer sets the condition |
| Alignment and analysis | `scripts/align_session.sh`, `scripts/analyze_session.sh` | run Corryvreckan with configuration files |
| Results | `histograms.root` written by QA | open the ROOT file |
