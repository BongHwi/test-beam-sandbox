"""Lifecycle and command-line checks L1-L10."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

import _support as support

support.use_source()

STAMP = "\n# appended by the drift check\n"


def generate(name_or_settings, out: Path, **kwargs):
    return support.generate_example(name_or_settings, out, **kwargs)


def l1(runs: Path):
    observed, expected, listing = {}, {}, {}
    for name in support.EXAMPLE_NAMES:
        out = runs / name
        gen = generate(name, out, cwd=runs)
        printed = json.loads(gen.stdout) if gen.returncode == 0 else None
        ver = support.run_cli(["verify", out], cwd=runs)
        printed_report = json.loads(ver.stdout) if ver.returncode == 0 else None
        stored = out / "verification" / "report.json"
        observed[name] = {
            "generate_exit": gen.returncode,
            "complete": printed and printed.get("complete"),
            "verify_exit": ver.returncode,
            "status": printed_report and printed_report.get("status"),
            "report_equals_printed": stored.is_file()
            and json.loads(stored.read_text()) == printed_report,
        }
        expected[name] = {"generate_exit": 0, "complete": True, "verify_exit": 0,
                          "status": "verified", "report_equals_printed": True}
        listing[name] = sorted(support.file_hashes(out)) if out.is_dir() else []
    return observed, expected, listing


def l2(runs: Path):
    existing = runs / "three-plane"
    before = support.file_hashes(existing)
    empty = runs / "existing-empty"
    empty.mkdir()
    observed = {}
    for label, target in (("run directory", existing), ("empty directory", empty)):
        proc = generate("three-plane", target, cwd=runs)
        observed[label] = {"exit": proc.returncode,
                           "message": proc.stderr.startswith("tracksim: error:")}
    observed["hashes_unchanged"] = support.file_hashes(existing) == before
    observed["empty_still_empty"] = not any(empty.iterdir())
    expected = {"run directory": {"exit": 1, "message": True},
                "empty directory": {"exit": 1, "message": True},
                "hashes_unchanged": True, "empty_still_empty": True}
    return observed, expected


def l3(runs: Path):
    base = support.example_settings("three-plane")
    variants = {
        "unknown preset": {**base, "preset": "demo_preset"},
        "unknown backend": {**base, "backend": "demo_backend"},
        "unknown writer": {**base, "writer": "demo_writer"},
        "unknown schema": {**base, "schema_version": 99},
        "out-of-domain energy": copy.deepcopy(base),
    }
    variants["out-of-domain energy"]["beam"]["kinetic_energy_gev"] = 1000.0
    observed = {}
    for label, settings in variants.items():
        out = runs / ("invalid-" + label.replace(" ", "-"))
        proc = generate(settings, out, cwd=runs)
        observed[label] = {"exit": proc.returncode, "out_exists": out.exists(),
                           "message": proc.stderr.startswith("tracksim: error:")}
    return observed, {k: {"exit": 1, "out_exists": False, "message": True} for k in variants}


def l4():
    target = support.SRC / "tracksim" / f"_e2e_must_not_exist_{id(object())}"
    try:
        with tempfile.TemporaryDirectory(prefix="e2e-l4-") as cwd:
            proc = generate("three-plane", target, cwd=cwd)
        observed = {"exit": proc.returncode, "created": target.exists()}
    finally:
        if target.exists():
            shutil.rmtree(target)
    return observed, {"exit": 1, "created": False}


def flip_middle(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def flip_recorded_hash(manifest: Path, file_name: str) -> None:
    document = json.loads(manifest.read_text())
    digest = document["files"][file_name]["sha256"]
    document["files"][file_name]["sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
    manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def l5(runs: Path, scratch: Path):
    source = runs / "three-plane"
    observed, messages = {}, {}
    for name in ("response.jsonl", "frames.jsonl", "intersections.jsonl", "metadata.json",
                 "fast-trace.jsonl", "manifest.json"):
        copy_dir = scratch / ("tamper-" + name)
        shutil.copytree(source, copy_dir)
        if name == "manifest.json":
            flip_recorded_hash(copy_dir / name, "response.jsonl")
        else:
            flip_middle(copy_dir / name)
        proc = support.run_cli(["verify", copy_dir], cwd=scratch)
        observed[name] = {"exit": proc.returncode,
                          "message": proc.stderr.startswith("tracksim: error:")
                          and "Traceback" not in proc.stderr}
        messages[name] = proc.stderr.strip()[-300:]
    return observed, {n: {"exit": 1, "message": True} for n in observed}, messages


def l6(scratch: Path):
    package = support.copy_package(scratch / "package-copy")
    out = scratch / "drift-run"
    src = package / "src"
    gen = generate("three-plane", out, env=support.child_env({"PYTHONPATH": str(src)}),
                   cwd=scratch)
    target = src / "tracksim" / "scattering.py"
    with target.open("a") as handle:
        handle.write(STAMP)
    ver = support.run_cli(["verify", out], src=src, cwd=scratch)
    report = json.loads(ver.stdout) if ver.returncode == 0 else {}
    observed = {"generate_exit": gen.returncode, "verify_exit": ver.returncode,
                "status": report.get("status"),
                "changed_sources": report.get("changed_sources")}
    expected = {"generate_exit": 0, "verify_exit": 0,
                "status": "verified_with_source_drift",
                "changed_sources": ["tracksim/scattering.py"]}
    return observed, expected


L7_CHILD = r"""
import json, sys
from pathlib import Path
src, out, config, target = sys.argv[1:5]
sys.path.insert(0, src)
sys.dont_write_bytecode = True
from tracksim import lifecycle
real_respond = lifecycle.respond
def edit_then_respond(run):
    with open(target, "a") as handle:
        handle.write("\n# edited during the run\n")
    return real_respond(run)
lifecycle.respond = edit_then_respond
message = None
try:
    lifecycle.generate(Path(out), json.loads(Path(config).read_text()))
except Exception as exc:
    message = str(exc)
state = json.loads((Path(out) / "run-state.json").read_text())
print(json.dumps({"message": message, "state": state.get("status")}))
"""


def l7(scratch: Path):
    package = support.copy_package(scratch / "package-edit")
    out = scratch / "edit-run"
    config = scratch / "edit-config.json"
    config.write_text(json.dumps(support.example_settings("three-plane")))
    target = package / "src" / "tracksim" / "scattering.py"
    proc = support.run_code(L7_CHILD, package / "src", out, config, target, cwd=scratch,
                            env=support.child_env({"PYTHONPATH": str(package / "src")}))
    if proc.returncode != 0:
        return {"stderr": proc.stderr[-800:]}, {"returncode": 0}
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    observed = {"raised": result["message"] is not None,
                "has_text": "source changed" in (result["message"] or ""),
                "state": result["state"]}
    return observed, {"raised": True, "has_text": True, "state": "failed"}


def l8(runs: Path):
    from tracksim.fast_transport import ALGORITHM
    from tracksim.provenance import python_identity

    proc = support.run_cli(["preflight", "--config", support.EXAMPLES / "three-plane.json"],
                           cwd=runs)
    printed = json.loads(proc.stdout) if proc.returncode == 0 else None
    observed = {"exit": proc.returncode,
                "keys": sorted(printed) if printed else None,
                "values": printed}
    expected = {"exit": 0, "keys": ["algorithm", "backend", "python"],
                "values": {"python": python_identity(), "backend": "fast_electron",
                           "algorithm": ALGORITHM}}
    return observed, expected


def l9():
    with tempfile.TemporaryDirectory(prefix="e2e-l9-") as cwd:
        proc = support.run_cli(["--help"], cwd=cwd)
    return {"exit": proc.returncode, "prints_usage": "usage" in proc.stdout.lower()}, \
        {"exit": 0, "prints_usage": True}


def l10(runs: Path, scratch: Path):
    from tracksim import lifecycle

    copy_dir = scratch / "finalize-copy"
    shutil.copytree(runs / "three-plane", copy_dir)
    manifest = copy_dir / "manifest.json"
    report = copy_dir / "verification" / "report.json"
    before_bytes, before_mtime = manifest.read_bytes(), manifest.stat().st_mtime_ns
    report_bytes, report_mtime = report.read_bytes(), report.stat().st_mtime_ns
    returned = lifecycle.finalize(copy_dir)
    observed = {"returned_equals_manifest": returned == json.loads(before_bytes),
                "bytes_unchanged": manifest.read_bytes() == before_bytes,
                "mtime_unchanged": manifest.stat().st_mtime_ns == before_mtime,
                "report_bytes_unchanged": report.read_bytes() == report_bytes,
                "report_mtime_unchanged": report.stat().st_mtime_ns == report_mtime}
    return observed, {k: True for k in observed}


def main() -> int:
    args = support.parse_args(__doc__)
    report = support.Report("cli_lifecycle", args.evidence)
    runs = args.evidence / "runs"
    if runs.exists():
        shutil.rmtree(runs)
    runs.mkdir(parents=True)

    holder = {}

    def with_detail(cid, fn):
        def wrapped():
            result = fn()
            holder[cid] = result[2] if len(result) > 2 else None
            return result[0], result[1]

        report.check(cid, wrapped)
        if holder.get(cid) is not None:
            report.checks[-1]["details"] = support.jsonable(holder[cid])

    with tempfile.TemporaryDirectory(prefix="e2e-l-") as scratch_name:
        scratch = Path(scratch_name)
        with_detail("L1", lambda: l1(runs))
        report.check("L2", lambda: l2(runs))
        report.check("L3", lambda: l3(scratch))
        report.check("L4", l4)
        with_detail("L5", lambda: l5(runs, scratch))
        report.check("L6", lambda: l6(scratch))
        report.check("L7", lambda: l7(scratch))
        report.check("L8", lambda: l8(runs))
        report.check("L9", l9)
        report.check("L10", lambda: l10(runs, scratch))
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
