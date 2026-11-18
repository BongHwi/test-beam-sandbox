[English](operator-screen.md) | [Korean](ko/operator-screen.md) | [Japanese](ja/operator-screen.md)

# Operator screen

The session starts with the beam off. Start with `scripts/start_session.sh`, or use `--no-attach` and later take over with `scripts/attach_session.sh`. The session keeps running after detaching.

![The RunControl window of the operator screen during a run.](images/runcontrol-screen.png)

## Windows and controls

The public launcher creates `rc` (RunControl), `rp` (reference-plane producers), `dp` (DUT producer), `sp` (support processes), `dc` (collector), `log` (log output) and `perf` (performance output). The `log` window runs the public EUDAQ log collector and shows the log lines of RunControl, the producers and the collector, including `RUN #NUMBER is started` at each run start; the same messages are written to `EUDAQLog*.log` in `/tmp/session-run`. The `perf` window runs `htop`. Use `Ctrl-b n` and `Ctrl-b p` to move between windows, `Ctrl-b 0` for RunControl, and `Ctrl-b d` to detach. The RunControl screen shows the connection and state of each process, the current configuration and event progress. The event count on a producer or collector line can stay unchanged for a few seconds and then jump, because the public EUDAQ software delivers these status updates at irregular intervals; data is still being recorded during that time. To confirm that data is arriving, run the analysis described below again and compare the number of complete events.

`S` stops only this run and the next starts immediately; to finish, send `T` and then `Q`.

In RunControl, `S` stops the current run and proceeds to the next configuration, `R` repeats the current configuration, `T` terminates the sequence, and `Q` exits after termination. These are the public RunControl keys.

A key pressed at the moment the screen redraws a status line can freeze the public RunControl for good: the screen stops changing, the status file and the raw file are no longer written, `updated_monotonic_ns` in the status file stops increasing and requests to the control socket get no answer. Remove the session with `docker rm -f public-session` and start it again. The commands `scripts/session_control.sh public-session stop`, `repeat` and `terminate` do not go through the screen and are not affected.


For a nestDAQ session, attach to `NESTDAQ:runners`; its seven windows are `redis`, `webctl`, `slowdash`, `runners`, `sources`, `sink` and `dqm`. See [nestDAQ mode](nestdaq.md).

## Beam and analysis

In another terminal use `scripts/session_control.sh public-session on`, `off`, or `beam-status`. Each command shows the five latest changes with UTC timestamps and the path to the complete history inside the container. After an accepted `on` or `off` that changes the beam, the last line is that change. Add `--json` for the machine-readable response. The beam remains independent of run changes.

`scripts/session_control.sh public-session stop`, `repeat` and `terminate` send the RunControl requests `stop_next`, `repeat` and `terminate`, the same as `S`, `R` and `T` on the screen. Each prints the JSON response of RunControl and exits with 0 when the request was accepted and 1 otherwise. `--id ID` sets the id of the request, which the response and the status file `/tmp/session-run/status.json` repeat in `last_intent`; without it the id is `operator-stop`, `operator-repeat` or `operator-terminate`. An id has 1 to 64 letters, digits, `.`, `-` or `_`; any other id is not sent, and the command exits with 2. The same `--id ID` is also accepted for `on` and `off`.

Use `scripts/analyze_session.sh public-session` to analyze a snapshot of the current run. Repeat it to obtain a later snapshot, or add `--run NUMBER` for a closed run. Results are written to distinct directories inside the container. The command ends with the values the public analysis wrote to its log: the number of complete events in the snapshot, the last event progress line, the track line and the `Total efficiency of detector ...` line of each DUT. The temporary raw copy is removed after the analysis; `snapshot.json` and the results remain. `scripts/analyze_session.sh public-session --list` lists the run numbers of the raw files, oldest first; the `log` window also shows each run number when the run starts. Acquisition continues during analysis. See [session source and device loss](session-core.md) for source limits and recovery.

QA and alignment each print a line `Corryvreckan configuration: PATH (in the container)`. The first line of QA names its result directory in the container; `analysis/output/histograms.root` there holds the histograms. Copy it with `docker cp` and open it with ROOT, as after an analysis at a test beam. The alignment configuration files stay in `/tmp/session-run/alignment/corryvreckan/` and show the steps alignment ran; the snapshot they read is removed when alignment finishes. The build uses the [official Corryvreckan repository](https://gitlab.cern.ch/corryvreckan/corryvreckan) at master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`.

## Settings for the next run

The public RunControl reads its configuration file again before every run. For a session started with `--setup`, `scripts/session_control.sh public-session set` changes one setting in that file for the next run; the current run keeps its settings. These items can be changed: `events` of `[run]`, `ithr` and `strobe_length` of an ALPIDE plane, and `threshold_dac`, `readout`, `window_length_ns`, `strobe_delay_ns` and `strobe_length_ns` of an msensor. Names and values are those of the setup file and are checked by the same rules; the other items stay as the session started.

```sh
# Set the threshold of the third reference plane for the next run.
scripts/session_control.sh public-session set alpide 2 ithr 60

# Show the settings of the current run and of the next run.
scripts/session_control.sh public-session settings
```

The change applies when RunControl configures the next run: when the current run reaches its events, or after `stop` (`S`) or `repeat` (`R`). Each run keeps the configuration it read as `run<number>.conf` beside its raw file, QA names the settings of the run that differ from the setup file, and each change is a line of `command-history.jsonl` in the session directory. At a test beam the operator edits the configuration file between runs in the same way.

The `[trigger]` items `trg`, `veto` and `count` can also be changed for the next run:

```sh
scripts/session_control.sh public-session set trigger count "1xx1 xxx1"
```

## A producer stops responding

When a producer stops responding while it stays connected, its event count on the RunControl screen stops while the other counts continue, and its state can stay `RUNNING`. The data collector waits for it.

At a test beam the operator ends such a producer in its window: the reference planes are in the `rp` window and the DUT producers in `dp`, and each pane is titled with its producer and shows its process number. Press Ctrl-C in that pane. A producer that does not react to Ctrl-C is ended with the command:

```sh
# End the stalled producer ALPIDE_plane_2: SIGTERM, then SIGKILL after 5 s.
scripts/session_control.sh public-session end-producer ALPIDE_plane_2
```

The command takes the producer names shown on the RunControl screen and records the operation in `command-history.jsonl`. RunControl then shows the producer as disconnected.

In one test, the remaining producers and collector continued; a `stop` request was accepted but did not finish. Restarting the ended producer in its pane reconnected it, but it stayed `UNINIT` and did not join another run. To use every device again, keep the output with `docker cp` and start a new session.

## QA result files

Each QA writes `/tmp/session-run/qa/latest.json` (the last QA) in the session container and, when its `status` is `ok`, adds one line to `/tmp/session-run/qa/history.jsonl`. These items are kept:

- `format`: `1`, the version of these items. Within the same `format` the items below keep their names and meaning; items may be added. A change of meaning raises `format`.
- `status`: `ok` (the analysis finished), `not_aligned` (the setup has position offsets and the session has no alignment; QA stopped before the analysis) or `failed` (the analysis ended with an error).
- `started_utc`, `finished_utc`: start and end of the QA, ISO 8601 time in UTC.
- `id`: the result directory under `qa/` (`example-...` for the finite example).
- `run`: the raw file name of the analyzed run.
- `data_events`: the complete data events analyzed (for the finite example, the count of the last analysis progress line).
- `tracks`: the tracks reconstructed, or `null` when the analysis reported none.
- `efficiency`: one entry per DUT with `dut` (analysis name), `percent` (%), `plus` and `minus` (upper and lower error, %), `matched` and `total` (tracks with an associated hit and all tracks counted). Empty without a DUT.
- `alignment.id`: the alignment used; `alignment` is `null` without one.
- `position_warning`: present only when QA finds that the devices seem to have moved since the alignment.

With `not_aligned`, `latest.json` has only `format`, `status`, `started_utc`, `finished_utc` and `alignment` (`null`) of these items. With `ok` and `failed` it has all of them, `position_warning` only when it applies; with `failed`, `tracks` can be `null` and `efficiency` empty. Each line of `history.jsonl` has `format`, `id`, `run`, `data_events`, `tracks`, `efficiency`, `started_utc` and `finished_utc`. Other items of the two files may change. The QA command prints the same lines as before, and the result page reads these files.

## Running Corryvreckan yourself

The QA configuration reads a snapshot of the run with complete events, which QA removes after the analysis. To repeat the analysis of a closed run (`scripts/analyze_session.sh public-session --run NUMBER`) with Corryvreckan directly, make the snapshot again and run `corry` with that configuration in a new directory. Replace `NUMBER` with the run number and `RESULT` with the result directory that QA printed:

```sh
# Make the snapshot of the closed run again at the path the configuration reads.
docker exec public-session python -m session_setup.snapshot /tmp/session-run/runNUMBER.raw RESULT/snapshot.raw

# Run Corryvreckan with the configuration of that QA in a new directory.
docker exec public-session mkdir -p /tmp/corry-again
docker exec -w /tmp/corry-again public-session corry -c RESULT/analysis/corry.conf
```

`corry` prints the same track and efficiency lines as the QA of that run.
