# tracksim

tracksim simulates electrons crossing planar pixel detectors and writes the result as a neutral JSON record set. It uses only the Python standard library (Python 3.11 or newer).

The pipeline has four stages:

1. **Transport.** An analytic, constant-momentum Gaussian primary-core model propagates each electron through the planes and any extra material sheets and records the crossings.
2. **Response.** An empirical binary pixel response (efficiency, delay, jitter, optional dark noise) turns crossings into pixel hits.
3. **Readout.** Hits are grouped into frames, either one frame per trigger (triggered) or fixed windows over the whole run (continuous).
4. **Output.** The neutral JSON writer stores response and frame records, a metadata file with checksums, and a manifest that `verify` can check later.

Every run is deterministic for a given configuration and interpreter.

## Quick start

Run these from the project root (the directory that holds `src/` and `examples/`). Nothing needs to be installed.

Check a configuration and print the interpreter and transport identity:

```sh
PYTHONPATH=src python3 -m tracksim preflight --config examples/three-plane.json
```

Generate a complete run into a new directory (an existing directory is never overwritten):

```sh
PYTHONPATH=src python3 -m tracksim generate --config examples/three-plane.json --out runs/x
```

Verify the finished run:

```sh
PYTHONPATH=src python3 -m tracksim verify runs/x
```

`generate` accepts `--events N` and `--seed S` to override those two keys. A configuration, input or run error is printed as `tracksim: error: <message>` and the exit status is 1; command-line usage errors exit with status 2.

Two example configurations are provided: `examples/three-plane.json` and `examples/three-plane-dark.json` (the same layout with dark noise, suitable for the pinned-horizon stream mode described below). Their values are illustrative and are not tuned to any device.

## Configuration (schema 4)

A configuration is one JSON object. Unknown keys are rejected. Lengths are in millimetres unless a key name says otherwise.

### Top level

| key | meaning | domain |
|---|---|---|
| `schema_version` | configuration schema | `4` |
| `preset` | registered preset name | `generic` (others only if registered) |
| `backend` | registered transport backend | `fast_electron` (others only if registered) |
| `writer` | registered writer | `json` (others only if registered) |
| `events` | number of simulated events | integer, 1 to 1 000 000 (a full run is also bounded by `limits.events`) |
| `seed` | transport and trigger seed | integer, 0 to 2147483647 |
| `device_seed` | response seed | integer, 0 to 2147483647 |
| `workers` | worker count | 1 for the fast backend |
| `beam` | source description | see below |
| `transport` | material description | see below |
| `timeline` | trigger timeline | see below |
| `detectors` | list of planes | non-empty, at most `limits.detectors` |
| `assumptions` | free text notes | optional, string to string |
| `limits` | resource caps | optional, may only lower the defaults |

### `beam`

| key | meaning | domain |
|---|---|---|
| `particle` | particle name | `"e-"` |
| `kinetic_energy_gev` | kinetic energy in GeV | 3 to 6 (the supported domain of the analytic model) |
| `position_mm` | source position | three numbers, each at most 10 000 in magnitude |
| `direction` | beam direction | three numbers, forward, polar angle below 0.05 rad |
| `phase_space_covariance` | 5x5 covariance of (x, y, slope x, slope y, relative momentum) | symmetric, positive semi-definite; standard deviations at most 1000 mm, 0.005, 0.005 and 0.05 |

### `transport`

| key | meaning | domain |
|---|---|---|
| `scattering` | enable multiple scattering | boolean |
| `air` | include the air between planes | boolean |
| `extra_layers` | thin sheets, each `{"z_mm", "material_budget"}` | at most 128; strictly between the source and the last plane, sorted, distinct, not on a plane; `material_budget` in radiation lengths, at least 0 |

All planes must have distinct `z` above the source, a propagation length of at most 1 m, no x or y tilt, and an extent (pixels times pitch) of at most 1 m. The conservative upper bound of the material budget may be at most 0.02 radiation lengths, and with scattering enabled a positive reference budget must be at least 1e-6 radiation lengths.

### `timeline`

| key | meaning | domain |
|---|---|---|
| `rate_hz` | mean trigger rate (exponential spacing) | positive |
| `duration_s` | nominal run duration in seconds | positive |
| `tail_padding_ps` | padding added after the last response, in ps | integer, at least 1 |
| `clock_hz` | readout clock | integer that divides 10^12 exactly |

### `detectors[]`

| key | meaning | domain |
|---|---|---|
| `id` | plane name | ASCII letter, then letters, digits or `_`, at most 64 characters; not `global`; unique |
| `pixels` | `[columns, rows]` | integers, 1 to 1 000 000 |
| `pitch_um` | pixel pitch `[x, y]` in micrometres | positive |
| `position_mm` | plane centre | three numbers |
| `orientation_deg` | rotation angles | `[0, 0, rotation about z]` for the fast backend |
| `active_um`, `passive_um` | silicon thicknesses in micrometres | active positive, passive at least 0 |
| `response` | see below | |
| `readout` | see below | |

`response` keys common to both models: `efficiency` (0 to 1), `response_delay_ns` (at least 0), `jitter_ns` (at least 0), `dark_hit_rate_per_pixel_hz` (at least 0). The delay and jitter are also bounded so that `1000 * (response_delay_ns + 8.6 * jitter_ns)` fits the timestamp range (below 2^63 ps); a larger value is rejected with an error naming the field.

- `"model": "boundary_resolution"` adds `resolution_um` `[x, y]`; each value must lie between `pitch/sqrt(48)` and `pitch/sqrt(12)` of its axis.
- `"model": "boundary_cluster_mean"` adds `cluster_mean`, between 1 and 2.25.

`readout` is one of:

- `{"model": "triggered", "gate_ns": g}` or `"gate_ps"`: one frame per trigger, covering `[origin, origin + gate)`. Exactly one of the two gate keys is required, and the gate must resolve to a whole number of picoseconds.
- `{"model": "continuous", "window_cycles": n}` or `"window_ps"`: fixed windows from time 0. Exactly one of the two keys is required; `window_cycles` is at most 2^32 - 1 and is converted with `timeline.clock_hz`.

### `limits`

Optional integer caps, each between 1 and its default (it can only be lowered): `events` (250 000), `detectors` (64), `intersections` (2 000 000), `pixels` (2 000 000), `frames` (2 000 000), `assignments` (5 000 000), `contributors` (10 000 000), `dark_hits` (100 000), `line_bytes` (67 108 864).

## Run directory

`generate` creates these files in the output directory:

| file | content |
|---|---|
| `settings.json`, `response-settings.json` | the configuration, after any `--events` or `--seed` override |
| `normalized-spec.json` | the checked, fully expanded specification |
| `geometry.json` | one geometry row per plane |
| `intersections.jsonl` | the crossings, one event per line |
| `fast-trace.jsonl` | compact transport trace, one event per line |
| `response.jsonl` | neutral response records: metadata, timeline, outcomes, hits |
| `frames.jsonl` | neutral readout records: metadata and frames |
| `metadata.json` | schema, checksums of the two neutral files, spec hashes, limitations |
| `response-report.json` | counts, horizon, provenance and the verification results of the response stage |
| `acceptance.json`, `report.json` | completion summary and acceptance level |
| `run-state.json` | the run status: `complete` when finished, `failed` with the error text after an error, or an in-progress state |
| `python-runtime.json`, `runtime.json`, `transport-envelope.json` | interpreter and transport identities recorded at the start of the run |
| `source-snapshot.json` | sha256 of every file of the package at the start of the run |
| `manifest.json` | file index with sizes and sha256, capabilities, runtime identities, source hashes, assumptions and limitations |
| `verification/report.json` | written by `verify` |

`verify` is a consistency check of the files in the directory. It requires `run-state.json` to say `complete`, then:

- checks every file listed in `manifest.json` against its recorded sha256 and size;
- checks that `normalized-spec.json` equals the specification derived from `settings.json`, and that the recorded requested capabilities match that specification and the recorded implemented lists contain the run's own backend and writer;
- re-checks the transport files against the specification and the recorded runtime identities;
- re-verifies the neutral records against the crossings (source digest, response outcomes, and the regrouping of the hits into frames);
- compares the package files and the interpreter with the recorded identities.

The reported `status` is one of:

- `verified`: everything matches;
- `verified_with_source_drift`: files are consistent but the package changed since the run (`changed_sources` names the files);
- `verified_with_runtime_drift`: the interpreter differs from the recorded one;
- `verified_with_source_and_runtime_drift`: both.

An inconsistency found by these checks makes `verify` fail with exit status 1. Hashes and identities are recorded metadata, not signatures: a complete, self-consistent set of forged files would pass.

## Library API

The package is importable as `tracksim` when `src` is on `sys.path`.

```python
import json
from tracksim.specs import normalize_config
from tracksim.fast_transport import iter_transport
from tracksim.stream import TriggerScheduler, ResponseStream, CrossingDigest, assemble_result
from tracksim.readout import assemble_frames, assemble_frame_set

spec = normalize_config(json.load(open("examples/three-plane-dark.json")))
scheduler = TriggerScheduler(spec)
stream = ResponseStream(spec, pin_horizon=True)
digest = CrossingDigest()
chunks = []
for event, _trace in iter_transport(spec):
    digest.update(event)
    trigger = scheduler.next(event["simulation_event_id"])
    chunks.append(stream.feed([(event, trigger)]))
tail = stream.close(source_sha256=digest.hexdigest())
result = assemble_result(chunks, tail)
```

- `normalize_config(settings)` validates a schema-4 configuration and returns an immutable `RunSpec`. `validate_capabilities(spec)` checks that the selected backend and writer are available.
- `iter_transport(spec, start=0, stop=None)` yields `(event, trace)` pairs in event order. Each event has its own random stream, so any slice `[start, stop)` is identical to the same events of the full run, and `propagate_event(spec, k)` returns event `k` alone.
- `TriggerScheduler(spec)` issues triggers one at a time. `peek_origin_ps()` returns the next trigger origin without consuming it, and `next(event_id)` consumes it.
- `ResponseStream(spec, pin_horizon=False)` applies the response to events as they arrive. `feed(pairs)` takes `(event, trigger)` pairs and returns a `ResponseChunk` (timeline entries, outcomes and signal hits for those events). `advance_to(origin_ps)` promises that no later trigger has an earlier origin. `hit_watermark_ps(detector)` and `frames_final_before_ps(detector)` report how far results are final. `close(source_sha256=...)` finishes the run and returns the tail (horizon and dark hits). `close(..., partial=True)` accepts fewer events than `spec.events`.
- `CrossingDigest` computes the sha256 that `intersections.jsonl` will have from the events as they are produced, so a streaming session can obtain the run's source digest without writing the file.
- `assemble_result(chunks, tail)` combines chunks and tail into a `ResponseResult`.
- `assemble_frames(spec, detector_id, start_ps, end_ps, timeline=..., hits=..., outcomes=..., horizon_ps=None)` builds the frames of one detector whose start lies in `[start_ps, end_ps)`. `assemble_frame_set(spec, frames, horizon_ps)` combines frames from all detectors into a `FrameSet`, and `frame_totals(frames)` returns the assignment and contributor-link counts. `build_readout(result, spec)` does the whole run at once.

## Streaming guarantees

- **Equivalence.** For any split of the events into consecutive chunks (including empty chunks) and any use of `peek_origin_ps` and `advance_to`, `assemble_result(chunks, tail)` equals `generate_response(events, spec, source_sha256=...)` exactly, as dataclasses and as serialised bytes. The stream and the batch function share one implementation of the numerical code.
- **Order.** Event IDs must be strictly increasing, trigger ordinals must equal the number of events consumed, and trigger origins must not decrease. Each trigger carries `physical_trigger_id`, which equals its zero-based ordinal. A violation raises `ResponseError`, after which the stream is failed and every later call raises.
- **Memory.** The stream keeps counters and compiled kernels, not per-event data. Its size does not depend on the number of events fed. In pinned mode it also holds the dark hits, at most `limits.dark_hits`.
- **Hit watermark.** After `advance_to(x)`, every hit of detector `D` produced by a later `feed` has `time_ps >= hit_watermark_ps(D)`.
- **Frame closure.** Every frame of detector `D` that starts before `frames_final_before_ps(D)` exists in the final readout and its content is final. Emitting disjoint intervals `[b0, b1), [b1, b2), ...` with each boundary at most the closure value at call time, and one last call after `close`, gives exactly the frames of the batch readout. Continuous windows are final only below the lower bound of the horizon; the number of windows is known at `close`.
- **Dark noise modes.** Dark hits are drawn over the whole run horizon, so by default they are produced only by `close()`, and a detector with a nonzero dark rate has no final frames before then. With `pin_horizon=True` the horizon is fixed to `timeline.duration_s` at construction; dark hits are available immediately as `stream.dark_hits`, results are identical to the batch, and the stream fails fast with `pinned dark-noise horizon exceeded` if a response would reach beyond the duration (choose `duration_s` with margin).
- **Errors.** On the same input the stream completes exactly when the batch function completes, with the same exception class; the first error reported can differ when an input has several problems. `ResponseStream` also validates the specification's capabilities when it is constructed (including any registered capability checks), which the batch function leaves to the caller. The stream additionally rejects event IDs that do not increase. Pinned mode fails where the batch function succeeds when a response would reach beyond `timeline.duration_s`.

## Extension points

Extension points live in `tracksim.registry` and are filled by importing the module that registers them; the package never imports or discovers other packages. Tables are process-global and append-only. Built-in entries come first and cannot be replaced. Names must be valid Python identifiers and unique.

| function | registers |
|---|---|
| `register_preset(Preset(name, apply, geometry_rows=None))` | a preset: `apply(detectors, timeline)` returns the detector tuple (it may set `label`, an opaque label the core never interprets); the optional `geometry_rows(spec)` hook supplies the rows of `geometry.json` |
| `register_backend(BackendInfo(...))` | a transport backend with its factory, configuration hook, worker limit, whether it models deposited charge (`deposition_available`), and the descriptive strings used in the metadata and the manifest |
| `register_writer(name, factory)` | a writer; the lifecycle in this package drives only the `json` writer |
| `register_capability_check(check)` | a function `check(spec, writer)` that raises `ValueError` to reject a combination |
| `register_schema(SchemaHandler(...))` | a configuration schema version |
| `tracksim.provenance.register_source_root(label, directory)` | a directory whose files are added to the source hashes as `label/<path>` |

Unknown names raise `ValueError` and list the registered alternatives, for example `registered presets: generic`. `backend_names()`, `writer_names()` and `preset_names()` return tuples in registration order; the manifest capability lists follow that order. A registering module might look like this (the names are placeholders):

```python
from tracksim import registry

registry.register_preset(registry.Preset("demo_preset", lambda detectors, timeline: detectors))
```

Response kernels are registered per instance: `ResponseRegistry().register(name, kernel)`, passed as `registry=` to `generate_response` or `ResponseStream`. Runs that use one are marked `provenance: "external"`.

## Determinism and provenance

- All randomness is keyed by the configuration: transport by `(seed, event_id)`, triggers by `seed`, efficiency and jitter by a hash of the seeds and the crossing, dark noise by the seeds. No wall clock is read except for the `created_utc` field of `manifest.json`, which is the only value that differs between two runs of the same configuration.
- The package files are hashed at the start of the run and again at the end; a change in between makes the run fail with `source changed`.
- The interpreter identity (version, platform, implementation, machine, random module version, C library) is recorded and compared by `verify`. Identical output across interpreter versions is not promised.
- Recorded hashes and identities are metadata, not signatures.

## Physics limitations

- Constant-momentum Gaussian primary core; no bremsstrahlung or secondary transport.
- Center-footprint silicon and extra thin sheets with configured paraxial air.
- Empirical nominal-MIP sensor response; the intrinsic energy dependence is not predicted. Deposited charge is not modelled (`deposited_e` is `null`).
- Structural consistency checks only; this is not a physics validation.
- The supported domain is 3 to 6 GeV electrons and paraxial beams.

## Running the end-to-end suite

The checks in `tests/e2e/` run the code from `src` directly, use no network, and need only the standard library:

```console
python3 tests/e2e/run_all.py --evidence evidence
```

This runs every script in its own interpreter and writes `evidence/e2e-report.json` with the status of each check and the sha256 of each evidence file. A script can also be run alone, for example `python3 tests/e2e/stream_equivalence.py --evidence evidence`. Checks that register extension points run in separate processes because registrations cannot be undone.

`python3 tests/e2e/run_all.py --record-golden` writes `tests/e2e/golden/examples.json` with the artifact hashes of both examples for the current interpreter. The golden comparison runs only when the interpreter identity matches the recorded one and is skipped otherwise.
