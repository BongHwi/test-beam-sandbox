"""Transport slicing and isolation checks T1-T5."""

from __future__ import annotations

import random
import sys

import _support as support

support.use_source()

from tracksim.fast_transport import _json_line, iter_transport, propagate_event  # noqa: E402


def lines(pairs) -> tuple[bytes, bytes]:
    events, traces = [], []
    for event, trace in pairs:
        events.append(_json_line(event))
        traces.append(_json_line(trace))
    return b"".join(events), b"".join(traces)


def spec_a():
    return support.make_spec(support.example_settings("three-plane"))


def spec_b():
    settings = support.example_settings("three-plane-dark")
    settings["seed"] = 777
    settings["events"] = 60
    return support.make_spec(settings)


def t1():
    spec = spec_a()
    whole = lines(iter_transport(spec))
    rng = random.Random(11)
    results = []
    for trial in range(10):
        cuts = sorted(rng.sample(range(0, spec.events + 1), rng.randint(1, 6)))
        bounds = sorted({0, spec.events, *cuts})
        pieces = [lines(iter_transport(spec, start=a, stop=b))
                  for a, b in zip(bounds, bounds[1:])]
        joined = (b"".join(p[0] for p in pieces), b"".join(p[1] for p in pieces))
        results.append({"bounds": bounds, "events_equal": joined[0] == whole[0],
                        "traces_equal": joined[1] == whole[1]})
    observed = [(r["events_equal"], r["traces_equal"]) for r in results]
    return observed, [(True, True)] * 10, {"cuts": [r["bounds"] for r in results]}


def t2():
    spec = spec_a()
    items = list(iter_transport(spec))
    observed, expected = {}, {}
    for k in (0, 1, spec.events // 2, spec.events - 1):
        observed[str(k)] = propagate_event(spec, k) == items[k]
        expected[str(k)] = True
    return observed, expected


def raises(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc).__name__
    return None


def t3():
    spec = spec_a()
    n = spec.events
    observed = {
        "start<0": raises(lambda: list(iter_transport(spec, start=-1, stop=3))),
        "stop>events": raises(lambda: list(iter_transport(spec, start=0, stop=n + 1))),
        "start>stop": raises(lambda: list(iter_transport(spec, start=5, stop=4))),
        "start>events": raises(lambda: list(iter_transport(spec, start=n + 1))),
        "empty slice": len(list(iter_transport(spec, start=7, stop=7))),
    }
    expected = {"start<0": "ValueError", "stop>events": "ValueError",
                "start>stop": "ValueError", "start>events": "ValueError", "empty slice": 0}
    return observed, expected


def t4():
    one, two = spec_a(), spec_b()
    alone = (lines(iter_transport(one)), lines(iter_transport(two)))
    left, right = iter_transport(one), iter_transport(two)
    got_left, got_right = [], []
    live = [True, True]
    while any(live):
        for index, (iterator, sink) in enumerate(((left, got_left), (right, got_right))):
            if live[index]:
                try:
                    sink.append(next(iterator))
                except StopIteration:
                    live[index] = False
    observed = (lines(got_left), lines(got_right))
    return observed == alone, {"left": len(got_left), "right": len(got_right)}, \
        {"left": one.events, "right": two.events}


def t5():
    spec = spec_a()
    clean = lines(iter_transport(spec))
    noisy = []
    for index, item in enumerate(iter_transport(spec)):
        random.seed(index * 7919 + 3)
        random.random()
        noisy.append(item)
    random.seed(0)
    return lines(noisy) == clean, "bytes compared after reseeding between items", "equal"


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("transport_slices", args.evidence)

    holder = {}

    def with_detail(cid, fn):
        def wrapped():
            result = fn()
            holder[cid] = result[2] if len(result) > 2 else None
            return result[0], result[1]

        report.check(cid, wrapped)
        if holder.get(cid) is not None:
            report.checks[-1]["details"] = support.jsonable(holder[cid])

    with_detail("T1", t1)
    report.check("T2", t2)
    report.check("T3", t3)
    report.verdict("T4", t4)
    report.verdict("T5", t5)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
