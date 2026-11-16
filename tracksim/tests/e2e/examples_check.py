"""Examples and README checks E1-E3."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import _support as support

support.use_source()

FENCE = re.compile(r"^```sh[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
PYFENCE = re.compile(r"^```python[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def e1(scratch: Path):
    observed, expected, details = {}, {}, {}
    from tracksim.specs import normalize_config, validate_capabilities

    for name in support.EXAMPLE_NAMES:
        settings = support.example_settings(name)
        try:
            spec = normalize_config(settings)
            validate_capabilities(spec)
            normalised = True
        except Exception as exc:
            normalised = repr(exc)
        out = scratch / name
        gen = support.generate_example(name, out, cwd=scratch)
        ver = support.run_cli(["verify", out], cwd=scratch)
        status = json.loads(ver.stdout).get("status") if ver.returncode == 0 else None
        observed[name] = {"normalises": normalised, "generate_exit": gen.returncode,
                          "complete": gen.returncode == 0 and json.loads(gen.stdout)["complete"],
                          "verify_exit": ver.returncode, "status": status}
        expected[name] = {"normalises": True, "generate_exit": 0, "complete": True,
                          "verify_exit": 0, "status": "verified"}
        details[name] = {"generate_stderr": gen.stderr[-300:], "verify_stderr": ver.stderr[-300:]}
    return observed, expected, details


def e2(scratch: Path):
    spec = support.make_spec(support.example_settings("three-plane-dark"))
    events = support.transport_events(spec)
    batch = support.batch_result(spec, events)
    run_report = json.loads((scratch / "three-plane-dark" / "response-report.json").read_text())
    stream_result, *_rest = support.stream_result(spec, events, [len(events)], pin=True)
    duration_ps = round(spec.timeline.duration_s * 1e12)
    observed = {"batch_horizon_ps": batch.horizon_ps,
                "run_horizon_ps": run_report["horizon_ps"],
                "pinned_stream_horizon_ps": stream_result.horizon_ps,
                "dark_hits_present": batch.dark_hits > 0}
    expected = {"batch_horizon_ps": duration_ps, "run_horizon_ps": duration_ps,
                "pinned_stream_horizon_ps": duration_ps, "dark_hits_present": True}
    return observed, expected


def readme_blocks() -> list[str]:
    text = (support.ROOT / "README.md").read_text()
    return [block.strip() for block in FENCE.findall(text)]


def e3(scratch: Path):
    blocks = readme_blocks()
    package = support.copy_package(scratch / "readme-copy")
    bin_dir = scratch / "bin"
    bin_dir.mkdir()
    for name in ("python", "python3"):
        (bin_dir / name).symlink_to(sys.executable)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    results = []
    for block in blocks:
        proc = subprocess.run(["sh", "-ec", block], cwd=package, env=env,
                              capture_output=True, text=True, timeout=900)
        results.append({"commands": block.splitlines(), "exit": proc.returncode,
                        "stderr_tail": proc.stderr[-300:]})
    python_blocks = [b.strip() for b in PYFENCE.findall((support.ROOT / "README.md").read_text())]
    python_results = []
    for block in python_blocks:
        proc = subprocess.run([sys.executable, "-c", block], cwd=package,
                              env=dict(env, PYTHONPATH=str(package / "src")),
                              capture_output=True, text=True, timeout=900)
        python_results.append({"exit": proc.returncode, "stderr_tail": proc.stderr[-300:]})
    observed = {"sh_blocks": len(blocks) >= 3, "exits": [r["exit"] for r in results],
                "python_blocks": len(python_blocks) >= 1,
                "python_exits": [r["exit"] for r in python_results]}
    expected = {"sh_blocks": True, "exits": [0] * len(blocks), "python_blocks": True,
                "python_exits": [0] * len(python_blocks)}
    return observed, expected, {"sh": results, "python": python_results}


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("examples_check", args.evidence)
    holder = {}

    def with_detail(cid, fn):
        def wrapped():
            result = fn()
            holder[cid] = result[2] if len(result) > 2 else None
            return result[0], result[1]

        report.check(cid, wrapped)
        if holder.get(cid) is not None:
            report.checks[-1]["details"] = support.jsonable(holder[cid])

    with tempfile.TemporaryDirectory(prefix="e2e-e-") as name:
        scratch = Path(name)
        with_detail("E1", lambda: e1(scratch))
        report.check("E2", lambda: e2(scratch))
        with_detail("E3", lambda: e3(scratch))
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
