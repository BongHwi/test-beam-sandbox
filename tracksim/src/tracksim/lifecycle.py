"""Schema-4 run lifecycle for the neutral JSON writer."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
from typing import Any

from . import registry
from .events import load_events
from .neutral import (
    JSON_FILES,
    neutral_acceptance_level,
    verify_neutral,
    write_neutral,
)
from .provenance import file_hash, python_identity, source_hashes, write_json
from .readout import build_readout
from .response import generate_response
from .runtime import run_path
from .specs import (
    RunSpec,
    canonical_hash,
    normalize_config,
    spec_to_dict,
    validate_capabilities,
)
from .transport import backend_for, simulate_run, verify_transport
from .writers import WriterRegistry


MANIFEST_SCHEMA = "tracksim-modular-run-v4"
_NEUTRAL_FILES = tuple(JSON_FILES)
_RESPONSE_FILES = ("response-settings.json", "response-report.json")
_COMMON_FINAL_FILES = ("acceptance.json", "report.json")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON at {path}: {exc}") from exc


def _normalized_spec_data(spec: RunSpec) -> dict:
    return json.loads(json.dumps(spec_to_dict(spec), allow_nan=False))


def _write(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)


def _state(out: Path, status: str, **extra) -> None:
    _write(out / "run-state.json", {"status": status, **extra})


def _read_spec(out: Path) -> RunSpec:
    settings = _load(out / "settings.json")
    spec = normalize_config(settings)
    _require_schema4(spec)
    validate_capabilities(spec, full_pipeline=True)
    if _load(out / "normalized-spec.json") != _normalized_spec_data(spec):
        raise ValueError("stored normalized spec differs from settings.json")
    return spec


def _require_schema4(spec: RunSpec) -> RunSpec:
    if not isinstance(spec, RunSpec) or spec.schema_version != 4:
        raise ValueError("electron lifecycle requires a schema-4 RunSpec")
    return spec

def _writer(spec: RunSpec):
    if spec.writer != "json":
        raise ValueError(
            f"writer {spec.writer!r} is not supported by this lifecycle"
        )
    writer = WriterRegistry().get(spec.writer)
    writer.validate_support(spec)
    return writer


def _preflight(settings: dict) -> tuple[RunSpec, Any, Any]:
    spec = normalize_config(settings)
    _require_schema4(spec)
    validate_capabilities(spec, full_pipeline=True)
    # Validate backend and writer before creating a run directory. The registry
    # remains the authoritative writer map.
    backend = backend_for(spec)
    writer = _writer(spec)
    backend.required_files(spec)
    return spec, backend, writer


def _preflight_stored_run(out: Path) -> tuple[RunSpec, Any, Any]:
    spec = _read_spec(out)
    validate_capabilities(spec, full_pipeline=True)
    return spec, backend_for(spec), _writer(spec)


def _response_report(response, frames, artifact, spec: RunSpec) -> dict:
    return {
        "status": "responded",
        "schema": response.schema,
        "events": len(response.timeline),
        "outcomes": len(response.outcomes),
        "accepted_outcomes": sum(outcome.accepted for outcome in response.outcomes),
        "hits": len(response.hits),
        "dark_hits": response.dark_hits,
        "horizon_ps": response.horizon_ps,
        "frames": len(frames.frames),
        "assignments": frames.assignments,
        "contributor_links": frames.contributor_links,
        "response_spec_hash": response.response_spec_hash,
        "readout_spec_hash": frames.readout_spec_hash,
        "source_sha256": response.source_sha256,
        "response_provenance": artifact.response_provenance,
        "acceptance_level": artifact.acceptance_level,
        "backend": spec.backend,
        "writer": spec.writer,
        "limitations": list(registry.backend_info(spec.backend).limitations),
    }

def _preexisting_response_paths(out: Path) -> list[str]:
    return [
        name for name in (*_NEUTRAL_FILES, *_RESPONSE_FILES) if (out / name).exists()
    ]



def respond(out: Path) -> dict:
    """Create response, readout and neutral artifacts for a schema-4 run."""
    out = run_path(out)
    spec = _read_spec(out)
    writer = _writer(spec)
    if not (out / "intersections.jsonl").is_file():
        raise ValueError("electron response requires an existing transport stream")
    collisions = _preexisting_response_paths(out)
    if collisions:
        raise FileExistsError(
            f"refusing to overwrite electron response artifact(s): {collisions}"
        )

    try:
        _state(out, "responding")
        transport = verify_transport(out, spec)
        events = load_events(
            out / "intersections.jsonl",
            spec.detectors,
            limits=spec.limits,
            event_limit=spec.events,
            deposition_available=spec.deposition_available,
        )
        source_sha256 = file_hash(out / "intersections.jsonl")
        response = generate_response(events, spec, source_sha256=source_sha256)
        frames = build_readout(response, spec)

        _state(out, "writing-neutral")
        artifact = writer.write(response, frames, spec, out)
        neutral_check = writer.verify(spec, out, events=events)
        if neutral_check.get("valid") is not True:
            raise ValueError("electron neutral artifact readback failed")
        report = _response_report(response, frames, artifact, spec)
        report["transport_verification"] = transport
        report["neutral_verification"] = neutral_check
        _write(out / "response-settings.json", spec.raw_settings)
        _write(out / "response-report.json", report)
        _state(out, "response-complete", acceptance_level=artifact.acceptance_level)
        return report
    except Exception as exc:
        _state(out, "failed", error=str(exc))
        raise


def _source_status(snapshot: dict, current: dict) -> tuple[str, list[str]]:
    changed = sorted(
        name
        for name in set(snapshot) | set(current)
        if snapshot.get(name) != current.get(name)
    )
    return ("matches" if not changed else "drifted", changed)


def _validate_runtime_shapes(
    spec: RunSpec, runtime_data: dict, stored_python: dict
) -> None:
    expected_python = python_identity()
    if not isinstance(stored_python, dict) or set(stored_python) != set(
        expected_python
    ):
        raise ValueError("recorded Python identity has an invalid schema")
    if any(
        not isinstance(value, type(expected_python[key]))
        for key, value in stored_python.items()
    ):
        raise ValueError("recorded Python identity has invalid field types")
    if not isinstance(runtime_data, dict):
        raise ValueError("manifest runtime must be an object")
    required_runtime = {"transport", "python"}
    if set(runtime_data) != required_runtime:
        raise ValueError(
            f"manifest runtime keys differ from selected capability: {sorted(required_runtime)}"
        )
    if runtime_data.get("python") != stored_python:
        raise ValueError(
            "manifest Python runtime differs from simulation-start identity"
        )
    backend_for(spec).validate_runtime_record(
        spec, runtime_data.get("transport"), stored_python
    )


_IMPLEMENTED_RESPONSE = ["boundary_resolution", "boundary_cluster_mean"]
_IMPLEMENTED_READOUT = ["triggered", "continuous"]


def _capabilities(spec: RunSpec) -> dict:
    return {
        "requested": {
            "transport": spec.backend,
            "response": "builtin",
            "readout": list(dict.fromkeys(d.readout.model for d in spec.detectors)),
            "writer": spec.writer,
        },
        "implemented": {
            "transport": list(registry.backend_names()),
            "response": list(_IMPLEMENTED_RESPONSE),
            "readout": list(_IMPLEMENTED_READOUT),
            "writers": list(registry.writer_names()),
        },
    }


def _capabilities_match(spec: RunSpec, recorded: Any) -> bool:
    """Check recorded capabilities against the run, not against the live registry.

    The requested block must equal the run's own; the recorded lists of
    implemented backends and writers must contain the run's backend and writer.
    """
    if not isinstance(recorded, dict) or set(recorded) != {"requested", "implemented"}:
        return False
    if recorded["requested"] != _capabilities(spec)["requested"]:
        return False
    implemented = recorded["implemented"]
    if not isinstance(implemented, dict) or set(implemented) != {
        "transport",
        "response",
        "readout",
        "writers",
    }:
        return False
    transports, writers = implemented["transport"], implemented["writers"]
    return (
        isinstance(transports, list)
        and isinstance(writers, list)
        and all(isinstance(name, str) for name in (*transports, *writers))
        and spec.backend in transports
        and spec.writer in writers
        and implemented["response"] == _IMPLEMENTED_RESPONSE
        and implemented["readout"] == _IMPLEMENTED_READOUT
    )


def _required_files(spec: RunSpec) -> tuple[str, ...]:
    names = list(backend_for(spec).required_files(spec))
    names.extend((*_RESPONSE_FILES, *_NEUTRAL_FILES, *_COMMON_FINAL_FILES))
    return tuple(dict.fromkeys(names))


def _read_source_snapshot(out: Path) -> dict:
    snapshot = _load(out / "source-snapshot.json")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("source-at-start snapshot is missing or invalid")
    if any(
        not isinstance(key, str) or not _valid_digest(value)
        for key, value in snapshot.items()
    ):
        raise ValueError("source-at-start snapshot must map paths to SHA-256 digests")
    for name in snapshot:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("source snapshot path is not relative and contained")
    return snapshot


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )



def _artifact_index(out: Path, names: tuple[str, ...]) -> dict:
    _validate_artifact_containment(out, names)
    missing = [name for name in names if not (out / name).is_file()]
    if missing:
        raise ValueError(
            "electron completion is missing artifacts: " + ", ".join(missing)
        )
    return {
        name: {"sha256": file_hash(out / name), "bytes": (out / name).stat().st_size}
        for name in names
    }

def _validate_artifact_containment(out: Path, names: tuple[str, ...]) -> None:
    out = Path(out).resolve()
    for name in names:
        relative = PurePosixPath(name)
        target = (out / Path(name)).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.is_relative_to(out)
        ):
            raise ValueError("manifest path must stay inside the run directory")



def _acceptance(spec: RunSpec, neutral: dict) -> dict:
    level = neutral_acceptance_level(spec, neutral.get("response_provenance", ""))
    if (
        neutral.get("valid") is not True
        or neutral.get("response_provenance") != "builtin"
    ):
        raise ValueError(
            "completed electron runs require builtin verified neutral artifacts"
        )
    return {
        "pipeline_pass": True,
        "acceptance_level": level,
        "backend": spec.backend,
        "writer": spec.writer,
        "deposition_available": spec.deposition_available,
        "neutral_verification": neutral,
        "criterion": "schema, transport, response/readout and neutral relations",
        "limitations": list(registry.backend_info(spec.backend).limitations),
    }


def _manifest(
    out: Path,
    spec: RunSpec,
    acceptance: dict,
    snapshot: dict,
    transport_runtime: dict,
    py: dict,
) -> dict:
    names = _required_files(spec)
    files = _artifact_index(out, names)
    runtime = {"transport": transport_runtime, "python": py}
    return {
        "schema": MANIFEST_SCHEMA,
        "complete": True,
        "synthetic": True,
        "acceptance_level": acceptance["acceptance_level"],
        "backend": spec.backend,
        "algorithm": spec.transport.algorithm,
        "deposition_available": spec.deposition_available,
        "writer": spec.writer,
        "layout_scope": spec.preset,
        "capabilities": _capabilities(spec),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "files": files,
        "source_hashes": snapshot,
        "assumptions": spec.assumptions,
        "limitations": list(registry.backend_info(spec.backend).limitations),
    }


def _restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        temp = path.with_name(path.name + ".restore")
        temp.write_bytes(previous)
        temp.replace(path)



def finalize(out: Path) -> dict:
    """Publish completion only after transport and the writer verify."""
    out = run_path(out)
    spec, backend, writer = _preflight_stored_run(out)
    if (out / "manifest.json").is_file():
        state = _load(out / "run-state.json")
        status = state.get("status") if isinstance(state, dict) else None
        if status not in ("complete", "finalizing"):
            raise ValueError("run-state.json does not record a completed run")
        verified = _verify_run(out, require_complete=False)
        if verified.get("valid") is not True:
            raise ValueError("existing electron manifest did not verify")
        manifest = _load(out / "manifest.json")
        if status == "finalizing":
            _state(out, "complete", acceptance_level=manifest["acceptance_level"])
        return manifest

    output_paths = [out / "acceptance.json", out / "report.json", out / "manifest.json"]
    previous_bytes = {
        path: path.read_bytes() if path.is_file() else None for path in output_paths
    }
    published_manifest = False
    try:
        _state(out, "finalizing")
        snapshot = _read_source_snapshot(out)
        current_sources = source_hashes()
        source_status, changed = _source_status(snapshot, current_sources)
        if source_status != "matches":
            raise RuntimeError(
                "source changed during electron run: " + ", ".join(changed)
            )
        py = _load(out / "python-runtime.json")
        if py != python_identity():
            raise RuntimeError("Python runtime changed during electron run")
        stored_transport_runtime = _load(out / "runtime.json")
        backend.validate_runtime_record(spec, stored_transport_runtime, py)
        if stored_transport_runtime != backend.runtime_identity(out):
            raise ValueError("backend runtime identity differs from runtime.json")
        envelope = _load(out / "transport-envelope.json")
        if (
            envelope.get("python") != py
            or envelope.get("runtime") != stored_transport_runtime
        ):
            raise ValueError(
                "transport envelope runtime differs from simulation-start identities"
            )
        verify_transport(out, spec)
        events = load_events(
            out / "intersections.jsonl",
            spec.detectors,
            limits=spec.limits,
            event_limit=spec.events,
            deposition_available=spec.deposition_available,
        )
        neutral = verify_neutral(spec, out, events=events)
        if neutral.get("valid") is not True:
            raise ValueError(
                "electron neutral artifacts failed relational verification"
            )
        response_report = _load(out / "response-report.json")
        if _load(out / "response-settings.json") != spec.raw_settings:
            raise ValueError("response settings differ from validated run settings")
        if (
            response_report.get("backend") != spec.backend
            or response_report.get("writer") != spec.writer
            or response_report.get("response_provenance") != "builtin"
        ):
            raise ValueError(
                "electron response report capability/settings differ from the selected run"
            )

        _validate_artifact_containment(out, _required_files(spec))
        acceptance = _acceptance(spec, neutral)
        report = {
            "status": "complete",
            "acceptance_level": acceptance["acceptance_level"],
            "backend": spec.backend,
            "writer": spec.writer,
            "events": len(events),
            "detectors": [detector.id for detector in spec.detectors],
            "response": response_report,
            "transport": _load(out / "transport-envelope.json")["report"],
            "physics_hypothesis": registry.backend_info(
                spec.backend
            ).physics_hypothesis,
            "limitations": acceptance["limitations"],
        }
        _write(out / "acceptance.json", acceptance)
        _write(out / "report.json", report)

        if source_hashes() != snapshot:
            raise RuntimeError("source changed before electron manifest publication")
        if python_identity() != py:
            raise RuntimeError(
                "Python runtime changed before electron manifest publication"
            )
        transport_runtime = backend.runtime_identity(out)
        manifest = _manifest(out, spec, acceptance, snapshot, transport_runtime, py)
        _write(out / "manifest.json", manifest)
        published_manifest = True
        _state(out, "complete", acceptance_level=manifest["acceptance_level"])
        return manifest
    except Exception as exc:
        if published_manifest and previous_bytes[out / "manifest.json"] is None:
            (out / "manifest.json").unlink(missing_ok=True)
        for path in output_paths[:2]:
            _restore_file(path, previous_bytes[path])
        _state(out, "failed", error=str(exc))
        raise


def _manifest_keys() -> set[str]:
    return {
        "schema",
        "complete",
        "synthetic",
        "acceptance_level",
        "backend",
        "algorithm",
        "deposition_available",
        "writer",
        "layout_scope",
        "capabilities",
        "created_utc",
        "runtime",
        "files",
        "source_hashes",
        "assumptions",
        "limitations",
    }



def _verify_manifest_paths(out: Path, spec: RunSpec, manifest: dict) -> list[str]:
    if set(manifest) != _manifest_keys():
        raise ValueError("schema-4 manifest keys differ from the required schema")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("complete") is not True
        or manifest.get("synthetic") is not True
        or manifest.get("backend") != spec.backend
        or manifest.get("algorithm") != spec.transport.algorithm
        or manifest.get("deposition_available") is not spec.deposition_available
        or manifest.get("writer") != spec.writer
        or manifest.get("layout_scope") != spec.preset
        or manifest.get("acceptance_level") != neutral_acceptance_level(spec, "builtin")
        or not _capabilities_match(spec, manifest.get("capabilities"))
        or manifest.get("limitations")
        != list(registry.backend_info(spec.backend).limitations)
        or not isinstance(manifest.get("created_utc"), str)
        or not manifest["created_utc"]
    ):
        raise ValueError(
            "completed schema-4 electron manifest has invalid identity or scope"
        )
    assumptions = manifest.get("assumptions")
    if not isinstance(assumptions, dict) or assumptions != spec.assumptions:
        raise ValueError("manifest assumptions differ from validated electron settings")

    required = set(_required_files(spec))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or set(files) != required:
        missing = (
            sorted(required - set(files))
            if isinstance(files, dict)
            else sorted(required)
        )
        extra = sorted(set(files) - required) if isinstance(files, dict) else []
        raise ValueError(
            f"manifest files differ from required artifacts; missing={missing}, extra={extra}"
        )
    for name, row in files.items():
        path = PurePosixPath(name)
        target = (out / Path(name)).resolve()
        if path.is_absolute() or ".." in path.parts or not target.is_relative_to(out):
            raise ValueError("manifest path must stay inside the run directory")
        if (
            not isinstance(row, dict)
            or set(row) != {"sha256", "bytes"}
            or not _valid_digest(row.get("sha256"))
            or isinstance(row.get("bytes"), bool)
            or not isinstance(row.get("bytes"), int)
            or row["bytes"] < 0
        ):
            raise ValueError(f"invalid manifest file entry: {name}")
        if (
            not target.is_file()
            or target.stat().st_size != row["bytes"]
            or file_hash(target) != row["sha256"]
        ):
            raise ValueError(f"manifest file hash/size mismatch: {name}")

    snapshot = _read_source_snapshot(out)
    source_hashes_manifest = manifest.get("source_hashes")
    if source_hashes_manifest != snapshot:
        raise ValueError("manifest source hashes differ from source-at-start snapshot")
    return list(required)


def verify(out: Path, manifest: dict | None = None) -> dict:
    """Verify files and relations and report source/Python drift explicitly.

    The report is also written to ``verification/report.json``.
    """
    out = run_path(out)
    result = _verify_run(out, manifest)
    _write(out / "verification" / "report.json", result)
    return result


def _verify_run(
    out: Path, manifest: dict | None = None, *, require_complete: bool = True
) -> dict:
    """Verify a run directory and return the report without writing it."""
    if require_complete:
        state = _load(out / "run-state.json")
        if not isinstance(state, dict) or state.get("status") != "complete":
            raise ValueError("run-state.json does not record a completed run")
    on_disk = _load(out / "manifest.json")
    if not isinstance(on_disk, dict):
        raise ValueError("electron manifest must be an object")
    if manifest is not None and canonical_hash(manifest) != canonical_hash(on_disk):
        raise ValueError("supplied manifest differs from the run's manifest.json")
    manifest = on_disk
    settings = _load(out / "settings.json")
    spec = normalize_config(settings)
    _require_schema4(spec)
    validate_capabilities(spec, full_pipeline=True)
    if _load(out / "normalized-spec.json") != _normalized_spec_data(spec):
        raise ValueError("stored normalized spec differs from settings.json")
    _verify_manifest_paths(out, spec, manifest)
    backend = backend_for(spec)
    writer = _writer(spec)
    recorded_python = _load(out / "python-runtime.json")
    runtime_data = manifest.get("runtime")
    _validate_runtime_shapes(spec, runtime_data, recorded_python)
    stored_transport_runtime = _load(out / "runtime.json")
    backend.validate_runtime_record(spec, stored_transport_runtime, recorded_python)
    if runtime_data["transport"] != stored_transport_runtime:
        raise ValueError("manifest transport runtime differs from runtime.json")
    if backend.runtime_identity(out) != stored_transport_runtime:
        raise ValueError("transport runtime identity differs from stored runtime.json")
    envelope = _load(out / "transport-envelope.json")
    if (
        envelope.get("python") != recorded_python
        or envelope.get("runtime") != stored_transport_runtime
    ):
        raise ValueError(
            "transport envelope runtime differs from simulation-start identities"
        )

    transport_report = verify_transport(out, spec)
    events = load_events(
        out / "intersections.jsonl",
        spec.detectors,
        limits=spec.limits,
        event_limit=spec.events,
        deposition_available=spec.deposition_available,
    )
    neutral = verify_neutral(spec, out, events=events)
    if (
        neutral.get("valid") is not True
        or neutral.get("response_provenance") != "builtin"
    ):
        raise ValueError("electron neutral artifact relational verification failed")
    if _load(out / "response-settings.json") != spec.raw_settings:
        raise ValueError("response settings differ from validated run settings")
    response_report = _load(out / "response-report.json")
    if (
        response_report.get("backend") != spec.backend
        or response_report.get("writer") != spec.writer
        or response_report.get("response_provenance") != "builtin"
    ):
        raise ValueError(
            "electron response report capability differs from selected settings"
        )
    acceptance = _load(out / "acceptance.json")
    report = _load(out / "report.json")
    if (
        acceptance.get("pipeline_pass") is not True
        or acceptance.get("acceptance_level") != manifest["acceptance_level"]
        or acceptance.get("backend") != spec.backend
        or acceptance.get("writer") != spec.writer
        or acceptance.get("deposition_available") is not spec.deposition_available
    ):
        raise ValueError("electron acceptance report has invalid scope or capability")
    if (
        report.get("status") != "complete"
        or report.get("backend") != spec.backend
        or report.get("writer") != spec.writer
        or report.get("events") != spec.events
        or report.get("acceptance_level") != manifest["acceptance_level"]
    ):
        raise ValueError(
            "electron completion report differs from the manifest/settings"
        )
    writer_report = writer.verify(spec, out, events=events)
    if writer_report.get("valid") is not True:
        raise ValueError("selected electron writer verification failed")

    current_sources = source_hashes()
    source_status, changed_sources = _source_status(
        manifest["source_hashes"], current_sources
    )
    current_python = python_identity()
    python_status = "matches" if recorded_python == current_python else "drifted"
    runtime_status = "drifted" if python_status == "drifted" else "matches"
    if source_status == "matches" and runtime_status == "matches":
        status = "verified"
    elif source_status == "drifted" and runtime_status == "drifted":
        status = "verified_with_source_and_runtime_drift"
    elif source_status == "drifted":
        status = "verified_with_source_drift"
    else:
        status = "verified_with_runtime_drift"
    result = {
        "status": status,
        "valid": True,
        "schema": MANIFEST_SCHEMA,
        "acceptance_level": manifest["acceptance_level"],
        "backend": spec.backend,
        "writer": spec.writer,
        "files_checked": len(manifest["files"]),
        "source_status": source_status,
        "changed_sources": changed_sources,
        "runtime_status": runtime_status,
        "python_runtime_status": python_status,
        "recorded_python": recorded_python,
        "current_python": current_python,
        "transport_verification": transport_report,
        "neutral_verification": neutral,
        "writer_verification": writer_report,
        "provenance_note": "manifest hashes and runtime identities are recorded metadata; they are not signed or authenticated",
    }
    return result


def generate(out: Path, settings: dict) -> dict:
    """Generate a complete schema-4 run: transport, response, readout, writer."""
    spec, backend, writer = _preflight(settings)
    out = run_path(out)
    if out.exists():
        raise FileExistsError(f"electron output already exists: {out}")
    start_sources = source_hashes()
    try:
        backend.simulate(spec, out)
        if _load(out / "source-snapshot.json") != start_sources:
            raise RuntimeError(
                "source changed between electron preflight and transport start"
            )
        respond(out)
        return finalize(out)
    except Exception as exc:
        if out.is_dir() and not (out / "manifest.json").is_file():
            _state(out, "failed", error=str(exc))
        raise
