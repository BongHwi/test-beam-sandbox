"""Interval readout checks O1-O6."""

from __future__ import annotations

import copy
import math
import random
import sys
import traceback

import _support as support

support.use_source()

from tracksim.readout import assemble_frame_set, assemble_frames, build_readout, frame_totals  # noqa: E402

DIGEST = support.DEMO_DIGEST


def overlapping(settings: dict) -> dict:
    out = copy.deepcopy(settings)
    for detector in out["detectors"]:
        if detector["readout"]["model"] == "triggered":
            detector["readout"] = {"model": "triggered", "gate_ns": 1_000_000}
    return out


def late_empty(settings: dict) -> dict:
    out = copy.deepcopy(settings)
    out["timeline"]["duration_s"] = 0.01
    return out


def scenarios() -> dict:
    base = support.example_settings("three-plane")
    dark = support.example_settings("three-plane-dark")
    mixed = support.set_dark(base, {"PLANE_B": 2.0, "PLANE_C": 0.5})
    return {
        "dark-free": dict(settings=base, pin=False, strip=None),
        "mixed-dark": dict(settings=mixed, pin=False, strip=None),
        "pinned-dark": dict(settings=dark, pin=True, strip=None),
        "late-empty": dict(settings=late_empty(base), pin=False, strip=30),
        "overlapping-gates": dict(settings=overlapping(base), pin=False, strip=None),
    }


_cache: dict = {}


def prepare(name: str) -> dict:
    """Events, batch result, batch frames and the progressive run for one scenario."""
    if name in _cache:
        return _cache[name]
    scenario = scenarios()[name]
    spec = support.make_spec(scenario["settings"])
    events = support.transport_events(spec)
    if scenario["strip"] is not None:
        events = support.strip_crossings(events, scenario["strip"])
    batch = support.batch_result(spec, events)
    expected = build_readout(batch, spec)
    run = support.progressive_readout(spec, events, pin=scenario["pin"])
    _cache[name] = dict(spec=spec, events=events, batch=batch, expected=expected, run=run)
    return _cache[name]


def scenario_property(name: str, data: dict) -> bool:
    """True when the scenario really has the feature it is named for."""
    batch, spec = data["batch"], data["spec"]
    if name == "late-empty":
        return batch.timeline[-1].origin_ps >= batch.horizon_ps
    if name == "overlapping-gates":
        frames = [f for f in data["expected"].frames if f.model == "triggered"]
        return any(b.start_ps < a.end_ps for a, b in zip(frames, frames[1:])
                   if a.detector == b.detector)
    if name == "pinned-dark":
        return batch.horizon_ps == round(spec.timeline.duration_s * 10**12) and batch.dark_hits > 0
    if name == "mixed-dark":
        return batch.dark_hits > 0 and any(
            d.response.dark_hit_rate_per_pixel_hz == 0 for d in spec.detectors)
    return batch.dark_hits == 0


def o1(report):
    observed, expected, summary = {}, {}, {}
    for name in scenarios():
        data = prepare(name)
        run, want = data["run"], data["expected"]
        observed[name] = {
            "frame_set_equal": run["frame_set"] == want,
            "frames_sha256": support.frames_sha(run["frame_set"]),
            "emitted_before_close": run["before_close"] > 0,
            "scenario_has_its_feature": scenario_property(name, data),
        }
        expected[name] = {
            "frame_set_equal": True,
            "frames_sha256": support.frames_sha(want),
            "emitted_before_close": True,
            "scenario_has_its_feature": True,
        }
        summary[name] = {"frame_set_sha256": observed[name]["frames_sha256"],
                         "frames_total": len(want.frames),
                         "frames_emitted_before_close": run["before_close"],
                         "horizon_ps": data["batch"].horizon_ps,
                         "last_origin_ps": data["batch"].timeline[-1].origin_ps}
    report.extra["O1_frame_sets"] = summary
    return observed, expected


def o2():
    observed, expected, intervals = {}, {}, {}
    for name in scenarios():
        data = prepare(name)
        batch_frames = {(f.detector, f.index): f for f in data["expected"].frames}
        first_bad = None
        for frame in data["run"]["frames"]:
            if batch_frames.get((frame.detector, frame.index)) != frame:
                interval = [e for e in data["run"]["log"]
                            if e["detector"] == frame.detector
                            and e["start_ps"] <= frame.start_ps < e["end_ps"]]
                first_bad = {"detector": frame.detector, "index": frame.index,
                             "interval": interval[:1]}
                break
        observed[name] = {"first_mismatch": first_bad,
                          "emitted": len(data["run"]["frames"]) == len(batch_frames)}
        expected[name] = {"first_mismatch": None, "emitted": True}
    return observed, expected


def o3():
    observed, expected = {}, {}
    for name in scenarios():
        data = prepare(name)
        spec, horizon = data["spec"], data["batch"].horizon_ps
        windows = {d.id: d.readout.window_ps for d in spec.detectors
                   if d.readout.model == "continuous"}
        bad = [(f.detector, f.index) for f in data["run"]["frames"]
               if f.detector in windows
               and f.index >= math.ceil(horizon / windows[f.detector])]
        observed[name] = bad
        expected[name] = []
    return observed, expected


def o4():
    golden = support.load_golden()
    if golden is None:
        return "skip", "golden/examples.json is not recorded"
    if golden["python"] != support.python_identity():
        return "skip", "identity differs"
    observed, expected = {}, {}
    for name in support.EXAMPLE_NAMES:
        spec = support.make_spec(support.example_settings(name))
        events = support.transport_events(spec)
        batch = support.batch_result(spec, events)
        observed[name] = support.frames_sha(build_readout(batch, spec))
        expected[name] = golden["examples"][name]["frames.jsonl"]
    return observed, expected


def o5():
    observed, expected = {}, {}
    for name in ("pinned-dark", "overlapping-gates", "dark-free"):
        data = prepare(name)
        spec, batch, frame_set = data["spec"], data["batch"], data["expected"]
        final = max(batch.horizon_ps, batch.timeline[-1].origin_ps + 1)
        rng = random.Random(55)
        ok = []
        for detector in spec.detectors:
            want = [f for f in frame_set.frames if f.detector == detector.id]
            for _trial in range(20):
                cuts = sorted(rng.randint(0, final) for _ in range(rng.randint(1, 8)))
                bounds = [0, *cuts, final]
                got = []
                for a, b in zip(bounds, bounds[1:]):
                    got.extend(assemble_frames(
                        spec, detector.id, a, b, timeline=batch.timeline,
                        hits=batch.hits,
                        outcomes={o.id: o for o in batch.outcomes},
                        horizon_ps=batch.horizon_ps))
                ok.append(got == want)
        observed[name] = {"partitions": len(ok), "all_equal": all(ok)}
        expected[name] = {"partitions": len(ok), "all_equal": True}
    return observed, expected


def recount(spec, batch, frame_set) -> tuple[int, int]:
    """Assignments and contributor links counted from hits and outcomes, not from the frames' own fields."""
    outcomes = {o.id: o for o in batch.outcomes}
    by_detector: dict = {}
    for hit in batch.hits:
        by_detector.setdefault(hit.detector, []).append(hit)
    assignments = links = 0
    for frame in frame_set.frames:
        inside = [h for h in by_detector.get(frame.detector, [])
                  if frame.start_ps <= h.time_ps < frame.end_ps]
        assignments += len(inside)
        pixels: dict = {}
        for hit in inside:
            if hit.response_id is not None:
                outcome = outcomes[hit.response_id]
                pixels.setdefault((hit.col, hit.row), set()).add(
                    (outcome.simulation_event_id, outcome.track_id))
        links += sum(len(v) for v in pixels.values())
    return assignments, links


def o6():
    observed, expected = {}, {}
    for name in scenarios():
        data = prepare(name)
        want = data["expected"]
        counted = list(recount(data["spec"], data["batch"], want))
        observed[name] = {"stored_totals": [want.assignments, want.contributor_links],
                          "frame_totals": list(frame_totals(want.frames))}
        expected[name] = {"stored_totals": counted, "frame_totals": counted}
        progressive = data["run"]["frame_set"]
        observed[name + " (progressive)"] = [progressive.assignments, progressive.contributor_links]
        expected[name + " (progressive)"] = counted
    return observed, expected


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("readout_intervals", args.evidence)
    report.check("O1", lambda: o1(report))
    report.check("O2", o2)
    report.check("O3", o3)
    try:
        outcome = o4()
    except Exception:
        report.add("O4", "fail", {"exception": traceback.format_exc(limit=6)}, None,
                   "check raised")
    else:
        if outcome[0] == "skip":
            report.skip("O4", outcome[1])
        else:
            report.check("O4", lambda: outcome)
    report.check("O5", o5)
    report.check("O6", o6)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
