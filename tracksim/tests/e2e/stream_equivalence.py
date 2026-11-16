"""Streaming response checks R1-R11."""

from __future__ import annotations

import copy
import dataclasses
import random
import sys
import tempfile
from pathlib import Path

import _support as support

support.use_source()

from tracksim import lifecycle, neutral  # noqa: E402
from tracksim import response as response_module  # noqa: E402
from tracksim.errors import ResponseError  # noqa: E402
from tracksim.records import Trigger  # noqa: E402
from tracksim.response import ResponseRegistry, schedule  # noqa: E402
from tracksim.stream import (  # noqa: E402
    CrossingDigest,
    ResponseStream,
    TriggerScheduler,
    assemble_result,
)

DIGEST = support.DEMO_DIGEST


def configs() -> dict:
    return {
        "three-plane": support.example_settings("three-plane"),
        "three-plane-dark": support.example_settings("three-plane-dark"),
        "stress": support.stress_settings(200),
    }


def error_name(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc).__name__, str(exc)
    return None, None


def exact_triggers(spec, events):
    scheduler = TriggerScheduler(spec)
    return [scheduler.next(e["simulation_event_id"]) for e in events]


# -------------------------------------------------------------------------- R1


def r1(report):
    table, observed, expected = {}, {}, {}
    for name, settings in configs().items():
        spec = support.make_spec(settings)
        events = support.transport_events(spec)
        batch = support.batch_result(spec, events)
        batch_hash = support.response_sha(batch)
        observed[name], expected[name] = {}, {}
        for part, sizes in support.partition_table(len(events)).items():
            result, *_rest = support.stream_result(spec, events, sizes)
            observed[name][part] = {"sha256": support.response_sha(result),
                                    "dataclass_equal": result == batch}
            expected[name][part] = {"sha256": batch_hash, "dataclass_equal": True}
        table[name] = {"batch_sha256": batch_hash, "partitions": len(observed[name])}
    report.extra["R1_batch_hashes"] = table
    report.extra["R1_partition_hashes"] = {
        n: {p: v["sha256"] for p, v in parts.items()} for n, parts in observed.items()}
    return observed, expected


# -------------------------------------------------------------------------- R2


def r2():
    spec = support.make_spec(support.example_settings("three-plane-dark"))
    events = support.transport_events(spec)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    noise_before_close, dark_before_close = 0, []
    for index, event in enumerate(events):
        chunk = stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
        noise_before_close += sum(1 for h in chunk.hits if h.noise_id is not None)
        dark_before_close.append(stream.dark_hits)
    tail = stream.close(source_sha256=DIGEST)
    observed = {"noise_hits_in_chunks": noise_before_close,
                "dark_hits_before_close": sorted({repr(v) for v in dark_before_close}),
                "dark_hits_in_tail": tail.dark_hits > 0}
    expected = {"noise_hits_in_chunks": 0, "dark_hits_before_close": ["None"],
                "dark_hits_in_tail": True}
    return observed, expected


# -------------------------------------------------------------------------- R3


def r3():
    spec = support.make_spec(support.example_settings("three-plane-dark"))
    events = support.transport_events(spec)
    batch = support.batch_result(spec, events)
    batch_dark = tuple(h for h in batch.hits if h.noise_id is not None)
    pinned = ResponseStream(spec, pin_horizon=True)
    dark_equal = tuple(pinned.dark_hits) == batch_dark
    hashes = {}
    for part, sizes in (("one-chunk", support.chunk_sizes(len(events), "one")),
                        ("per-event", support.chunk_sizes(len(events), "single")),
                        ("random-3", support.chunk_sizes(len(events), "random", 3))):
        result, *_r = support.stream_result(spec, events, sizes, pin=True)
        hashes[part] = (support.response_sha(result), result == batch)

    short = copy.deepcopy(support.example_settings("three-plane-dark"))
    short["timeline"]["duration_s"] = 0.001
    short_spec = support.make_spec(short)
    short_events = support.transport_events(short_spec)
    scheduler = TriggerScheduler(short_spec)
    stream = ResponseStream(short_spec, pin_horizon=True)
    raised_at, message, later = None, None, {}
    for index, event in enumerate(short_events):
        trigger = scheduler.next(event["simulation_event_id"])
        try:
            stream.feed([(event, trigger)])
        except ResponseError as exc:
            raised_at, message = index, str(exc)
            break
    if raised_at is not None:
        nxt = short_events[min(raised_at + 1, len(short_events) - 1)]
        later["feed_empty"] = error_name(lambda: stream.feed([]))[0]
        later["feed_event"] = error_name(
            lambda: stream.feed([(nxt, scheduler.next(nxt["simulation_event_id"]))]))[0]
        later["close"] = error_name(lambda: stream.close(source_sha256=DIGEST))[0]
    observed = {
        "pinned_dark_equals_batch": dark_equal,
        "partitions": hashes,
        "short_duration": {"raised": raised_at is not None,
                           "message_has_text": "pinned dark-noise horizon exceeded" in (message or ""),
                           "later_calls": later},
    }
    batch_hash = support.response_sha(batch)
    expected = {
        "pinned_dark_equals_batch": True,
        "partitions": {k: (batch_hash, True) for k in hashes},
        "short_duration": {"raised": True, "message_has_text": True,
                           "later_calls": {"feed_empty": "ResponseError",
                                           "feed_event": "ResponseError",
                                           "close": "ResponseError"}},
    }
    return observed, expected


# -------------------------------------------------------------------------- R4


def r4():
    observed, expected = {}, {}
    for name, settings in configs().items():
        spec = support.make_spec(settings)
        events = support.transport_events(spec)
        rng = random.Random(404)
        scheduler = TriggerScheduler(spec)
        triggers, peeks_ok = [], True
        for event in events:
            peeked = None
            for _ in range(rng.randint(0, 3)):
                value = scheduler.peek_origin_ps()
                peeked = value if peeked is None else peeked
                peeks_ok = peeks_ok and value == peeked
            trigger = scheduler.next(event["simulation_event_id"])
            peeks_ok = peeks_ok and (peeked is None or peeked == trigger.origin_ps)
            triggers.append(trigger)
        observed[name] = {"equals_schedule": tuple(triggers) == tuple(schedule(events, spec)),
                          "peek_stable": peeks_ok, "ordinal": scheduler.ordinal}
        expected[name] = {"equals_schedule": True, "peek_stable": True,
                          "ordinal": len(events)}
    return observed, expected


# -------------------------------------------------------------------------- R5


def r5():
    spec = support.make_spec(support.stress_settings(200))
    events = support.transport_events(spec)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    ids = [d.id for d in spec.detectors]
    margins, violations, checked = {d: None for d in ids}, [], 0
    for event in events:
        stream.advance_to(scheduler.peek_origin_ps())
        marks = {d: stream.hit_watermark_ps(d) for d in ids}
        chunk = stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
        times = [(o.detector, o.time_ps) for o in chunk.outcomes if o.eligible]
        times += [(h.detector, h.time_ps) for h in chunk.hits]
        for detector, time_ps in times:
            checked += 1
            margin = time_ps - marks[detector]
            if margins[detector] is None or margin < margins[detector]:
                margins[detector] = margin
            if margin < 0:
                violations.append({"detector": detector, "time_ps": time_ps,
                                   "watermark_ps": marks[detector]})
    observed = {"violations": violations[:5], "records_checked_positive": checked > 0,
                "margin_nonnegative": {d: (m is not None and m >= 0)
                                       for d, m in margins.items()}}
    expected = {"violations": [], "records_checked_positive": True,
                "margin_nonnegative": {d: True for d in ids}}
    return observed, expected, {"records_checked": checked, "minimum_margin_ps": margins}


# -------------------------------------------------------------------------- R6


def r6():
    spec = support.make_spec(support.example_settings("three-plane"))
    events = support.transport_events(spec)
    origins = [t.origin_ps for t in exact_triggers(spec, events)]

    def trig(k, event=None, **over):
        event = event if event is not None else events[k]
        values = {"ordinal": k, "simulation_event_id": event["simulation_event_id"],
                  "physical_trigger_id": response_module.trigger_number(k),
                  "origin_ps": origins[k]}
        values.update(over)
        return Trigger(**values)

    def fresh(settings_spec=None):
        return ResponseStream(settings_spec or spec)

    def duplicate():
        s = fresh()
        s.feed([(events[0], trig(0))])
        s.feed([(events[0], trig(1, events[0]))])

    def decreasing_id():
        s = fresh()
        s.feed([(events[1], trig(0, events[1]))])
        s.feed([(events[0], trig(1, events[0]))])

    def ordinal_mismatch():
        fresh().feed([(events[0], trig(0, ordinal=5))])

    def physical_mismatch():
        fresh().feed([(events[0], trig(0, physical_trigger_id=999))])

    def event_id_mismatch():
        fresh().feed([(events[0], trig(0, simulation_event_id=77))])

    def decreasing_origin():
        s = fresh()
        s.feed([(events[0], trig(0, origin_ps=5_000_000))])
        s.feed([(events[1], trig(1, origin_ps=4_000_000))])

    def below_floor():
        s = fresh()
        s.advance_to(10_000_000)
        s.feed([(events[0], trig(0, origin_ps=5))])

    def too_many():
        small = support.make_spec({**support.example_settings("three-plane"), "events": 2})
        s = fresh(small)
        s.feed([(events[k], trig(k)) for k in range(3)])

    def after_close():
        s = fresh()
        s.feed([(events[0], trig(0))])
        s.close(source_sha256=DIGEST, partial=True)
        s.feed([(events[1], trig(1))])

    def after_failure():
        s = fresh()
        try:
            s.feed([(events[0], trig(0, ordinal=5))])
        except ResponseError:
            pass
        s.feed([(events[0], trig(0))])

    def close_twice():
        s = fresh()
        s.feed([(events[0], trig(0))])
        s.close(source_sha256=DIGEST, partial=True)
        s.close(source_sha256=DIGEST, partial=True)

    def control():
        s = fresh()
        s.feed([(events[k], trig(k)) for k in range(3)])
        s.close(source_sha256=DIGEST, partial=True)

    # (case, text that the error message must contain: the violation the case targets)
    cases = {
        "control: valid sequence": (control, None),
        "duplicate event id": (duplicate, "duplicate simulation_event_id"),
        "decreasing event id": (decreasing_id, "does not increase"),
        "trigger ordinal mismatch": (ordinal_mismatch, "trigger ordinal must equal"),
        "physical_trigger_id mismatch": (physical_mismatch, "physical_trigger_id must equal"),
        "event id mismatch with trigger": (event_id_mismatch, "does not match event"),
        "decreasing origin": (decreasing_origin, "nondecreasing"),
        "origin below advance_to floor": (below_floor, "below the advance_to floor"),
        "more events than spec.events": (too_many, "more events than spec.events"),
        "feed after close": (after_close, "response stream is closed"),
        "feed after failure": (after_failure, "failed earlier"),
        "close twice": (close_twice, "response stream is closed"),
    }
    observed, expected, messages = {}, {}, {}
    for label, (fn, needle) in cases.items():
        kind, message = error_name(fn)
        messages[label] = message
        if needle is None:
            observed[label], expected[label] = [kind, message], [None, None]
        else:
            observed[label] = [kind, needle in (message or "")]
            expected[label] = ["ResponseError", True]
    return observed, expected, messages


def r7():
    spec = support.make_spec(support.example_settings("three-plane-dark"))
    events = support.transport_events(spec)
    total = len(events)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    stream.feed([(events[0], scheduler.next(0))])
    kind, message = error_name(lambda: stream.close(source_sha256=DIGEST))
    batch_kind, batch_message = error_name(
        lambda: support.batch_result(spec, events[:1]))
    observed = {"strict_close": [kind, message == batch_message]}
    expected = {"strict_close": ["ResponseError", True]}
    for n in (1, 17, total - 1):
        sizes = support.chunk_sizes(n, "fixed5")
        stream_part = _partial(spec, events[:n], sizes)
        shortened = support.batch_result(dataclasses.replace(spec, events=n), events[:n])
        observed[f"partial n={n}"] = [stream_part == shortened,
                                      support.response_sha(stream_part)]
        expected[f"partial n={n}"] = [True, support.response_sha(shortened)]
    return observed, expected


def _partial(spec, events, sizes):
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    chunks, index = [], 0
    for size in sizes:
        part = events[index:index + size]
        chunks.append(stream.feed([(e, scheduler.next(e["simulation_event_id"]))
                                   for e in part]))
        index += len(part)
    tail = stream.close(source_sha256=DIGEST, partial=True)
    return assemble_result(chunks, tail)


# -------------------------------------------------------------------------- R8


def r8():
    base = support.example_settings("three-plane")
    dark = support.example_settings("three-plane-dark")
    spec = support.make_spec(base)
    events = support.transport_events(spec)

    def limited(settings, **limits):
        return {**copy.deepcopy(settings), "limits": limits}

    broken = copy.deepcopy(events)
    broken[5]["tracks"] = []
    ghost_detectors = list(spec.detectors)
    ghost_detectors[0] = dataclasses.replace(
        ghost_detectors[0],
        response=dataclasses.replace(ghost_detectors[0].response, model="ghost_model"))
    ghost_spec = dataclasses.replace(spec, detectors=tuple(ghost_detectors))
    dark_spec = support.make_spec(dark)
    dark_events = support.transport_events(dark_spec)
    cases = {
        "control": (spec, events, DIGEST),
        "malformed event at 5": (spec, broken, DIGEST),
        "limits.pixels lowered": (support.make_spec(limited(base, pixels=10)), events, DIGEST),
        "limits.dark_hits lowered": (
            support.make_spec(limited(dark, dark_hits=100)), dark_events, DIGEST),
        "limits.intersections lowered": (
            support.make_spec(limited(base, intersections=50)), events, DIGEST),
        "uppercase digest": (spec, events, "AB" * 32),
        "short digest": (spec, events, "ab" * 10),
        "unsupported response model": (ghost_spec, events, DIGEST),
    }
    observed, expected, detail = {}, {}, {}
    for label, (case_spec, case_events, digest) in cases.items():
        batch_kind, batch_msg = error_name(
            lambda: support.batch_result(case_spec, case_events, digest=digest))
        original_ids = [e["simulation_event_id"] for e in events]

        def run_stream():
            scheduler = TriggerScheduler(case_spec)
            stream = ResponseStream(case_spec)
            pairs = []
            for position, event in enumerate(case_events):
                pairs.append((event, scheduler.next(original_ids[position])))
            stream.feed(pairs)
            stream.close(source_sha256=digest)

        stream_kind, stream_msg = error_name(run_stream)
        observed[label] = {"stream": stream_kind, "batch": batch_kind}
        wanted = None if label == "control" else "ResponseError"
        expected[label] = {"stream": wanted, "batch": wanted}
        detail[label] = {"batch": batch_msg, "stream": stream_msg}
    return observed, expected, detail


# -------------------------------------------------------------------------- R9


def r9():
    observed, expected = {}, {}
    for name in support.EXAMPLE_NAMES:
        settings = support.example_settings(name)
        spec = support.make_spec(settings)
        digest = CrossingDigest()
        for event in support.transport_events(spec):
            digest.update(event)
        with tempfile.TemporaryDirectory(prefix="e2e-r9-") as tmp:
            out = Path(tmp) / "run"
            lifecycle.generate(out, copy.deepcopy(settings))
            file_digest = support.sha256_file(out / "intersections.jsonl")
        observed[name], expected[name] = digest.hexdigest(), file_digest
    return observed, expected


# ------------------------------------------------------------------------- R10


def r10():
    spec = support.make_spec(support.example_settings("three-plane-dark"))
    events = support.transport_events(spec)
    sizes = support.chunk_sizes(len(events), "random", 7)
    first, *_a = support.stream_result(spec, events, sizes)
    batch = support.batch_result(spec, events)
    second, *_b = support.stream_result(spec, events, sizes)
    third, *_c = support.stream_result(spec, events, support.chunk_sizes(len(events), "one"))
    shas = [support.response_sha(r) for r in (first, batch, second, third)]
    return {"all_equal": len(set(shas)) == 1, "count": len(shas)}, \
        {"all_equal": True, "count": 4}


# ------------------------------------------------------------------------- R11


def r11():
    registry = ResponseRegistry()

    def kernel(detector, local_mm):
        col = round(local_mm[0] * 1000 / detector.pitch_um[0])
        row = round(local_mm[1] * 1000 / detector.pitch_um[1])
        if not (0 <= col < detector.pixels[0] and 0 <= row < detector.pixels[1]):
            return None
        return ((col, row),), (0.0, 0.0)

    registry.register("demo_kernel", kernel)
    spec = support.make_spec(support.example_settings("three-plane"))
    detectors = list(spec.detectors)
    detectors[1] = dataclasses.replace(
        detectors[1], response=dataclasses.replace(detectors[1].response, model="demo_kernel"))
    custom = dataclasses.replace(spec, detectors=tuple(detectors))
    events = support.transport_events(spec)
    batch = support.batch_result(custom, events, registry=registry)
    result, *_r = support.stream_result(custom, events, support.chunk_sizes(len(events), "fixed7"),
                                        registry=registry)
    observed = {"stream_provenance": result.provenance, "batch_provenance": batch.provenance,
                "equal": result == batch,
                "same_bytes": support.response_sha(result) == support.response_sha(batch),
                "kernel_used": any(o.detector == detectors[1].id and o.accepted
                                   for o in batch.outcomes)}
    expected = {"stream_provenance": "external", "batch_provenance": "external",
                "equal": True, "same_bytes": True, "kernel_used": True}
    return observed, expected


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("stream_equivalence", args.evidence)

    report.check("R1", lambda: r1(report))
    report.check("R2", r2)
    report.check("R3", r3)
    report.check("R4", r4)
    for cid, fn in (("R5", r5), ("R6", r6), ("R8", r8)):
        _run_with_detail(report, cid, fn)
    report.check("R7", r7)
    report.check("R9", r9)
    report.check("R10", r10)
    report.check("R11", r11)
    return report.finish()


def _run_with_detail(report, cid, fn):
    holder = {}

    def wrapped():
        result = fn()
        holder["detail"] = result[2]
        return result[0], result[1]

    report.check(cid, wrapped)
    if "detail" in holder:
        report.checks[-1]["details"] = support.jsonable(holder["detail"])


if __name__ == "__main__":
    sys.exit(main())
