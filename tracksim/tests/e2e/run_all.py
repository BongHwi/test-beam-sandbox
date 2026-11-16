"""Run every end-to-end script in a fresh interpreter and write ``e2e-report.json``.

``--record-golden`` is a separate, explicit step: it writes
``golden/examples.json`` from the current interpreter and tree, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import _support as support

SCRIPTS = {
    "scan_tree": ["S1", "S5", "S7"],
    "cli_lifecycle": [f"L{i}" for i in range(1, 11)],
    "registries": [f"X{i}" for i in range(1, 15)],
    "contract_checks": [f"B{i}" for i in range(1, 17)],
    "stream_equivalence": [f"R{i}" for i in range(1, 12)],
    "readout_intervals": [f"O{i}" for i in range(1, 7)],
    "transport_slices": [f"T{i}" for i in range(1, 6)],
    "determinism": [f"D{i}" for i in range(1, 6)],
    "memory_bound": [f"M{i}" for i in range(1, 5)],
    "examples_check": [f"E{i}" for i in range(1, 4)],
}
GATED_SKIPS = {"D3", "D4", "O4"}


def run_script(name: str, evidence: Path) -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(support.E2E / f"{name}.py"), "--evidence", str(evidence)],
        capture_output=True, text=True, env=support.child_env(), timeout=3600)
    entry = {"script": name, "returncode": proc.returncode,
             "seconds": round(time.monotonic() - started, 1),
             "stdout_tail": proc.stdout[-600:], "stderr_tail": proc.stderr[-1500:],
             "problems": [], "checks": {}}
    path = evidence / f"{name}.json"
    if not path.is_file():
        entry["problems"].append("evidence file missing")
        return entry
    entry["evidence_sha256"] = support.sha256_file(path)
    document = json.loads(path.read_text())
    for check in document["checks"]:
        entry["checks"][check["id"]] = {"status": check["status"], "reason": check["reason"]}
    for cid in SCRIPTS[name]:
        if cid not in entry["checks"]:
            entry["problems"].append(f"{cid} missing")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--only", help="comma-separated script names")
    parser.add_argument("--require-golden", action="store_true",
                        help="treat a skipped D4 or O4 as a failure")
    parser.add_argument("--record-golden", action="store_true",
                        help="write golden/examples.json and exit")
    args = parser.parse_args()

    if args.record_golden:
        data = support.record_golden()
        print(f"recorded {support.GOLDEN} for {sorted(data['examples'])}")
        return 0
    if args.evidence is None:
        parser.error("--evidence is required")

    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    names = args.only.split(",") if args.only else list(SCRIPTS)
    unknown = [n for n in names if n not in SCRIPTS]
    if unknown:
        parser.error(f"unknown script(s): {unknown}")
    mandatory_skips = set() if not args.require_golden else {"D4", "O4"}
    entries = [run_script(name, evidence) for name in names]

    failures = []
    for entry in entries:
        if entry["returncode"] != 0:
            failures.append(f"{entry['script']}: exit {entry['returncode']}")
        failures += [f"{entry['script']}: {p}" for p in entry["problems"]]
        for cid, check in entry["checks"].items():
            if check["status"] == "fail":
                failures.append(f"{cid}: fail")
            if check["status"] == "skip" and (cid not in GATED_SKIPS or cid in mandatory_skips):
                failures.append(f"{cid}: skipped ({check['reason']})")
            if check["status"] == "skip" and not check["reason"]:
                failures.append(f"{cid}: skip without reason")
    report = {
        "python": support.python_identity(),
        "tree_sha256": support.tree_sha256(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scripts": entries,
        "failures": failures,
        "passed": not failures,
    }
    (evidence / "e2e-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    summary = {e["script"]: {cid: c["status"] for cid, c in e["checks"].items()} for e in entries}
    print(json.dumps(summary, indent=1))
    print("failures:", failures or "none")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
