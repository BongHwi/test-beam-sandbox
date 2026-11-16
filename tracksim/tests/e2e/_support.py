"""Shared helpers for the end-to-end check scripts.

The checks run the package from ``src`` through ``sys.path``; nothing is
installed.  Every script writes ``<evidence>/<script>.json`` through
:class:`Report` and exits 1 when any check fails.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

E2E = Path(__file__).resolve().parent
ROOT = E2E.parents[1]
SRC = ROOT / "src"
EXAMPLES = ROOT / "examples"
GOLDEN = E2E / "golden" / "examples.json"
EXAMPLE_NAMES = ("three-plane", "three-plane-dark")
DEMO_DIGEST = "ab" * 32


def use_source() -> None:
    """Make the package under test importable from ``src`` only."""
    text = str(SRC)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    sys.dont_write_bytecode = True


def python_identity() -> dict:
    return {
        "sys_version": sys.version,
        "sys_platform": sys.platform,
        "implementation": sys.implementation.name,
        "machine": platform.machine(),
        "random_version": random.Random.VERSION,
        "libc": list(platform.libc_ver()),
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256() -> str:
    """Digest over the sorted (path, sha256) pairs of the package source."""
    digest = hashlib.sha256()
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(SRC).as_posix()
            digest.update(f"{rel}\0{sha256_file(path)}\n".encode())
    return digest.hexdigest()


def file_hashes(directory: Path) -> dict[str, str]:
    """Recursive ``{relative posix path: sha256}`` of every file in a directory."""
    base = Path(directory)
    return {
        path.relative_to(base).as_posix(): sha256_file(path)
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def jsonable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(jsonable(v) for v in value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class Report:
    """Collects per-check results and writes the evidence file."""

    def __init__(self, script: str, evidence: Path) -> None:
        self.script = script
        self.evidence = Path(evidence)
        self.checks: list[dict] = []
        self.extra: dict = {}
        self.started = datetime.now(timezone.utc).isoformat()

    def add(self, cid, status, observed=None, expected=None, reason=None) -> None:
        if status == "skip" and not reason:
            raise ValueError(f"skip of {cid} needs a reason")
        self.checks.append(
            {
                "id": cid,
                "status": status,
                "observed": jsonable(observed),
                "expected": jsonable(expected),
                "reason": reason,
            }
        )
        print(f"{self.script}: {cid} {status}" + (f" ({reason})" if reason else ""))

    def skip(self, cid, reason) -> None:
        self.add(cid, "skip", None, None, reason)

    def check(self, cid, fn) -> None:
        """``fn`` returns ``(observed, expected)``; the check passes when equal."""
        try:
            observed, expected = fn()
        except Exception:
            self.add(cid, "fail", {"exception": traceback.format_exc(limit=6)}, None,
                     "check raised")
            return
        observed, expected = jsonable(observed), jsonable(expected)
        self.add(cid, "pass" if observed == expected else "fail", observed, expected)

    def verdict(self, cid, fn) -> None:
        """``fn`` returns ``(ok, observed, expected)``."""
        try:
            ok, observed, expected = fn()
        except Exception:
            self.add(cid, "fail", {"exception": traceback.format_exc(limit=6)}, None,
                     "check raised")
            return
        self.add(cid, "pass" if ok else "fail", observed, expected)

    def finish(self) -> int:
        self.evidence.mkdir(parents=True, exist_ok=True)
        document = {
            "script": self.script,
            "checks": self.checks,
            "python": python_identity(),
            "tree_sha256": tree_sha256(),
            "started_utc": self.started,
        }
        if self.extra:
            document["extra"] = jsonable(self.extra)
        (self.evidence / f"{self.script}.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        )
        failed = [c["id"] for c in self.checks if c["status"] == "fail"]
        print(f"{self.script}: {len(self.checks)} checks, failed: {failed or 'none'}")
        return 1 if failed else 0


def parse_args(description: str, extra=None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--evidence", type=Path, required=True)
    if extra:
        extra(parser)
    return parser.parse_args()


# ---------------------------------------------------------------- subprocesses


def child_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def run_cli(args, *, cwd=None, env=None, interpreter=None, src=None):
    """Run ``python -m tracksim`` and return the completed process."""
    environment = env or child_env()
    if src is not None:
        environment = dict(environment, PYTHONPATH=str(src))
    return subprocess.run(
        [interpreter or sys.executable, "-m", "tracksim", *[str(a) for a in args]],
        cwd=str(cwd) if cwd else None,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
    )


def run_code(code: str, *args, cwd=None, env=None, isolated=False):
    command = [sys.executable]
    if isolated:
        command.append("-I")
    command += ["-c", code, *[str(a) for a in args]]
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env or child_env(),
        capture_output=True,
        text=True,
        timeout=900,
    )


def run_case(script: Path, case: str, timeout: int = 900) -> dict:
    """Run one registered case of a script in a fresh interpreter."""
    with tempfile.TemporaryDirectory(prefix="e2e-case-") as cwd:
        proc = subprocess.run(
            [sys.executable, str(script), "--case", case],
            cwd=cwd,
            env=child_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("CASE-RESULT ")]
    if proc.returncode != 0 or not lines:
        return {
            "ok": False,
            "observed": {"returncode": proc.returncode, "stderr": proc.stderr[-2000:]},
            "expected": None,
        }
    return json.loads(lines[-1][len("CASE-RESULT "):])


def emit_case(ok, observed, expected) -> None:
    print("CASE-RESULT " + json.dumps(
        {"ok": bool(ok), "observed": jsonable(observed), "expected": jsonable(expected)}
    ))


def copy_package(destination: Path) -> Path:
    """Copy the sub-project (without bytecode) so a check can edit its own copy."""
    shutil.copytree(
        ROOT, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    return destination


# ----------------------------------------------------------------- configuration


def example_settings(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def make_spec(settings: dict):
    use_source()
    from tracksim.specs import normalize_config

    return normalize_config(copy.deepcopy(settings))


def stress_settings(events: int = 200, dark: float = 5.0) -> dict:
    """Three-plane variant with zero delay, wide jitter, a fast trigger rate and dark noise."""
    settings = example_settings("three-plane")
    settings["events"] = events
    settings["timeline"]["rate_hz"] = 20000
    for detector in settings["detectors"]:
        detector["response"]["response_delay_ns"] = 0
        detector["response"]["jitter_ns"] = 40
        detector["response"]["dark_hit_rate_per_pixel_hz"] = dark
    return settings


def set_dark(settings: dict, rates: dict) -> dict:
    out = copy.deepcopy(settings)
    for detector in out["detectors"]:
        detector["response"]["dark_hit_rate_per_pixel_hz"] = rates.get(detector["id"], 0)
    return out


def transport_events(spec) -> list[dict]:
    use_source()
    from tracksim.fast_transport import iter_transport

    return [event for event, _trace in iter_transport(spec)]


def strip_crossings(events: list[dict], after: int) -> list[dict]:
    """Copy of the events with every crossing emptied after the given event index."""
    out = copy.deepcopy(events)
    for index, event in enumerate(out):
        if index > after:
            for plane in event["planes"].values():
                plane["mc"] = []
    return out


# ----------------------------------------------------------------- stream helpers


def chunk_sizes(total: int, kind: str, seed: int = 0) -> list[int]:
    if kind == "one":
        return [total]
    if kind == "single":
        return [1] * total
    if kind.startswith("fixed"):
        size = int(kind[5:])
        sizes = [size] * (total // size)
        if total % size:
            sizes.append(total % size)
        return sizes
    rng = random.Random(seed)
    sizes, used = [], 0
    while used < total:
        size = min(rng.randint(0, 13), total - used)
        sizes.append(size)
        used += size
    return sizes


def partition_table(total: int) -> dict[str, list[int]]:
    table = {"one-chunk": chunk_sizes(total, "one"),
             "per-event": chunk_sizes(total, "single"),
             "fixed-7": chunk_sizes(total, "fixed7")}
    for seed in range(1, 21):
        table[f"random-{seed}"] = chunk_sizes(total, "random", seed)
    return table


def response_sha(result) -> str:
    from tracksim import neutral

    return sha256_bytes(neutral._response_records(result))


def frames_sha(frame_set) -> str:
    from tracksim import neutral

    return sha256_bytes(neutral._frame_records(frame_set))


def stream_result(spec, events, sizes, *, pin=False, digest=DEMO_DIGEST, registry=None):
    """Feed events in the given chunking and return ``(result, chunks, tail, stream)``."""
    use_source()
    from tracksim.stream import ResponseStream, TriggerScheduler, assemble_result

    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec, registry=registry, pin_horizon=pin)
    chunks, index = [], 0
    for size in sizes:
        part = events[index:index + size]
        pairs = [(e, scheduler.next(e["simulation_event_id"])) for e in part]
        chunks.append(stream.feed(pairs))
        index += len(part)
    if index != len(events):
        raise ValueError("chunk sizes do not cover the events")
    tail = stream.close(source_sha256=digest)
    return assemble_result(chunks, tail), chunks, tail, stream


def batch_result(spec, events, *, digest=DEMO_DIGEST, registry=None):
    use_source()
    from tracksim.response import generate_response

    return generate_response(events, spec, source_sha256=digest, registry=registry)


def progressive_readout(spec, events, *, pin=False, digest=DEMO_DIGEST):
    """Emit frames interval by interval while events are fed one at a time.

    After every feed each detector is emitted up to its closure boundary,
    using only the hits returned so far (plus the pinned dark hits).  After
    ``close`` the remaining intervals are emitted.
    """
    use_source()
    from tracksim.readout import assemble_frame_set, assemble_frames
    from tracksim.stream import ResponseStream, TriggerScheduler, assemble_result

    scheduler = TriggerScheduler(spec)
    stream = ResponseStream(spec, pin_horizon=pin)
    ids = [d.id for d in spec.detectors]
    previous = {d: 0 for d in ids}
    timeline, signal, chunks = [], [], []
    outcomes: dict = {}
    frames, log = [], []
    dark = tuple(stream.dark_hits or ()) if pin else ()

    def emit(detector, end, horizon, hits, phase):
        if end <= previous[detector]:
            return
        part = assemble_frames(
            spec, detector, previous[detector], end,
            timeline=tuple(timeline), hits=hits, outcomes=outcomes,
            horizon_ps=horizon,
        )
        log.append({"detector": detector, "start_ps": previous[detector],
                    "end_ps": end, "frames": len(part), "phase": phase})
        frames.extend(part)
        previous[detector] = end

    for event in events:
        trigger = scheduler.next(event["simulation_event_id"])
        chunk = stream.feed([(event, trigger)])
        chunks.append(chunk)
        timeline.extend(chunk.timeline)
        outcomes.update({o.id: o for o in chunk.outcomes})
        signal.extend(chunk.hits)
        hits = tuple(signal) + dark
        for detector in ids:
            emit(detector, stream.frames_final_before_ps(detector), None, hits, "open")
    before_close = len(frames)
    tail = stream.close(source_sha256=digest)
    hits = tuple(signal) + tuple(tail.hits)
    for detector in ids:
        emit(detector, stream.frames_final_before_ps(detector), tail.horizon_ps, hits, "closed")
    result = assemble_result(chunks, tail)
    return {
        "frames": frames,
        "frame_set": assemble_frame_set(spec, frames, tail.horizon_ps),
        "result": result,
        "tail": tail,
        "before_close": before_close,
        "log": log,
    }


# --------------------------------------------------------------- run directories


def generate_example(name_or_settings, out: Path, *, interpreter=None, env=None, cwd=None):
    """Run the command-line ``generate`` on an example name or a settings dict."""
    if isinstance(name_or_settings, str):
        config = EXAMPLES / f"{name_or_settings}.json"
    else:
        config = Path(out).parent / (Path(out).name + "-config.json")
        config.write_text(json.dumps(name_or_settings))
    return run_cli(["generate", "--config", config, "--out", out],
                   interpreter=interpreter, env=env, cwd=cwd)


IDENTITY_BEARING = ("manifest.json", "source-snapshot.json")


def example_artifact_hashes(name: str, workdir: Path) -> dict:
    """Generate one example and hash every artifact that does not embed source hashes."""
    out = Path(workdir) / f"golden-{name}"
    proc = generate_example(name, out)
    if proc.returncode != 0:
        raise RuntimeError(f"generate failed for {name}: {proc.stderr[-500:]}")
    verified = run_cli(["verify", out])
    if verified.returncode != 0:
        raise RuntimeError(f"verify failed for {name}: {verified.stderr[-500:]}")
    return {k: v for k, v in file_hashes(out).items() if k not in IDENTITY_BEARING}


def record_golden(destination: Path = GOLDEN) -> dict:
    with tempfile.TemporaryDirectory(prefix="e2e-golden-") as tmp:
        data = {
            "python": python_identity(),
            "examples": {n: example_artifact_hashes(n, Path(tmp)) for n in EXAMPLE_NAMES},
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def load_golden():
    if not GOLDEN.is_file():
        return None
    return json.loads(GOLDEN.read_text())


def manifest_without_timestamp(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    data.pop("created_utc", None)
    return data


def forge_python_identity(run_dir: Path) -> dict:
    """Rewrite the recorded interpreter identity of a finished run, keeping hashes consistent.

    Every JSON file that embeds the identity is rewritten and the manifest file index is
    refreshed, so that a later ``verify`` sees a run recorded under another interpreter.
    """
    run_dir = Path(run_dir)
    old = json.loads((run_dir / "python-runtime.json").read_text())
    new = dict(old, sys_version="0.0.0 (forged for a drift check)")

    def swap(value):
        if value == old:
            return dict(new)
        if isinstance(value, dict):
            return {k: swap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [swap(v) for v in value]
        return value

    changed = []
    for name in ("python-runtime.json", "runtime.json", "transport-envelope.json",
                 "manifest.json"):
        path = run_dir / name
        data = json.loads(path.read_text())
        swapped = swap(data)
        if swapped != data:
            path.write_text(json.dumps(swapped, indent=2, sort_keys=True) + "\n")
            changed.append(name)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        target = run_dir / name
        entry["sha256"], entry["bytes"] = sha256_file(target), target.stat().st_size
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"rewritten": changed, "forged": new}
