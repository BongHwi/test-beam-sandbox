[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md)

# Test-beam sandbox

This repository is an environment for practicing the data taking and the analysis of a beam test. A simulated beam crosses a telescope of virtual pixel sensors (the reference planes) and a synthetic pixel sensor under test (msensor, the DUT: device under test). The devices, their settings and the beam are set in one setup file, and the beam can be switched on and off independently of the data taking.

A session takes data with one of two public data-acquisition frameworks, chosen in the setup file: EUDAQ (the default), with Corryvreckan for alignment, tracks and DUT efficiency, or nestDAQ, which reads every msensor as a stream and can add a virtual trigger board. [DAQ modes](docs/daq-modes.md) compares them.

Use it to learn acquisition and analysis procedures or to develop and test automation that operates acquisitions. All responses are synthetic; they are not measurements from real equipment.

![The experiment tab of the result page during a run: the devices in 3D along the beam, the beam on and the run state](docs/images/experiment-view.png)

## Requirements

Linux or macOS with Docker running Linux containers, Python 3 and at least 40 GiB of free space are required. The build needs Internet access; a session runs without network access. The build selects the Docker daemon architecture (arm64 or amd64) and uses at most 4 jobs and 8 GiB of memory. On the host, Python 3.9 or later runs the result page, the extraction tool and the frame builder of the nestDAQ mode without further packages; on macOS the first `python3` may ask to install the command line developer tools.

## Quick start

Run all commands from the repository root. The first build compiles EUDAQ and Corryvreckan and takes a while (about 4 minutes on a 24-core arm64 computer); later sessions reuse the images.

```sh
# Get the code.
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# Build the public base and session images (once).
scripts/build_session.sh

# Start a session from the default setup and show the public RunControl screen.
scripts/start_session.sh --setup configs/setups/default.ini --name public-session

# In a second terminal: turn on the simulated beam, then align and analyze the current run.
scripts/session_control.sh public-session on
scripts/align_session.sh public-session
scripts/analyze_session.sh public-session
```

The [walkthrough](docs/walkthrough.md) goes on from here: ending the session, the nestDAQ mode (`scripts/build_session.sh --nestdaq`) and the browser.

## Documentation

### Operating

- [Walkthrough](docs/walkthrough.md): a session step by step in the terminal (EUDAQ and nestDAQ) and in the browser.
- [DAQ modes](docs/daq-modes.md): the EUDAQ and nestDAQ modes compared, and the sandbox next to a real test beam.
- [Reference](docs/reference.md): what is running, the scripts, how the parts work together and the repository layout.
- [Operator screen](docs/operator-screen.md): windows and keys, beam and run commands, QA output and result files.
- [Setup file](docs/setup-file.md): devices, settings, run length, beam and `scripts/setup_session.sh`.
- [nestDAQ mode](docs/nestdaq.md): sessions that read the msensors with nestDAQ: setups, the optional trigger board, runs, screens, data, the frame builder and limits.
- [Alignment](docs/alignment.md): why and how the device positions are found.
- [Result page](docs/qa-page.md): the browser page.
- [Trigger](docs/trigger.md): trigger scintillators and the trigger condition.
- [msensor](docs/msensor.md): synthetic DUT divided into tiles that can be switched off, and its data and stream formats. Its default tile layout is `6 x 2` tiles of `156 x 444` pixels with pitch `23 x 21` µm.
- [Automated example](docs/automation.md): a session without the screen, for programs; the states of the status file.
- [Programs and agents](docs/programs-and-agents.md): what a program or an agent can connect to.

### Internals

- [Session source and beam control](docs/session-core.md): shared simulated beam, timing, device coordination and independent on/off operations.
- [Virtual ALPIDE board](docs/virtual-alpide-board.md): board interface for the public producer.
- [Virtual trigger board](docs/trigger-board-surface.md): condition and counters for the public producer.
- [Public board call surface](docs/alpide-board-surface.md): board interface used by the public producer.
- [RunControl extensions](docs/runcontrol-hook.md): status file and control socket.
- [Session acquisition and analysis](docs/session-run.md): layouts, generated files and the analysis.
- [tracksim](docs/tracksim.md): particle transport for the session core.
- [Public stack and source versions](docs/public-stack.md): public software versions and patches.

## License

Copyright (C) 2026 Bong-Hwi Lim (CNS, the University of Tokyo)

This repository is distributed under `LGPL-3.0-or-later`. The LGPL adds its conditions to the GPL text, so the full texts are in [COPYING.LESSER](COPYING.LESSER) and [COPYING](COPYING). EUDAQ (`LGPL-3.0`) and Corryvreckan (`MIT`) are downloaded during the build, and this repository contains no copy of them. `runcontrol/ITS3RunControl.patch` and `runcontrol/ITS3start.patch` are applied to EUDAQ, and `docker/base/corry-root-eigen.patch` is applied to Corryvreckan; each patch contains lines of the software it is applied to. The result page draws with JSROOT `7.5.2` (`MIT`), which the session image contains with ROOT, and its experiment view draws in 3D with three.js `r155` (`MIT`), which that JSROOT contains; the page server copies them from the image, and this repository contains no copy of them.

The nestDAQ image, built only for the nestDAQ mode, downloads further public software during its build; see [Public stack and source versions](docs/public-stack.md).
