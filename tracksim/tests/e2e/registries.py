"""Extension-point checks X1-X14.

Registrations are process-global and append-only, so every case runs in its
own interpreter (``--case ID``).  Only invented demo names are used.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

import _support as support

SELF = Path(__file__).resolve()
CASE_IDS = [f"X{i}" for i in range(1, 15)]

DEMO_STRINGS = {
    "transport": "demo transport limitation text",
    "material": "demo material limitation text",
    "acceptance": "demo-acceptance-level",
    "hypothesis": "demo-physics-hypothesis",
    "limitations": ("demo limitation one", "demo limitation two"),
}


# --------------------------------------------------------------------- helpers


def modules():
    support.use_source()
    from tracksim import lifecycle, provenance, registry, specs, writers
    return registry, specs, writers, lifecycle, provenance


def base_settings(**overrides) -> dict:
    settings = support.example_settings("three-plane")
    settings["events"] = 12
    settings.update(overrides)
    return settings


def error_of(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc).__name__, str(exc)
    return None, None


def demo_backend_info(registry, name: str, **overrides):
    info = registry.backend_info("fast_electron")
    values = {
        "name": name,
        "neutral_transport_limitation": DEMO_STRINGS["transport"],
        "neutral_material_limitation": DEMO_STRINGS["material"],
        "acceptance_level": DEMO_STRINGS["acceptance"],
        "physics_hypothesis": DEMO_STRINGS["hypothesis"],
        "limitations": DEMO_STRINGS["limitations"],
    }
    values.update(overrides)
    return dataclasses.replace(info, **values)


class DemoWriter:
    """Minimal object satisfying the writer protocol; it never writes."""

    name = "demo_writer"

    def validate_support(self, spec):
        return None

    def write(self, response, frames, spec, out):
        raise RuntimeError("demo writer is never driven")

    def verify(self, spec, out, *, events=None):
        raise RuntimeError("demo writer is never driven")


def register_demo_writer(registry, name="demo_writer"):
    registry.register_writer(name, DemoWriter)


def cli_generate(settings: dict, tmp: Path):
    config = tmp / "config.json"
    config.write_text(json.dumps(settings))
    out = tmp / "run-out"
    proc = support.run_cli(["generate", "--config", config, "--out", out], cwd=tmp)
    return proc, out


def rejection_case(key: str, value, needle: str):
    """Unregistered name: normalisation message, CLI exit code and no output directory."""
    registry, specs, writers, _l, _p = modules()
    settings = base_settings(**{key: value})
    kind, message = error_of(lambda: specs.normalize_config(copy.deepcopy(settings)))
    with tempfile.TemporaryDirectory(prefix="e2e-x-") as tmp:
        proc, out = cli_generate(settings, Path(tmp))
        cli = {"returncode": proc.returncode, "out_exists": out.exists(),
               "stderr_prefix": proc.stderr.startswith("tracksim: error:")}
    observed = {"error": kind, "has_needle": needle in (message or ""), "cli": cli}
    expected = {"error": "ValueError", "has_needle": True,
                "cli": {"returncode": 1, "out_exists": False, "stderr_prefix": True}}
    extra = {"message": message}
    return observed, expected, extra


# ----------------------------------------------------------------------- cases


def x1():
    return rejection_case("preset", "demo_preset", "registered presets: generic")


def x2():
    return rejection_case("backend", "demo_backend", "registered backends: fast_electron")


def x3():
    observed, expected, extra = rejection_case("writer", "demo_writer",
                                               "registered writers: json")
    _r, _s, writers, _l, _p = modules()
    kind, message = error_of(lambda: writers.WriterRegistry().get("demo_writer"))
    observed["get"] = {"error": kind, "has_text": "unsupported writer" in (message or "")}
    expected["get"] = {"error": "ValueError", "has_text": True}
    extra["get_message"] = message
    return observed, expected, extra


def x4():
    _r, specs, _w, _l, _p = modules()
    observed, messages = {}, {}
    for version in (7, 99):
        settings = base_settings(schema_version=version)
        kind, message = error_of(lambda: specs.normalize_config(settings))
        observed[str(version)] = {"error": kind,
                                  "has_text": "unsupported schema_version" in (message or "")}
        messages[str(version)] = message
    expected = {v: {"error": "ValueError", "has_text": True} for v in ("7", "99")}
    return observed, expected, messages


def x5():
    registry, specs, writers, _l, provenance = modules()
    generic_spec = specs.normalize_config(base_settings())
    fast = registry.backend_info("fast_electron")
    handler4 = registry.schema_handler(4)
    registry.register_preset(registry.Preset("demo_preset", lambda d, t: d))
    attempts = {
        "preset generic": lambda: registry.register_preset(
            registry.Preset("generic", lambda d, t: d)),
        "backend fast_electron": lambda: registry.register_backend(fast),
        "writer json": lambda: registry.register_writer("json", DemoWriter),
        "schema 4": lambda: registry.register_schema(handler4),
        "preset duplicate": lambda: registry.register_preset(
            registry.Preset("demo_preset", lambda d, t: d)),
        "preset not identifier": lambda: registry.register_preset(
            registry.Preset("demo preset", lambda d, t: d)),
        "backend not identifier": lambda: registry.register_backend(
            dataclasses.replace(fast, name="demo-backend")),
        "writer not identifier": lambda: registry.register_writer("demo writer", DemoWriter),
    }
    observed, messages = {}, {}
    for label, attempt in attempts.items():
        kind, message = error_of(attempt)
        observed[label] = kind
        messages[label] = message
    expected = {label: "ValueError" for label in attempts}
    observed["names_unchanged"] = [list(registry.backend_names()),
                                   list(registry.writer_names())]
    expected["names_unchanged"] = [["fast_electron"], ["json"]]
    return observed, expected, messages


def x6():
    registry, specs, _w, lifecycle, _p = modules()
    generic = specs.normalize_config(base_settings())

    def apply(detectors, timeline):
        return tuple(dataclasses.replace(d, label="demo") for d in detectors)

    def rows(spec):
        return [{"name": d.id, "tag": "demo-rows"} for d in spec.detectors]

    registry.register_preset(registry.Preset("demo_preset", apply, geometry_rows=rows))
    settings = base_settings(preset="demo_preset")
    spec = specs.normalize_config(copy.deepcopy(settings))
    with tempfile.TemporaryDirectory(prefix="e2e-x6-") as tmp:
        out = Path(tmp) / "run"
        lifecycle.generate(out, copy.deepcopy(settings))
        written = json.loads((out / "geometry.json").read_text())
    expected_rows = [{"name": d.id, "tag": "demo-rows"} for d in spec.detectors]
    observed = {
        "kinds": [d.label for d in spec.detectors],
        "hook_rows": specs.geometry_rows(spec) == expected_rows,
        "geometry_json": written == expected_rows,
        "fingerprint_equals_generic": specs.transport_fingerprint(spec)
        == specs.transport_fingerprint(generic),
    }
    expected = {"kinds": ["demo"] * 3, "hook_rows": True, "geometry_json": True,
                "fingerprint_equals_generic": True}
    return observed, expected, {}


def x7():
    _r, specs, _w, _l, _p = modules()
    spec = specs.normalize_config(base_settings())
    kind, message = error_of(
        lambda: specs.geometry_rows(dataclasses.replace(spec, preset="ghost")))
    return {"error": kind}, {"error": "ValueError"}, {"message": message}


def x8():
    registry, specs, _w, _l, _p = modules()
    spec = specs.normalize_config(base_settings())
    seen = []

    def check(candidate, writer):
        seen.append((candidate.preset, writer))
        if candidate.preset == "generic" and writer == "demo_writer":
            raise ValueError("demo capability rejected")

    register_demo_writer(registry)
    registry.register_capability_check(check)
    specs.validate_capabilities(spec)
    kind_n, message_n = error_of(
        lambda: specs.normalize_config(base_settings(writer="demo_writer")))
    kind_v, message_v = error_of(lambda: specs.validate_capabilities(spec, writer="demo_writer"))
    observed = {"called_for_json": ("generic", "json") in seen,
                "normalize": [kind_n, message_n], "validate": [kind_v, message_v]}
    expected = {"called_for_json": True,
                "normalize": ["ValueError", "demo capability rejected"],
                "validate": ["ValueError", "demo capability rejected"]}
    return observed, expected, {}


def x9():
    registry, specs, writers, lifecycle, _p = modules()
    from tracksim import neutral
    from tracksim.fast_transport import iter_transport
    from tracksim.readout import build_readout
    from tracksim.response import generate_response

    registry.register_backend(demo_backend_info(registry, "demo_backend"))
    spec = specs.normalize_config(base_settings())
    demo = dataclasses.replace(
        spec, transport=dataclasses.replace(spec.transport, backend="demo_backend"))
    events = [event for event, _t in iter_transport(spec)]
    response = generate_response(events, demo, source_sha256=support.DEMO_DIGEST)
    frames = build_readout(response, demo)
    with tempfile.TemporaryDirectory(prefix="e2e-x9-") as tmp:
        neutral.write_neutral(response, frames, demo, Path(tmp))
        metadata = json.loads((Path(tmp) / "metadata.json").read_text())
    artifact = writers.ArtifactSet("json", (), DEMO_STRINGS["acceptance"], "builtin")
    report = lifecycle._response_report(response, frames, artifact, demo)
    observed = {
        "limitations.transport": metadata["limitations"]["transport"],
        "limitations.material": metadata["limitations"]["material"],
        "acceptance_level": metadata["acceptance_level"],
        "implemented.transport": lifecycle._capabilities(demo)["implemented"]["transport"],
        "report.limitations": list(report["limitations"]),
    }
    expected = {
        "limitations.transport": DEMO_STRINGS["transport"],
        "limitations.material": DEMO_STRINGS["material"],
        "acceptance_level": DEMO_STRINGS["acceptance"],
        "implemented.transport": ["fast_electron", "demo_backend"],
        "report.limitations": list(DEMO_STRINGS["limitations"]),
    }
    return observed, expected, {}


def x10():
    registry, specs, _w, lifecycle, _p = modules()
    register_demo_writer(registry)
    settings = base_settings(writer="demo_writer")
    specs.normalize_config(copy.deepcopy(settings))
    with tempfile.TemporaryDirectory(prefix="e2e-x10-") as tmp:
        out = Path(tmp) / "run"
        kind, message = error_of(lambda: lifecycle.generate(out, settings))
        exists = out.exists()
    observed = {"error": kind, "out_exists": exists}
    return observed, {"error": "ValueError", "out_exists": False}, {"message": message}


def x11():
    registry, specs, _w, lifecycle, provenance = modules()
    with tempfile.TemporaryDirectory(prefix="e2e-x11-") as tmp:
        root = Path(tmp) / "demo-root"
        (root / "sub").mkdir(parents=True)
        (root / "a.txt").write_text("alpha")
        (root / "sub" / "b.txt").write_text("beta")
        provenance.register_source_root("demo", root)
        keys = sorted(k for k in provenance.source_hashes() if k.startswith("demo/"))
        real_respond = lifecycle.respond

        def edit_then_respond(out):
            with (root / "a.txt").open("a") as handle:
                handle.write("changed")
            return real_respond(out)

        lifecycle.respond = edit_then_respond
        out = Path(tmp) / "run"
        kind, message = error_of(lambda: lifecycle.generate(out, base_settings()))
    observed = {"keys": keys, "raised": kind is not None,
                "has_text": "source changed" in (message or "")}
    expected = {"keys": ["demo/a.txt", "demo/sub/b.txt"], "raised": True,
                "has_text": True}
    return observed, expected, {"message": message}


def x12():
    registry, _s, _w, _l, _p = modules()
    registry.register_backend(demo_backend_info(registry, "demo_backend"))
    registry.register_backend(demo_backend_info(registry, "demo_backend2"))
    register_demo_writer(registry, "demo_writer")
    register_demo_writer(registry, "demo_writer2")
    observed = {"backends": list(registry.backend_names()),
                "writers": list(registry.writer_names())}
    expected = {"backends": ["fast_electron", "demo_backend", "demo_backend2"],
                "writers": ["json", "demo_writer", "demo_writer2"]}
    return observed, expected, {}


def x13():
    registry, specs, _w, _l, _p = modules()
    template = specs.normalize_config(base_settings())
    produced = dataclasses.replace(template, schema_version=99)
    handler = registry.SchemaHandler(
        version=99,
        normalize=lambda settings: produced,
        validate_capabilities=lambda spec, writer: None,
    )
    registry.register_schema(handler)
    spec = specs.normalize_config({"schema_version": 99})
    kind, message = error_of(lambda: dataclasses.replace(spec, transport=None).backend)
    observed = {"schema": spec.schema_version, "backend": spec.backend,
                "no_transport": [kind, "spec declares no transport backend" in (message or "")]}
    expected = {"schema": 99, "backend": "fast_electron", "no_transport": ["ValueError", True]}
    return observed, expected, {}


def x14():
    _r, specs, _w, _l, _p = modules()
    spec = specs.normalize_config(base_settings())
    ghost = dataclasses.replace(
        spec, transport=dataclasses.replace(spec.transport, backend="ghost"))
    kind, message = error_of(lambda: ghost.deposition_available)
    return {"error": kind}, {"error": "ValueError"}, {"message": message}


CASES = {f"X{i}": fn for i, fn in
         enumerate((x1, x2, x3, x4, x5, x6, x7, x8, x9, x10, x11, x12, x13, x14), 1)}


def run_one(case: str) -> None:
    support.use_source()
    result = CASES[case]()
    observed, expected = result[0], result[1]
    support.emit_case(support.jsonable(observed) == support.jsonable(expected),
                      {"observed": observed, "details": result[2] if len(result) > 2 else {}},
                      expected)


def main() -> int:
    if "--case" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--case", required=True)
        run_one(parser.parse_args().case)
        return 0
    args = support.parse_args(__doc__)
    report = support.Report("registries", args.evidence)
    for cid in CASE_IDS:
        result = support.run_case(SELF, cid)
        status = "pass" if result["ok"] else "fail"
        observed = result["observed"]
        if isinstance(observed, dict) and "observed" in observed:
            report.add(cid, status, observed["observed"], result["expected"],
                       None)
            report.checks[-1]["details"] = observed.get("details")
        else:
            report.add(cid, status, observed, result["expected"], "case did not complete")
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
