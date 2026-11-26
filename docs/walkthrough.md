[English](walkthrough.md) | [Korean](ko/walkthrough.md) | [Japanese](ja/walkthrough.md)

# Walkthrough

This guide follows a session from the build to the removal of the container: in the terminal with EUDAQ, in the terminal with nestDAQ, or in the browser. The [quick start](../README.md#quick-start) in the README is the shortest path.

## Build the images

Get the code and build the images once. Run all commands from the repository root:

```sh
# Get the code.
git clone https://github.com/BongHwi/test-beam-sandbox.git
cd test-beam-sandbox

# Build the public base and session images (once).
scripts/build_session.sh

# For the nestDAQ mode, also build the nestDAQ image (once).
scripts/build_session.sh --nestdaq
```

The first build compiles EUDAQ and Corryvreckan and takes a while; on a 24-core arm64 computer it took about 4 minutes, and the nestDAQ image about 10 minutes more. Later sessions reuse the images.

Then take the EUDAQ or the nestDAQ path, in the terminal or in the browser. All paths use one session container named `public-session`, and each button of the browser path runs the same command as the terminal path, so the two can be mixed.

#### EUDAQ session in the terminal

##### 1. Acquire

Start a session from the default setup. Other setups are in `configs/setups/`; see the [setup file reference](setup-file.md). RunControl, the producers, the data collector and the simulated beam run in one container named `public-session`, and this terminal shows the public RunControl screen.

```sh
# Start the session and show the public RunControl screen.
scripts/start_session.sh --setup configs/setups/default.ini --name public-session
```

The session starts with the beam off. In a second terminal turn the simulated beam on; the event counts on the RunControl screen start to grow.

```sh
# Turn on the simulated beam.
scripts/session_control.sh public-session on
```

RunControl starts the next run when a run ends, until the sequence is ended with `T`. To leave the screen without stopping anything press `Ctrl-b` and then `d`; `scripts/attach_session.sh public-session` shows it again.

![The public RunControl screen of a session: run progress, the producers and the collectors](images/runcontrol-screen.png)

The RunControl screen of a running session. The session uses the [ITS3 RunControl of EUDAQ](https://github.com/eudaq/eudaq/blob/bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e/user/ITS3/python/ITS3RunControl.py). The window keys are in the [operator screen guide](operator-screen.md).

##### 2. Analyze

The setup file gives surveyed positions. The actual positions differ by up to `5` mm and stay hidden, as after mounting, so alignment finds them from the data before QA reconstructs tracks and measures the DUT efficiency with Corryvreckan. Acquisition continues meanwhile ([alignment guide](alignment.md)). Alignment waits until the current run has `3000` data events.

```sh
# Find the device positions from the data of the current run.
scripts/align_session.sh public-session

# Analyze the current run: events, tracks and DUT efficiency.
scripts/analyze_session.sh public-session
```

QA ends with lines like these (the numbers vary):

```text
  Events: Ev: 10.1k Px: 113.3k Tr: 7.1k (0.703/ev) t = 47.6141s
  [F:AnalysisEfficiency:MSENSOR_6] Total efficiency of detector MSENSOR_6: 98.4446(+0.149964 -0.164889)%, measured with 6709/6815 matched/total tracks
```

Alignment prints the corrections it found and the tracks per event before and after. QA prints the event count, the track count and the efficiency of each DUT; its first line names the result directory in the container, whose `analysis/output/histograms.root` holds the histograms. Both name the Corryvreckan configuration they used ([operator screen guide](operator-screen.md)). The DUT is named `MSENSOR_0` in the DAQ, after its producer, and `MSENSOR_6` in the analysis, after its plane number.

##### 3. Finish

On the RunControl screen press `T` to end the sequence and then `Q` to leave RunControl; the screen then shows a shell in the container, which `Ctrl-b` and then `d` leaves. From another terminal, `scripts/session_control.sh public-session terminate` sends the same request as `T`; programs should use it, because a key pressed while the screen redraws can freeze the public RunControl ([operator screen guide](operator-screen.md)).

```sh
# Copy the output to the host.
docker cp public-session:/tmp/session-run ./session-output

# Remove the session container and everything in it.
docker rm -f public-session
```

Window keys, `S` and `R`, detaching and attaching again, and the beam history are in the [operator screen guide](operator-screen.md); the run length is in the [setup file reference](setup-file.md).

#### nestDAQ session in the terminal

##### 1. Acquire

Start a session from the nestDAQ setup; the nestDAQ image is chosen from the setup file. The nestDAQ processes run in the background of the container, so the same terminal can turn the beam on and start a run. `scripts/attach_session.sh public-session` shows the msensor runners; `Ctrl-b` and then `d` leaves that screen.

```sh
# Start a nestDAQ session without showing its screen.
scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach

# Turn on the simulated beam.
scripts/session_control.sh public-session on

# Start a run; the answer is JSON with the run number.
scripts/session_control.sh public-session nestdaq start
```

To enable the virtual trigger board, start from `configs/setups/nestdaq-board.ini` instead, or set `trigger_board = on` and `periodic_clk` in `[nestdaq]`. Every msensor then receives a periodic trigger every `periodic_clk` cycles of the 100.5 MHz board clock, and the `TRIGGERBOARD_0` runner stores the periodic and physics triggers, along with each trigger's scintillator pattern, in its own stream. Without this setting, a session runs as before.

##### 2. Monitor

The status shows the run, the devices and, for each msensor, the frames sent and the DQM copies dropped. The nestDAQ tab of the page shows these and the frames written by each runner, and links to daq-webctl, SlowDash and the DQM; `scripts/qa_page.sh public-session` opens it. `nestdaq qa` prints the numbers of the DQM, which the QA tab of the page also shows; the DQM hit maps and the trigger pairing are described in [nestDAQ mode](nestdaq.md).

```sh
# Show the run, the devices and the msensor streams.
scripts/session_control.sh public-session nestdaq status --json

# Print the DQM numbers: frames, triggers, hits and the trigger pairing.
scripts/session_control.sh public-session nestdaq qa
```

![The nestDAQ tab of the result page: run control, devices, msensor streams and screen links](images/page-nestdaq.png)

##### 3. Finish

Stop the run; the remaining data are stored before the run stops, and the next `nestdaq start` begins a new run. Then copy the output, remove the container and extract the msraw streams of each msensor and the trigger board on this computer; if the trigger board was used, build the time frames as well. Each tool prints a summary in JSON.

```sh
# Stop the run.
scripts/session_control.sh public-session nestdaq stop

# Copy the output to the host.
docker cp public-session:/tmp/session-run ./session-output

# Remove the session container and everything in it.
docker rm -f public-session

# Extract the msraw streams of each msensor and the trigger board in run 1.
python3 -m nestdaq_mode.extract session-output/nestdaq/data/run000001.dat extracted

# With the trigger board: build time frames at the periodic triggers, one JSON line per frame.
python3 -m nestdaq_mode.framebuilder --dir extracted --out frames.jsonl
```

`terminate` and the keys `T` and `Q` belong to the EUDAQ RunControl; a nestDAQ session rejects `terminate`. Setup items, runs, screens, data and limits are in [nestDAQ mode](nestdaq.md).

#### In the browser

```sh
# Start the page server and open the result page (no session yet).
scripts/qa_page.sh
```

For an EUDAQ session:

1. Setup tab: choose `default.ini` and press "Start the session" (`scripts/start_session.sh --setup configs/setups/default.ini --name public-session --no-attach`).
2. Experiment tab: press "Turn the beam on" (`scripts/session_control.sh public-session on --id page-on`); event counts start to grow.
3. QA tab: press "Align" (`scripts/align_session.sh public-session`), then "Run QA" (`scripts/analyze_session.sh public-session`).
4. Experiment tab: press "End data taking" (`scripts/session_control.sh public-session terminate --id page-terminate`), then "Leave RunControl (Q)" (`docker exec public-session tmux send-keys -t ITS3:rc Q`).
5. In the same card, press "Copy the output" (`docker cp public-session:/tmp/session-run outputs/public-session-UTCTIME`), then "Remove the container" (`docker rm -f public-session`). The DAQ screen tab has the same steps.

For a nestDAQ session:

1. Setup tab: choose `nestdaq.ini` (`nestdaq-board.ini` for the trigger board) and press "Start the session" (`scripts/start_session.sh --setup configs/setups/nestdaq.ini --name public-session --no-attach`).
2. Experiment tab: press "Turn the beam on" (`scripts/session_control.sh public-session on --id page-on`).
3. nestDAQ tab: press "Start" (`scripts/session_control.sh public-session nestdaq start`); the screen links open daq-webctl, SlowDash and the DQM. Press "Stop" (`scripts/session_control.sh public-session nestdaq stop`) to end the run.
4. Experiment tab: press "Copy the output", then "Remove the container", as for an EUDAQ session.

Stop the page server with `scripts/qa_page.sh --stop`. To watch a session started in the terminal, run `scripts/qa_page.sh public-session`. The [result page guide](qa-page.md) describes every tab.
