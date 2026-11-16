"""Memory-bound checks M1-M4."""

from __future__ import annotations

import gc
import sys
import tracemalloc
import types

import _support as support

support.use_source()

from tracksim.fast_transport import iter_transport  # noqa: E402
from tracksim.stream import ResponseStream, TriggerScheduler  # noqa: E402

SMALL, LARGE = 2_000, 20_000
SLACK_BYTES = 256 * 1024
SIZE_TOLERANCE = 4 * 1024


def stress_spec(events: int):
    return support.make_spec(support.stress_settings(events))


def peak_stream(events: int) -> int:
    spec = stress_spec(events)
    gc.collect()
    tracemalloc.start()
    try:
        scheduler = TriggerScheduler(spec)
        stream = ResponseStream(spec)
        for event, _trace in iter_transport(spec):
            stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def peak_transport(events: int) -> int:
    spec = stress_spec(events)
    gc.collect()
    tracemalloc.start()
    try:
        for _item in iter_transport(spec):
            pass
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def bounded(peak_small: int, peak_large: int) -> bool:
    return peak_large <= 1.2 * peak_small + SLACK_BYTES


def m1():
    small, large = peak_stream(SMALL), peak_stream(LARGE)
    limit = 1.2 * small + SLACK_BYTES
    return (large <= limit,
            {"peak_2k_bytes": small, "peak_20k_bytes": large},
            {"peak_20k_bytes_at_most": limit})


def deep_size(root) -> int:
    """Sum of ``sys.getsizeof`` over every object reachable from attributes and collections."""
    seen, stack, total = set(), [root], 0
    skip = (type, types.FunctionType, types.BuiltinFunctionType, types.ModuleType,
            types.MethodType)
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        total += sys.getsizeof(obj)
        if isinstance(obj, skip):
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        else:
            if hasattr(obj, "__dict__"):
                stack.append(vars(obj))
            for klass in type(obj).__mro__:
                for slot in getattr(klass, "__slots__", ()):
                    if hasattr(obj, slot):
                        stack.append(getattr(obj, slot))
    return total


def sizes_after(events: int) -> dict:
    spec = stress_spec(events)
    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec)
    for event, _trace in iter_transport(spec):
        stream.feed([(event, scheduler.next(event["simulation_event_id"]))])
    return {"stream": deep_size(stream), "scheduler": deep_size(scheduler)}


def m2():
    small, large = sizes_after(100), sizes_after(10_000)
    growth = {k: large[k] - small[k] for k in small}
    ok = all(abs(v) <= SIZE_TOLERANCE for v in growth.values())
    return ok, {"n100": small, "n10000": large, "growth_bytes": growth}, \
        {"abs_growth_at_most": SIZE_TOLERANCE}


def m3():
    settings = support.example_settings("three-plane-dark")
    spec = support.make_spec(settings)
    count = len(ResponseStream(spec, pin_horizon=True).dark_hits)
    ceiling = {"limits": {"dark_hits": 3687}}
    capped = support.make_spec({**settings, **ceiling})
    at_cap = len(ResponseStream(capped, pin_horizon=True).dark_hits)
    tight = support.make_spec({**settings, "limits": {"dark_hits": 3000}})
    try:
        ResponseStream(tight, pin_horizon=True)
        refused = None
    except Exception as exc:
        refused = type(exc).__name__
    observed = {"default_within_limit": 0 < count <= spec.limits.dark_hits,
                "capped_within_limit": 0 < at_cap <= capped.limits.dark_hits,
                "cap_below_expected_count": refused}
    return observed == {"default_within_limit": True, "capped_within_limit": True,
                        "cap_below_expected_count": "ResponseError"}, \
        {**observed, "dark_hits": [count, at_cap]}, \
        {"default_within_limit": True, "capped_within_limit": True,
         "cap_below_expected_count": "ResponseError"}


def m4():
    small, large = peak_transport(SMALL), peak_transport(LARGE)
    limit = 1.2 * small + SLACK_BYTES
    return (large <= limit,
            {"peak_2k_bytes": small, "peak_20k_bytes": large},
            {"peak_20k_bytes_at_most": limit})


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("memory_bound", args.evidence)
    report.verdict("M1", m1)
    report.verdict("M2", m2)
    report.verdict("M3", m3)
    report.verdict("M4", m4)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
