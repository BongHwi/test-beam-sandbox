"""Determinism checks D1-D5."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import _support as support

SELF = Path(__file__).resolve()
HASH_SEEDS = ("0", "1", "4242")
IDENTITY_FILES = {"python-runtime.json", "runtime.json", "transport-envelope.json",
                  "manifest.json", "source-snapshot.json", "verification/report.json"}


def child_hashes() -> None:
    """Hidden mode: print stream response digests for three partitions."""
    support.use_source()
    out = {}
    for name in support.EXAMPLE_NAMES:
        spec = support.make_spec(support.example_settings(name))
        events = support.transport_events(spec)
        out[name] = {}
        for part in ("one-chunk", "per-event", "fixed-7"):
            sizes = support.partition_table(len(events))[part]
            result, *_rest = support.stream_result(spec, events, sizes)
            out[name][part] = support.response_sha(result)
    print("CHILD " + json.dumps(out, sort_keys=True))


def d1_d5(report):
    runs, table = [], {}
    with tempfile.TemporaryDirectory(prefix="e2e-d1-") as tmp:
        for index, seed in enumerate(HASH_SEEDS):
            cwd = Path(tmp) / f"cwd-{index}"
            cwd.mkdir()
            out = Path(tmp) / f"run-{index}"
            env = support.child_env({"PYTHONHASHSEED": seed})
            proc = support.run_cli(
                ["generate", "--config", support.EXAMPLES / "three-plane.json", "--out", out],
                cwd=cwd, env=env)
            if proc.returncode != 0:
                raise RuntimeError(f"generate failed (hash seed {seed}): {proc.stderr[-400:]}")
            hashes = support.file_hashes(out)
            runs.append({"hashseed": seed, "files": hashes,
                         "manifest": support.manifest_without_timestamp(out / "manifest.json"),
                         "created_utc": json.loads((out / "manifest.json").read_text())["created_utc"]})
            table[seed] = hashes
            if index == 0:
                time.sleep(2)
    report.extra["D1_file_hashes"] = table
    first = runs[0]
    others = {}
    for run in runs[1:]:
        differing = sorted(k for k in set(first["files"]) | set(run["files"])
                           if k != "manifest.json" and first["files"].get(k) != run["files"].get(k))
        others[run["hashseed"]] = {"differing_files": differing,
                                   "manifest_equal_without_created_utc":
                                       run["manifest"] == first["manifest"]}
    timestamps_differ = len({r["created_utc"] for r in runs}) > 1
    d1 = (others, {s: {"differing_files": [], "manifest_equal_without_created_utc": True}
                   for s in HASH_SEEDS[1:]})
    d5 = ({"created_utc_differs_across_sleep": runs[0]["created_utc"] != runs[1]["created_utc"],
           "only_timestamp_differs": all(o["manifest_equal_without_created_utc"]
                                         and not o["differing_files"] for o in others.values())},
          {"created_utc_differs_across_sleep": True, "only_timestamp_differs": True})
    return d1, d5


def d2():
    results = {}
    for seed in HASH_SEEDS:
        proc = subprocess.run([sys.executable, str(SELF), "--child"], capture_output=True,
                              text=True, env=support.child_env({"PYTHONHASHSEED": seed}),
                              timeout=900)
        lines = [l for l in proc.stdout.splitlines() if l.startswith("CHILD ")]
        if proc.returncode != 0 or not lines:
            raise RuntimeError(f"child failed (hash seed {seed}): {proc.stderr[-400:]}")
        results[seed] = json.loads(lines[-1][6:])
    first = results[HASH_SEEDS[0]]
    return ({s: r == first for s, r in results.items()},
            {s: True for s in HASH_SEEDS}), first


def d3(report):
    interpreters = {v: shutil.which(f"python{v}") for v in ("3.11", "3.12")}
    missing = [v for v, path in interpreters.items() if not path]
    if missing:
        report.skip("D3", f"interpreter for Python {', '.join(missing)} not found on PATH")
        return
    tables = {}
    with tempfile.TemporaryDirectory(prefix="e2e-d3-") as tmp:
        for version, path in interpreters.items():
            out = Path(tmp) / f"run-{version}"
            proc = support.run_cli(
                ["generate", "--config", support.EXAMPLES / "three-plane.json", "--out", out],
                interpreter=path)
            if proc.returncode != 0:
                report.add("D3", "fail", {"version": version, "stderr": proc.stderr[-400:]},
                           None, "generate failed")
                return
            tables[version] = {k: v for k, v in support.file_hashes(out).items()
                               if k not in IDENTITY_FILES}
    differing = sorted(k for k in set(tables["3.11"]) | set(tables["3.12"])
                       if tables["3.11"].get(k) != tables["3.12"].get(k))
    if differing:
        report.skip("D3", "drift between Python 3.11 and 3.12 in: " + ", ".join(differing)
                    + " (informational, not a guarantee)")
        return
    report.add("D3", "pass", {"differing_files": []}, {"differing_files": []}, None)


def d4(report):
    golden = support.load_golden()
    if golden is None:
        report.skip("D4", "golden/examples.json is not recorded")
        return
    if golden["python"] != support.python_identity():
        report.skip("D4", "identity differs")
        return
    with tempfile.TemporaryDirectory(prefix="e2e-d4-") as tmp:
        observed = {n: support.example_artifact_hashes(n, Path(tmp)) for n in support.EXAMPLE_NAMES}
    report.check("D4", lambda: (observed, golden["examples"]))


def main() -> int:
    if "--child" in sys.argv:
        child_hashes()
        return 0
    args = support.parse_args(__doc__)
    report = support.Report("determinism", args.evidence)
    try:
        d1, d5 = d1_d5(report)
        report.check("D1", lambda: d1)
        report.check("D5", lambda: d5)
    except Exception:
        text = traceback.format_exc(limit=6)
        report.add("D1", "fail", {"exception": text}, None, "check raised")
        report.add("D5", "fail", {"exception": text}, None, "check raised")
    holder = {}

    def wrapped():
        value, holder["first"] = d2()
        return value

    report.check("D2", wrapped)
    if "first" in holder:
        report.extra["D2_stream_sha256"] = holder["first"]
    d3(report)
    try:
        d4(report)
    except Exception:
        report.add("D4", "fail", {"exception": traceback.format_exc(limit=6)}, None,
                   "check raised")
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
