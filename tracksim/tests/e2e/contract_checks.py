"""Contract and boundary checks B1-B13.

Trigger numbering, input domains, limits, the pinned horizon, the event contract, the
watermark bound, runtime drift and the verification rules, each with a case that must fail.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

import _support as support

support.use_source()

from tracksim import specs as specs_module  # noqa: E402
from tracksim import response as response_module  # noqa: E402
from tracksim.errors import ResponseError  # noqa: E402
from tracksim.events import EventValidationError, validate_event  # noqa: E402
from tracksim.readout import assemble_frames  # noqa: E402
from tracksim.response_models import _stable_normal  # noqa: E402
from tracksim.stream import ResponseStream, TriggerScheduler  # noqa: E402

DIGEST = support.DEMO_DIGEST
ENERGY_PATH = ("beam", "kinetic_energy_gev")


def error_of(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc).__name__, str(exc)
    return None, None


def base() -> dict:
    return support.example_settings("three-plane")


def make_run(directory: Path, name: str = "three-plane") -> Path:
    out = directory / f"run-{name}"
    proc = support.generate_example(name, out, cwd=directory)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-400:])
    return out


def verify(run: Path, cwd: Path):
    proc = support.run_cli(["verify", run], cwd=cwd)
    report = json.loads(proc.stdout) if proc.returncode == 0 else None
    return proc, report


# -------------------------------------------------------------------------- B1


def b1():
    spec = support.make_spec(base())
    events = support.transport_events(spec)
    result = support.batch_result(spec, events)
    scheduler = TriggerScheduler(spec)
    scheduled = [scheduler.next(e["simulation_event_id"]).physical_trigger_id for e in events]
    frames = support.progressive_readout(spec, events)["frame_set"].frames
    triggered = [f for f in frames if f.model == "triggered"]
    with tempfile.TemporaryDirectory(prefix="e2e-b1-") as tmp:
        run = make_run(Path(tmp))
        stored = [json.loads(line) for line in (run / "response.jsonl").read_text().splitlines()]
    file_ids = [r["physical_trigger_id"] for r in stored if r["record"] == "timeline"]
    n = len(events)
    observed = {
        "batch_timeline": [t.physical_trigger_id for t in result.timeline] == list(range(n)),
        "scheduler": scheduled == list(range(n)),
        "triggered_frames": [f.trigger_id for f in triggered] == list(range(n)),
        "response_file": file_ids == list(range(len(file_ids))) and len(file_ids) == n,
        "trigger_number": [response_module.trigger_number(k) for k in (0, 1, 7)],
    }
    expected = {"batch_timeline": True, "scheduler": True, "triggered_frames": True,
                "response_file": True, "trigger_number": [0, 1, 7]}
    return observed, expected


# -------------------------------------------------------------------------- B2


def with_energy(value) -> dict:
    settings = base()
    settings["beam"]["kinetic_energy_gev"] = value
    return settings


def b2():
    accepted = {str(v): error_of(lambda v=v: specs_module.normalize_config(with_energy(v)))[0]
                for v in (3.0, 4.2, 6.0)}
    rejected, messages = {}, {}
    for v in (2.999, 6.001, 6.5, 100.0):
        kind, message = error_of(lambda v=v: specs_module.normalize_config(with_energy(v)))
        rejected[str(v)] = kind
        messages[str(v)] = message
    observed = {"accepted": accepted, "rejected": rejected}
    expected = {"accepted": {k: None for k in accepted},
                "rejected": {k: "ValueError" for k in rejected}}
    return observed, expected, messages


# -------------------------------------------------------------------------- B3


def b3():
    defaults = dataclasses.asdict(specs_module.Limits())
    observed, expected, messages = {}, {}, {}
    for name, default in defaults.items():
        row = {}
        for label, value in (("default", default), ("lowered", max(1, default // 2)),
                             ("minimum", 3 if name == "detectors" else 1), ("raised", default + 1), ("zero", 0)):
            settings = base()
            settings["limits"] = {name: value}
            kind, message = error_of(lambda s=settings: specs_module.normalize_config(s))
            row[label] = kind
            if kind:
                messages[f"{name}.{label}"] = message
        observed[name] = row
        expected[name] = {"default": None, "lowered": None, "minimum": None,
                          "raised": "ValueError", "zero": "ValueError"}
    return observed, expected, messages


# -------------------------------------------------------------------------- B4


def b4():
    settings = support.example_settings("three-plane-dark")
    spec = support.make_spec(settings)
    events = support.transport_events(spec)
    batch = support.batch_result(spec, events)
    latest = max(o.time_ps for o in batch.outcomes if o.eligible)
    target = latest + spec.timeline.tail_padding_ps

    def run(horizon_ps):
        tuned = copy.deepcopy(settings)
        tuned["timeline"]["duration_s"] = horizon_ps / 1e12
        tuned_spec = support.make_spec(tuned)
        exact = round(tuned_spec.timeline.duration_s * 1e12) == horizon_ps
        try:
            result, *_rest = support.stream_result(
                tuned_spec, events, [len(events)], pin=True)
            reference = support.batch_result(tuned_spec, events)
            return {"exact_horizon": exact, "error": None,
                    "equals_batch": result == reference, "horizon_ps": result.horizon_ps}
        except Exception as exc:
            return {"exact_horizon": exact, "error": type(exc).__name__,
                    "message": str(exc)}

    at_limit, over = run(target), run(target - 1)
    observed = {"horizon_equals_last_response_plus_tail": at_limit, "one_ps_short": over}
    expected = {
        "horizon_equals_last_response_plus_tail": {
            "exact_horizon": True, "error": None, "equals_batch": True, "horizon_ps": target},
        "one_ps_short": {"exact_horizon": True, "error": "ResponseError",
                         "message": "pinned dark-noise horizon exceeded"},
    }
    return observed, expected


# -------------------------------------------------------------------------- B5


def b5():
    with tempfile.TemporaryDirectory(prefix="e2e-b5-") as name:
        tmp = Path(name)
        run = make_run(tmp)
        forged = tmp / "forged"
        shutil.copytree(run, forged)
        info = support.forge_python_identity(forged)
        control_proc, control = verify(run, tmp)
        proc, report = verify(forged, tmp)
    observed = {
        "control": control and control["status"],
        "forged_exit": proc.returncode,
        "forged_status": report and report["status"],
        "runtime_status": report and report["runtime_status"],
        "python_runtime_status": report and report["python_runtime_status"],
        "source_status": report and report["source_status"],
        "files_rewritten": info["rewritten"],
    }
    expected = {"control": "verified", "forged_exit": 0,
                "forged_status": "verified_with_runtime_drift", "runtime_status": "drifted",
                "python_runtime_status": "drifted", "source_status": "matches",
                "files_rewritten": ["python-runtime.json", "runtime.json",
                                    "transport-envelope.json", "manifest.json"]}
    return observed, expected


# -------------------------------------------------------------------------- B6


def b6():
    spec = support.make_spec(base())
    ids = [d.id for d in spec.detectors]
    event = support.transport_events(spec)[0]

    def mutate(fn):
        copy_ = copy.deepcopy(event)
        fn(copy_)
        return copy_

    first_plane = ids[0]
    cases = {
        "no primary track": lambda e: e["tracks"][0].update(primary=False),
        "two primary tracks": lambda e: e["tracks"].append(
            {"track_id": 1, "parent_track_id": None, "parent_unrecorded": False,
             "primary": True, "pdg": 11}),
        "parent_track_id missing": lambda e: e["tracks"][0].pop("parent_track_id"),
        "duplicate track id": lambda e: e["tracks"].append(copy.deepcopy(e["tracks"][0])),
        "missing plane": lambda e: e["planes"].pop(first_plane),
        "extra plane": lambda e: e["planes"].update({"PLANE_Z": {"mc": []}}),
        "crossing pdg disagrees": lambda e: e["planes"][first_plane]["mc"][0].update(pdg=22),
        "deposit given for fast": lambda e: e["planes"][first_plane]["mc"][0].update(deposited_e=1.0),
        "negative time": lambda e: e["planes"][first_plane]["mc"][0].update(global_time_ns=-1.0),
        "non-finite position": lambda e: e["planes"][first_plane]["mc"][0].update(
            local_mm=[math.nan, 0.0, 0.0]),
        "wrong crossing schema": lambda e: e.update(schema="demo.v0"),
        "negative event id": lambda e: e.update(simulation_event_id=-1),
        "tracks empty": lambda e: e.update(tracks=[]),
    }
    observed, messages = {}, {}
    kind, _m = error_of(lambda: validate_event(copy.deepcopy(event), ids, deposition_available=False))
    observed["control"] = kind
    for label, fn in cases.items():
        kind, message = error_of(
            lambda fn=fn: validate_event(mutate(fn), ids, deposition_available=False))
        observed[label] = kind
        messages[label] = message
    expected = {label: "EventValidationError" for label in cases}
    expected["control"] = None
    return observed, expected, messages


# -------------------------------------------------------------------------- B7


def b7():
    from tracksim import stream as stream_module

    from tracksim import response_models

    # Force the uniform draw to its minimum: the result is the largest value the code can produce.
    original = response_models._stable_uniform
    response_models._stable_uniform = lambda key: 0.0
    try:
        analytic = response_models._stable_normal("extreme")
    finally:
        response_models._stable_uniform = original
    bound_specs, bound_stream = specs_module.JITTER_NORMAL_BOUND, stream_module.JITTER_NORMAL_BOUND
    sample = [_stable_normal(f"bound-sample:{i}") for i in range(300_000)]
    largest = max(abs(v) for v in sample)

    spec = support.make_spec(support.stress_settings(20_000))
    events = support.transport_events(spec)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    ids = [d.id for d in spec.detectors]
    violations, checked = 0, 0
    for event in events:
        stream.advance_to(scheduler.peek_origin_ps())
        marks = {d: stream.hit_watermark_ps(d) for d in ids}
        chunk = stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
        for o in chunk.outcomes:
            if o.eligible:
                checked += 1
                violations += o.time_ps < marks[o.detector]
    observed = {
        "specs_bound_covers_analytic_maximum": bound_specs >= analytic,
        "stream_bound_covers_analytic_maximum": bound_stream >= analytic,
        "bound_is_not_loose": bound_specs <= analytic + 0.1,
        "sample_inside_bound": largest <= bound_specs,
        "stress_violations": violations,
        "stress_crossings_checked": checked > 50_000,
    }
    expected = {"specs_bound_covers_analytic_maximum": True,
                "stream_bound_covers_analytic_maximum": True, "bound_is_not_loose": True,
                "sample_inside_bound": True, "stress_violations": 0,
                "stress_crossings_checked": True}
    return observed, expected, {"analytic": analytic, "bound": bound_specs,
                                "sample_largest": largest, "checked": checked}


# -------------------------------------------------------------------------- B8


def edit_manifest(run: Path, fn) -> None:
    path = run / "manifest.json"
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def b8():
    observed, messages = {}, {}
    cases = {
        "control": lambda m: None,
        "requested writer differs": lambda m: m["capabilities"]["requested"].update(writer="demo_writer"),
        "requested transport differs": lambda m: m["capabilities"]["requested"].update(transport="demo_backend"),
        "implemented transport lacks own backend": lambda m: m["capabilities"]["implemented"].update(transport=["demo_backend"]),
        "implemented response list differs": lambda m: m["capabilities"]["implemented"].update(response=["boundary_resolution"]),
        "implemented readout list differs": lambda m: m["capabilities"]["implemented"].update(readout=["triggered"]),
        "implemented writers lack own writer": lambda m: m["capabilities"]["implemented"].update(writers=["demo_writer"]),
        "extra implemented names are accepted": lambda m: (
            m["capabilities"]["implemented"].update(
                transport=["fast_electron", "demo_backend"], writers=["json", "demo_writer"])),
    }
    with tempfile.TemporaryDirectory(prefix="e2e-b8-") as name:
        tmp = Path(name)
        run = make_run(tmp)
        for label, fn in cases.items():
            copy_dir = tmp / ("case-" + label.replace(" ", "-"))
            shutil.copytree(run, copy_dir)
            edit_manifest(copy_dir, fn)
            proc, report = verify(copy_dir, tmp)
            observed[label] = {"exit": proc.returncode, "status": report and report["status"],
                               "message": proc.stderr.startswith("tracksim: error:")
                               if proc.returncode else None}
            messages[label] = proc.stderr.strip()[-200:]
    accepted = {"exit": 0, "status": "verified", "message": None}
    rejected = {"exit": 1, "status": None, "message": True}
    expected = {label: (accepted if label in ("control", "extra implemented names are accepted")
                        else rejected) for label in cases}
    return observed, expected, messages


# -------------------------------------------------------------------------- B9


def b9():
    spec = support.make_spec(base())
    events = support.transport_events(spec)
    observed, messages = {}, {}
    for field in ("response_delay_ns", "jitter_ns"):
        for value in (1e300, 1e17):
            settings = base()
            settings["detectors"][0]["response"][field] = value
            kind, message = error_of(lambda s=settings: specs_module.normalize_config(s))
            observed[f"normalise {field}={value:g}"] = {
                "error": kind, "names_field": field in (message or "")}
            with tempfile.TemporaryDirectory(prefix="e2e-b9-") as tmp:
                proc, _out = cli_generate(settings, Path(tmp))
            observed[f"cli {field}={value:g}"] = {
                "exit": proc.returncode, "prefix": proc.stderr.startswith("tracksim: error:"),
                "traceback": "Traceback" in proc.stderr}
            messages[f"{field}={value:g}"] = message
    expected = {}
    for key in observed:
        expected[key] = ({"error": "ValueError", "names_field": True} if key.startswith("normalise")
                         else {"exit": 1, "prefix": True, "traceback": False})
    # arithmetic failure after normalisation (spec rebuilt by hand): both paths fail alike
    huge = list(spec.detectors)
    huge[0] = dataclasses.replace(
        huge[0], response=dataclasses.replace(huge[0].response, response_delay_ns=1e306))
    bad = dataclasses.replace(spec, detectors=tuple(huge))
    batch_kind, batch_message = error_of(lambda: support.batch_result(bad, events))
    stream_kind, stream_message = error_of(
        lambda: support.stream_result(bad, events, [len(events)]))
    observed["batch arithmetic failure"] = batch_kind
    observed["stream arithmetic failure"] = stream_kind
    expected["batch arithmetic failure"] = "ResponseError"
    expected["stream arithmetic failure"] = "ResponseError"
    messages["batch"], messages["stream"] = batch_message, stream_message
    return observed, expected, messages


def cli_generate(settings: dict, tmp: Path):
    config = tmp / "config.json"
    config.write_text(json.dumps(settings))
    out = tmp / "out"
    return support.run_cli(["generate", "--config", config, "--out", out], cwd=tmp), out


# ------------------------------------------------------------------------- B10


def b10():
    observed, expected = {}, {}
    scenarios = {
        "dark-free": (base(), False),
        "pinned-dark": (support.example_settings("three-plane-dark"), True),
        "short-duration": ({**base(), "timeline": {**base()["timeline"], "duration_s": 0.01}},
                           False),
    }
    for name, (settings, pin) in scenarios.items():
        spec = support.make_spec(settings)
        events = support.transport_events(spec)
        scheduler = TriggerScheduler(spec)
        stream = ResponseStream(spec, pin_horizon=pin)
        ids = [d.id for d in spec.detectors]
        history = {d: [stream.frames_final_before_ps(d)] for d in ids}
        for event in events:
            stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
            for d in ids:
                history[d].append(stream.frames_final_before_ps(d))
        stream.close(source_sha256=DIGEST)
        for d in ids:
            history[d].append(stream.frames_final_before_ps(d))
        observed[name] = {d: all(b >= a for a, b in zip(h, h[1:])) for d, h in history.items()}
        expected[name] = {d: True for d in ids}
    spec = support.make_spec(base())
    events = support.transport_events(spec)
    result = support.batch_result(spec, events)
    outcomes = {o.id: o for o in result.outcomes}
    empty = {}
    for label, (a, b) in {"start equals end": (1000, 1000), "start after end": (5000, 10)}.items():
        empty[label] = [len(assemble_frames(spec, d.id, a, b, timeline=result.timeline,
                                            hits=result.hits, outcomes=outcomes,
                                            horizon_ps=result.horizon_ps))
                        for d in spec.detectors]
    late = copy.deepcopy(base())
    late["events"], late["timeline"]["rate_hz"], late["timeline"]["duration_s"] = 3, 5, 0.001
    spec = support.make_spec(late)
    events = support.transport_events(spec)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    timeline, hits, outcomes = [], [], {}
    for event in events:
        stream.advance_to(scheduler.peek_origin_ps())
        chunk = stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
        timeline += chunk.timeline
        hits += chunk.hits
        outcomes.update({o.id: o for o in chunk.outcomes})
    stream.advance_to(scheduler.peek_origin_ps())
    open_bound = stream.frames_final_before_ps("PLANE_A")
    tail = stream.close(source_sha256=DIGEST)
    closed_bound = stream.frames_final_before_ps("PLANE_A")
    kind, _m = error_of(lambda: assemble_frames(
        spec, "PLANE_A", open_bound, closed_bound, timeline=timeline, hits=hits,
        outcomes=outcomes, horizon_ps=tail.horizon_ps))
    observed["bound after the last advance_to"] = {
        "closed_at_least_open": closed_bound >= open_bound, "last_call_error": kind}
    expected["bound after the last advance_to"] = {
        "closed_at_least_open": True, "last_call_error": None}
    observed["empty interval"] = empty
    expected["empty interval"] = {k: [0, 0, 0] for k in empty}
    return observed, expected


# ------------------------------------------------------------------------- B11


def b11():
    observed = {}
    with tempfile.TemporaryDirectory(prefix="e2e-b11-") as name:
        tmp = Path(name)
        run = make_run(tmp)
        for label, state in {"control": None, "failed": {"status": "failed", "error": "x"},
                             "responding": {"status": "responding"}, "empty object": {}}.items():
            copy_dir = tmp / ("case-" + label.replace(" ", "-"))
            shutil.copytree(run, copy_dir)
            if state is not None:
                (copy_dir / "run-state.json").write_text(json.dumps(state))
            proc, _r = verify(copy_dir, tmp)
            observed[label] = {"exit": proc.returncode,
                               "message": proc.stderr.startswith("tracksim: error:")
                               if proc.returncode else None}
    expected = {"control": {"exit": 0, "message": None}}
    expected.update({k: {"exit": 1, "message": True}
                     for k in ("failed", "responding", "empty object")})
    return observed, expected


# ------------------------------------------------------------------------- B12


def b12():
    observed = {}
    roots = {"array": "[1, 2]", "number": "3", "string": '"x"', "null": "null"}
    with tempfile.TemporaryDirectory(prefix="e2e-b12-") as name:
        tmp = Path(name)
        for label, text in roots.items():
            config = tmp / f"{label}.json"
            config.write_text(text)
            for variant, extra in (("plain", []), ("overrides", ["--events", "5", "--seed", "3"])):
                out = tmp / f"out-{label}-{variant}"
                proc = support.run_cli(["generate", "--config", config, "--out", out, *extra],
                                       cwd=tmp)
                observed[f"generate {label} {variant}"] = {
                    "exit": proc.returncode, "prefix": proc.stderr.startswith("tracksim: error:"),
                    "traceback": "Traceback" in proc.stderr, "out_exists": out.exists()}
            proc = support.run_cli(["preflight", "--config", config], cwd=tmp)
            observed[f"preflight {label}"] = {
                "exit": proc.returncode, "prefix": proc.stderr.startswith("tracksim: error:"),
                "traceback": "Traceback" in proc.stderr}
    expected = {k: ({"exit": 1, "prefix": True, "traceback": False}
                    if k.startswith("preflight")
                    else {"exit": 1, "prefix": True, "traceback": False, "out_exists": False})
                for k in observed}
    return observed, expected


# ------------------------------------------------------------------------- B13


def b13():
    with tempfile.TemporaryDirectory(prefix="e2e-b13-") as name:
        tmp = Path(name)
        run = make_run(tmp)
        proc, _report = verify(run, tmp)
        load = lambda n: json.loads((run / n).read_text())  # noqa: E731
        manifest, acceptance = load("manifest.json"), load("acceptance.json")
        verification, metadata = load("verification/report.json"), load("metadata.json")
        normalized = load("normalized-spec.json")
    caps = manifest["capabilities"]
    detector_keys = {"id", "pixels", "pitch_um", "position_mm", "orientation_deg", "active_um",
                     "passive_um", "response", "readout"}
    labelled = sorted({k for d in normalized["detectors"] for k in d} - detector_keys)
    observed = {
        "manifest keys": sorted(manifest),
        "capabilities keys": {k: sorted(caps[k]) for k in ("requested", "implemented")},
        "acceptance keys": sorted(acceptance),
        "verification keys": sorted(verification),
        "unexpected detector keys": labelled,
        "criterion mentions writer": "writer" in acceptance["criterion"].lower(),
        "physics text": metadata["limitations"]["physics"],
        "third limitation": manifest["limitations"][2],
        "synthetic assumption": manifest["assumptions"]["synthetic"],
        "metadata limitation keys": sorted(metadata["limitations"]),
    }
    expected = {
        "manifest keys": ["acceptance_level", "algorithm", "assumptions", "backend",
                          "capabilities", "complete", "created_utc", "deposition_available",
                          "files", "layout_scope", "limitations", "runtime", "schema",
                          "source_hashes", "synthetic", "writer"],
        "capabilities keys": {"requested": ["readout", "response", "transport", "writer"],
                              "implemented": ["readout", "response", "transport", "writers"]},
        "acceptance keys": ["acceptance_level", "backend", "criterion", "deposition_available",
                            "limitations", "neutral_verification", "pipeline_pass", "writer"],
        "verification keys": ["acceptance_level", "backend", "changed_sources",
                              "current_python", "files_checked", "neutral_verification",
                              "provenance_note", "python_runtime_status", "recorded_python",
                              "runtime_status", "schema", "source_status", "status",
                              "transport_verification", "valid", "writer",
                              "writer_verification"],
        "unexpected detector keys": [], "criterion mentions writer": False,
        "physics text": "empirical binary response; charge transport in the sensor is not modelled",
        "third limitation": "structural consistency checks only; this is not a physics validation",
        "synthetic assumption": "synthetic electron source",
        "metadata limitation keys": ["external_response", "material", "physics", "transport"],
    }
    return observed, expected


# ------------------------------------------------------------------------- B14


def b14():
    spec = support.make_spec(base())
    fp = specs_module.response_fingerprint
    first = spec.detectors[0]
    variants = {
        "events": dataclasses.replace(spec, events=spec.events - 1),
        "seed": dataclasses.replace(spec, seed=spec.seed + 1),
        "device_seed": dataclasses.replace(spec, device_seed=spec.device_seed + 1),
        "rate_hz": dataclasses.replace(spec, timeline=dataclasses.replace(
            spec.timeline, rate_hz=spec.timeline.rate_hz * 2)),
        "duration_s": dataclasses.replace(spec, timeline=dataclasses.replace(
            spec.timeline, duration_s=spec.timeline.duration_s * 2)),
        "tail_padding_ps": dataclasses.replace(spec, timeline=dataclasses.replace(
            spec.timeline, tail_padding_ps=spec.timeline.tail_padding_ps + 1)),
        "efficiency": dataclasses.replace(spec, detectors=(dataclasses.replace(
            first, response=dataclasses.replace(first.response, efficiency=0.5)),
            *spec.detectors[1:])),
        "pitch": dataclasses.replace(spec, detectors=(dataclasses.replace(
            first, pitch_um=(first.pitch_um[0] + 1, first.pitch_um[1])), *spec.detectors[1:])),
    }
    same = fp(dataclasses.replace(spec))
    observed = {"identical_spec_same_hash": same == fp(spec)}
    observed.update({name: fp(changed) != fp(spec) for name, changed in variants.items()})
    # a run stopped early is distinguishable from a full one
    events = support.transport_events(spec)
    full, *_a = support.stream_result(spec, events, [len(events)])
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    stream.feed([(e, scheduler.next(e["simulation_event_id"])) for e in events[:17]])
    partial = stream.close(source_sha256=DIGEST, partial=True)
    observed["partial_run_hash_differs"] = partial.response_spec_hash != full.response_spec_hash
    return observed, {k: True for k in observed}


# ------------------------------------------------------------------------- B15


def b15():
    """A run interrupted after the manifest was written is completed by finalize."""
    from tracksim import lifecycle

    with tempfile.TemporaryDirectory(prefix="e2e-b15-") as name:
        tmp = Path(name)
        run = make_run(tmp)
        verify(run, tmp)
        stale = tmp / "interrupted"
        shutil.copytree(run, stale)
        (stale / "run-state.json").write_text(json.dumps({"status": "finalizing"}))
        before = verify(stale, tmp)[0].returncode
        manifest_before = (stale / "manifest.json").read_bytes()
        kind, message = error_of(lambda: lifecycle.finalize(stale))
        state = json.loads((stale / "run-state.json").read_text())
        proc, report = verify(stale, tmp)
        manifest_same = (stale / "manifest.json").read_bytes() == manifest_before
        # control: a complete run stays untouched
        done = tmp / "done"
        shutil.copytree(run, done)
        stamp = (done / "verification" / "report.json").stat().st_mtime_ns
        lifecycle.finalize(done)
        untouched = (done / "verification" / "report.json").stat().st_mtime_ns == stamp
    observed = {"verify_before": before, "finalize_error": kind,
                "manifest_unchanged": manifest_same,
                "state_after": state.get("status"), "verify_after": proc.returncode,
                "status_after": report and report["status"], "complete_run_untouched": untouched}
    expected = {"verify_before": 1, "finalize_error": None, "manifest_unchanged": True,
                "state_after": "complete", "verify_after": 0, "status_after": "verified",
                "complete_run_untouched": True}
    return observed, expected, {"message": message}


# ------------------------------------------------------------------------- B16


def b16():
    spec = support.make_spec(base())
    bare = dataclasses.replace(spec, transport=None)
    probes = {
        "backend": lambda: bare.backend,
        "spec_to_dict": lambda: specs_module.spec_to_dict(bare),
        "transport_fingerprint": lambda: specs_module.transport_fingerprint(bare),
        "response_fingerprint": lambda: specs_module.response_fingerprint(bare),
        "deposition_available": lambda: bare.deposition_available,
    }
    observed, messages = {}, {}
    for label, fn in probes.items():
        kind, message = error_of(fn)
        observed[label] = [kind, "spec declares no transport backend" in (message or "")]
        messages[label] = message
    expected = {label: ["ValueError", True] for label in probes}
    return observed, expected, messages


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("contract_checks", args.evidence)

    def with_detail(cid, fn):
        holder = {}

        def wrapped():
            result = fn()
            holder["detail"] = result[2] if len(result) > 2 else None
            return result[0], result[1]

        report.check(cid, wrapped)
        if holder.get("detail") is not None:
            report.checks[-1]["details"] = support.jsonable(holder["detail"])

    for cid, fn in (("B1", b1), ("B2", b2), ("B3", b3), ("B4", b4), ("B5", b5), ("B6", b6),
                    ("B7", b7), ("B8", b8), ("B9", b9), ("B10", b10), ("B11", b11),
                    ("B12", b12), ("B13", b13), ("B14", b14), ("B15", b15), ("B16", b16)):
        with_detail(cid, fn)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
