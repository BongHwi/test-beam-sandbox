[English](nestdaq.md) | [Korean](ko/nestdaq.md) | [Japanese](ja/nestdaq.md)

# nestDAQ mode

## What it is

In the nestDAQ mode a session reads every msensor with nestDAQ instead of EUDAQ. Each msensor runs in its own runner and writes a continuous or triggered stream. An input device for each msensor reads that stream from a file or a TCP connection, FileSink stores the frames, and the msensor DQM fills hit maps and pairs the triggers of the msensors. daq-webctl controls the runs, and SlowDash shows time series. The beam, the setup file and the result page work as in the EUDAQ mode, and the EUDAQ mode itself is unchanged.

Compared with the EUDAQ mode:

- Only the msensors are read; the ALPIDE planes remain in the beamline as material.
- Scintillators, `[trigger]` and `--example` are unavailable, and `events` is ignored.
- Alignment and analysis are not available yet. For QA, the DQM shows hit maps, correlations and the trigger pairing, `nestdaq qa` and the QA tab of the page print its numbers, and the extraction tool writes the msraw stream of each msensor to its own file.

## Build

Build with `scripts/build_session.sh --nestdaq`. On ARM64 the first build of the nestDAQ image took about 10 to 13 minutes on top of the session image; the image is about 1.4 GB, about 0.2 GB more than the session image. Components and licenses are in [Public stack and source versions](public-stack.md).

## Setup files

The example setups are in `configs/setups/`, next to those of the EUDAQ mode, and the setup tab of the page lists them:

| File | What it shows |
|---|---|
| `nestdaq.ini` | two msensors read in the nestDAQ mode |
| `nestdaq-clocks.ini` | an msensor that runs on its own clock |
| `nestdaq-missed.ini` | msensors that miss some triggers |
| `nestdaq-telescope.ini` | a telescope of six reference msensors and one DUT msensor |
| `nestdaq-board.ini` | two msensors with the virtual trigger board: periodic triggers and a board stream |

Set `[run] daq = nestdaq`; `[nestdaq] input = file | socket` chooses input, `frame_ms` configures frames, and `send_buffer_mb` applies only to socket input. See the [setup file reference](setup-file.md) for all items.

Each msensor can run on its own clock. In its `[msensor N]` section, `clock_offset_ns` sets the reading of the msensor clock when the session clock reads 0, and `clock_drift_ppm` sets how much faster that clock runs, in parts per million. Both are only for `daq = nestdaq` and default to 0, a clock shared with the session. The msensor then counts windows, frames and trigger times in its own clock, so window numbers of different msensors no longer match; the trigger number stays common to all of them. See `configs/setups/nestdaq-clocks.ini` for an example.

An msensor can also miss triggers on purpose: its `[msensor N]` section can list in `missed_triggers` the session trigger numbers, counted from 0 in every run, that this msensor does not see. It then writes no trigger record for them and numbers its later triggers one lower for each missed trigger, as a sensor that counts triggers itself would, so from its first missed trigger on its trigger numbers no longer match those of the other msensors. This is only for `daq = nestdaq`. With the trigger board on (see below), the numbers are those of the periodic triggers. See `configs/setups/nestdaq-missed.ini` for an example.

In nestDAQ mode an msensor can also produce noise hits: its `[msensor N]` section can set `noise = on`, which uses the default response of the msensor; its noise rate grows as `threshold_dac` is lowered. The default, `noise = off`, gives no noise hits, as in the EUDAQ mode.

For a load test of the data path, the `[nestdaq]` section can set `generator_mb_s`; above 0 it replaces every msensor runner with a generator that writes valid msensor raw frames at about that many MB/s per msensor, with periodic triggers and without the beam simulation. Every msensor must use continuous readout. The data carry no physics, so the DQM correlation means nothing in this mode.

In the telescope of `configs/setups/nestdaq-telescope.ini` every msensor except the most upstream one runs on its own clock. Every msensor has its own runner and its own input device, and all of them feed the same FileSink and DQM; the DQM pairs each msensor with the most upstream one.

The `[nestdaq]` section can also add a virtual trigger board with `trigger_board = on`. The default `off` keeps everything above unchanged. With the board on, every msensor receives a periodic trigger instead of one trigger for each particle: periodic trigger k, counted from 0 in every run, comes (k + 1) × `periodic_clk` cycles of the 100.5 MHz board clock after the start of the run. `periodic_clk` is 10000 to 1005000000 and defaults to 1005000, which is 10 ms; the period must be at least one window of every msensor. Hits still come from every particle. Each msensor writes the periodic triggers as TRIGGER records in its own clock, so periodic triggers with the same number have different times when the clocks differ. Every msensor must use continuous readout, and `generator_mb_s` cannot be used with the board. Scintillators and `[trigger]` are available in this mode; they decide which particles are physics triggers, and without scintillators every particle is a physics trigger with pattern 0. The board runner `TRIGGERBOARD_0` reads the physics triggers that the session accepts and writes them with the periodic triggers as a board stream. The stream reaches FileSink through its own input device, without a DQM copy. See `configs/setups/nestdaq-board.ini` for an example.

## Start a session

Start with `scripts/start_session.sh --setup FILE --name public-session`; the image is selected automatically. With `--no-attach` the terminal stays free, as in the README. The `NESTDAQ` tmux session has seven windows: `redis` for Redis, `webctl` for run control, `slowdash` for time series, `runners` for msensor runners, `sources` for input devices, `sink` for FileSink and `dqm` for monitoring. `scripts/attach_session.sh public-session` attaches to `NESTDAQ:runners`; `Ctrl-b` and then `d` leaves it. A runner that exits restarts after 2 seconds.

## Runs

Use `scripts/session_control.sh NAME nestdaq start|stop|status|qa [--json]`. The `--json` option applies only to `status` and `qa`; `start` and `stop` already print JSON. Exit codes are 0 for success, 1 for failure, 2 for rejection and 3 for an incomplete drain on stop. The page tab's Start and Stop and the daq-webctl page use the same run control. Drain sends remaining data before STOP. The command and page tab reject a run number that already has data; daq-webctl RUN moves to the next empty number, though the page can show the old number until refreshed. Beam commands remain `on`, `off` and `beam-status`; other EUDAQ commands are rejected.

`qa` reads the DQM: for each msensor the frames, triggers and hits of the current run, and for each other msensor its trigger pairing with the most upstream one (matched, only in one of the two, skipped), its column and row offset in pixels, its clock drift in ppm and its time offset in microseconds. Without `--json` it prints tables. It returns 0 when the DQM answers, even before the first pairs, and 1 when the DQM cannot be read. The QA tab of the page shows the same numbers.

## Screens

Run `scripts/qa_page.sh public-session` to open the page. Its nestDAQ tab shows run control, devices, streams, screen links, health and stored files.

![The nestDAQ tab shows a running session and its screen links.](images/page-nestdaq.png)

daq-webctl controls runs, SlowDash's `slowplot-msensor` shows time series, and DQM shows `hitmap` with columns × rows bins, `hitmap_tiles`, `tile_hits` with tiles_x × tiles_y bins, and `hits_per_frame` and `triggers_per_frame` with 2048 bins from −0.5 inclusive to 2047.5 exclusive. Ports 8080, 18881 and 8090 are inside the container. The DQM link opens the list of histograms: open `MSENSOR_0/hitmap` for the hit map of `MSENSOR_0`, or `correlation/summary` for all the other msensors at once.

DQM also pairs the triggers of each other msensor with those of the most upstream msensor, the one with the smallest z; DEV below is the name of the other msensor. It pairs them by time: after locking on the first triggers, it follows the difference of the two trigger times, which changes only slowly with the clock drift, and takes as a pair the triggers whose difference stays on that track, so a trigger missed by one msensor does not shift the later pairs. `correlation/col_DEV` and `correlation/row_DEV` compare the columns and rows of the paired triggers, `dcol_DEV` and `drow_DEV` show the differences, `trigger_dt_DEV` shows the trigger time difference as the run goes on, and `trigger_dn_DEV` shows the trigger-number difference of the pairs, which steps at each missed trigger. `match_DEV` counts matched, unmatched and skipped triggers, and `lost_DEV` counts triggers seen only by the upstream msensor or only by DEV. With continuous readout a trigger brings every hit of its window, so the correlation sits on a broad background from other particles in the same window; the difference histograms show it most clearly, and the position of their peak gives the offset between the two msensors.

`correlation/summary` shows all the other msensors at once, one bin for each. `summary_dcol` and `summary_drow` give the peak position of the difference histograms of each msensor, which is its offset from the upstream one in pixels; `summary_drift` and `summary_offset` give the slope in ppm and the intercept in microseconds of a straight line through its trigger time differences, so the clock of every msensor can be compared with the upstream one; and `summary_counts` gives its matched triggers, the triggers seen by only one of the two msensors, and the skipped ones.

The screens run inside the container, which has no network; the page server relays them on `127.0.0.1`. A link in the nestDAQ tab carries a ticket; the first visit exchanges it for a cookie for that port and removes the ticket from the address. Only the first request of a connection is checked, so this is weaker than the token of the page operations. Use it on a computer that only you use, and stop the page server with `scripts/qa_page.sh --stop` when you are done.

## Data

Data flows from runner to input device to FileSink. With `socket`, the runner sends to container port `18600 + N`; if the receiver is slow, frames are dropped and counted. `file` reads the runner's file. Saved files are under `/tmp/session-run/nestdaq/data/` in the container, one file for each run, such as `run000001.dat`. Copy them with `docker cp NAME:/tmp/session-run ./session-output`, then run `python3 -m nestdaq_mode.extract session-output/nestdaq/data/run000001.dat OUTDIR` from the repository root on the host. It writes the msraw stream of each msensor to its own file in OUTDIR and prints a summary in JSON. Extract returns 0 for complete data, 3 for incomplete or missing frames, and 2 for format or I/O errors. It runs with the Python 3 of the host, version 3.9 or later, and needs no further packages.

The msraw stream has a header and five record types: `TILE_DATA`, `TRIGGER`, `TICK`, `TILE_STATUS` and `END`; `TICK` closes a frame. Messages contain a SubTimeFrame header and carry `META`, frame and `END` data.

With the trigger board, the run also stores a board stream, which `extract` writes to `TRIGGERBOARD_0_runN.msraw`. It has the container of the msraw stream, with metadata format `msraw-board-metadata-1` and plane 1000, and the records `PERIODIC` (periodic trigger number and board clock), `PHYSICS` (physics trigger number, board clock, number of the last periodic trigger before it, clock cycles since that trigger or since the start of the run, and the scintillator pattern with bit n for channel n), `TICK` and `END`. The board clock counts cycles of 100.5 MHz from the start of the run; a `TICK` closes a frame of `frame_ms`. The periodic triggers are the logical frame border for the offline frame builder below; the frames of all streams still end at their `TICK`. `python3 -m msensor.boardstream verify FILE` checks a board stream and returns 0 for complete, 2 for incomplete and 3 for corrupt streams. The DQM does not show the board.

The offline frame builder joins the streams of one run into time frames: `python3 -m nestdaq_mode.framebuilder --dir DIR --out frames.jsonl`, where DIR holds the extracted `*.msraw` files, or `--dat FILE --work DIR` to extract a FileSink run file first. Frame f covers the board cycles from f × `periodic_clk` up to (f + 1) × `periodic_clk` and ends at periodic trigger f. Each line of the output is one frame with its physics triggers and, for every msensor, the number of windows, tiles and hits; `--hits` adds the hits. A window of an msensor belongs to the frame of its start time, read in the clock of that msensor from its TRIGGER records, so clock offset and drift do not move data between frames. A periodic trigger that an msensor missed is matched by time and its border is interpolated; the frame is then marked `estimated_border`. This needs a clock offset below half a period. Data after the last periodic trigger is counted in the summary on standard output and not written. The exit status is 0 for complete streams, 2 for unusable input and 3 for incomplete streams.

## Finish

Stop the run with `scripts/session_control.sh NAME nestdaq stop`; the remaining data are stored before the run stops. Copy the output with `docker cp NAME:/tmp/session-run ./session-output` and remove the container with `docker rm -f NAME`; the stored files are then in `session-output/nestdaq/data/`. `terminate` and the keys `T` and `Q` belong to the EUDAQ RunControl, and a nestDAQ session rejects `terminate`. On the page, the nestDAQ tab stops the run, and the Experiment tab copies the output and removes the container.

## Failures and limits

A runner restarts after 2 seconds. A run can continue without DQM, which returns only in a new session. Missing frames and dropped DQM copies are counted. Alignment and analysis are unavailable in this mode. FileSink discards messages that arrive after STOP. The tunnel checks only the first request of a connection. The nestDAQ mode has been checked on arm64 and x86_64 Linux and on arm64 macOS.

The queue from each input device to the DQM holds only a few frames. When the DQM falls behind, the input devices drop its copies and count them in `dqm_dropped`, so a slow DQM neither uses up memory nor slows the stored data.
