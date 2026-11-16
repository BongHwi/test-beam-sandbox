"""Strict standard-library serialization and relational verification for neutral runs."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from . import registry
from .events import EventValidationError, load_events, validate_event
from .readout import build_readout
from .records import (
    Contributor,
    Frame,
    FramePixel,
    FrameSet,
    Outcome,
    PixelHit,
    ResponseResult,
    Trigger,
)
from .response import NEUTRAL_PDG, schedule
from .response_models import ResponseRegistry
from .specs import RunSpec, canonical_hash, response_fingerprint, spec_to_dict


NEUTRAL_SCHEMA = "tracksim.neutral-run.v2"
RESPONSE_SCHEMA = "tracksim.response.v2"
FRAME_SCHEMA = "tracksim.frame-set.v1"
JSON_FILES = ("metadata.json", "response.jsonl", "frames.jsonl")


class NeutralError(ValueError):
    """Invalid neutral artifact or relational consistency failure."""


def neutral_acceptance_level(spec: RunSpec, provenance: str) -> str:
    """Structural consistency scope."""
    if provenance != "builtin":
        return "generic-structural-unverified-response"
    return registry.backend_info(spec.backend).acceptance_level


def _neutral_schema(spec: RunSpec) -> str:
    return NEUTRAL_SCHEMA


def _response_schema(spec: RunSpec) -> str:
    return RESPONSE_SCHEMA


def _charge(value, spec: RunSpec):
    if not spec.deposition_available:
        if value is not None:
            raise NeutralError("fast response requires explicit null deposited charge")
        return None
    return _as_number(value, "outcome.deposited_charge_e", minimum=0)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise NeutralError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    else:
        text = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return text.encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NeutralError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise NeutralError(f"non-standard JSON number is not permitted: {value}")


def _line_bytes(record: dict[str, Any]) -> bytes:
    return _json_bytes(record) + b"\n"


def _response_records(result: ResponseResult) -> bytes:
    rows = [
        {
            "record": "metadata",
            "schema": result.schema,
            "source_sha256": result.source_sha256,
            "response_spec_hash": result.response_spec_hash,
            "horizon_ps": result.horizon_ps,
            "dark_hits": result.dark_hits,
            "provenance": result.provenance,
        }
    ]
    rows.extend({"record": "timeline", **asdict(row)} for row in result.timeline)
    rows.extend({"record": "outcome", **asdict(row)} for row in result.outcomes)
    rows.extend({"record": "hit", **asdict(row)} for row in result.hits)
    return b"".join(_line_bytes(row) for row in rows)


def _frame_records(frames: FrameSet) -> bytes:
    rows = [
        {
            "record": "metadata",
            "schema": frames.schema,
            "readout_spec_hash": frames.readout_spec_hash,
            "horizon_ps": frames.horizon_ps,
            "assignments": frames.assignments,
            "contributor_links": frames.contributor_links,
        }
    ]
    rows.extend({"record": "frame", **asdict(row)} for row in frames.frames)
    return b"".join(_line_bytes(row) for row in rows)


def _atomic_bundle(out: Path, files: dict[str, bytes]) -> None:
    out = Path(out)
    if not out.is_dir():
        raise NeutralError(f"output directory does not exist: {out}")
    destinations = [out / name for name in files]
    existing = [path.name for path in destinations if path.exists()]
    if existing:
        raise NeutralError(f"refusing to overwrite neutral artifact(s): {existing}")
    temporary: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for name, payload in files.items():
            with tempfile.NamedTemporaryFile(
                "wb", dir=out, prefix=f".{name}.", suffix=".pending", delete=False
            ) as stream:
                temp = Path(stream.name)
                temporary[out / name] = temp
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        # Publish metadata last; its presence marks a complete neutral bundle.
        for destination in destinations:
            if destination.name == "metadata.json":
                continue
            os.replace(temporary.pop(destination), destination)
            published.append(destination)
        metadata_path = out / "metadata.json"
        os.replace(temporary.pop(metadata_path), metadata_path)
        published.append(metadata_path)
    except Exception:
        for path in published:
            if path.exists():
                path.unlink()
        raise
    finally:
        for temp in temporary.values():
            if temp.exists():
                temp.unlink()


def write_neutral(
    response: ResponseResult, frames: FrameSet, spec: RunSpec, out: Path
) -> tuple[str, ...]:
    """Write a self-describing, checksummed neutral response/readout bundle."""
    if not isinstance(spec, RunSpec) or spec.schema_version != 4:
        raise NeutralError("neutral JSON requires a schema-4 RunSpec")
    try:
        expected_frames = build_readout(response, spec)
    except ValueError as exc:
        raise NeutralError(str(exc)) from exc
    if frames != expected_frames:
        raise NeutralError("provided FrameSet differs from the response/spec readout")
    if (Path(out) / "intersections.jsonl").is_file():
        event_rows, source_hash = _intersection_records(spec, Path(out), None)
        if source_hash != response.source_sha256:
            raise NeutralError("response source hash differs from intersections.jsonl")
        _verify_outcomes(spec, response, event_rows)
    if response.provenance not in {"builtin", "external"}:
        raise NeutralError(f"unsupported response provenance {response.provenance!r}")
    acceptance_level = neutral_acceptance_level(spec, response.provenance)
    response_payload = _response_records(response)
    frame_payload = _frame_records(frames)
    metadata = {
        "schema": _neutral_schema(spec),
        "writer": "json",
        "acceptance_level": acceptance_level,
        "response_provenance": response.provenance,
        "spec_sha256": canonical_hash(spec_to_dict(spec)),
        "response_spec_sha256": response.response_spec_hash,
        "readout_spec_sha256": frames.readout_spec_hash,
        "source_sha256": response.source_sha256,
        "files": {
            "response.jsonl": hashlib.sha256(response_payload).hexdigest(),
            "frames.jsonl": hashlib.sha256(frame_payload).hexdigest(),
        },
        "spec": spec_to_dict(spec),
        "raw_settings": spec.raw_settings,
        "limitations": {
            "physics": "empirical binary response; charge transport in the sensor is not modelled",
            "external_response": (
                "external response code is not independently verified"
                if response.provenance == "external"
                else "none"
            ),
        },
    }
    metadata.update(
        {
            "transport_backend": spec.backend,
            "deposition_available": spec.deposition_available,
            "selected_writer": spec.writer,
        }
    )
    info = registry.backend_info(spec.backend)
    metadata["limitations"]["transport"] = info.neutral_transport_limitation
    if info.neutral_material_limitation is not None:
        metadata["limitations"]["material"] = info.neutral_material_limitation
    metadata_payload = _json_bytes(metadata, pretty=True)
    _atomic_bundle(
        Path(out),
        {
            "response.jsonl": response_payload,
            "frames.jsonl": frame_payload,
            "metadata.json": metadata_payload,
        },
    )

    # Parse the bytes just published and independently rebuild grouping from hits.
    try:
        stored_metadata, stored_response, stored_frames = load_neutral(spec, out)
        if (
            canonical_hash(stored_metadata) != canonical_hash(metadata)
            or stored_response != response
            or stored_frames != frames
        ):
            raise NeutralError("neutral writer readback differs from input records")
        if build_readout(stored_response, spec) != stored_frames:
            raise NeutralError("neutral writer readback frame validation failed")
        if (Path(out) / "intersections.jsonl").is_file():
            verify_neutral(spec, out)
    except Exception:
        for name in JSON_FILES:
            path = Path(out) / name
            if path.exists():
                path.unlink()
        raise
    return JSON_FILES


def _read_json(path: Path, maximum_bytes: int) -> dict:
    try:
        if path.stat().st_size > maximum_bytes:
            raise NeutralError(f"{path.name} exceeds the JSON byte limit")
        row = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except OSError as exc:
        raise NeutralError(f"cannot read {path.name}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NeutralError(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(row, dict):
        raise NeutralError(f"{path.name} must contain a JSON object")
    return row


def _read_jsonl(path: Path, maximum_line_bytes: int):
    line_count = 0
    try:
        with path.open("rb") as source:
            while raw := source.readline(maximum_line_bytes + 1):
                if len(raw) > maximum_line_bytes:
                    raise NeutralError(
                        f"{path.name} line exceeds configured byte limit"
                    )
                try:
                    row = json.loads(
                        raw,
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_constant,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise NeutralError(f"invalid JSON in {path.name}: {exc}") from exc
                if not isinstance(row, dict):
                    raise NeutralError(f"{path.name} records must be objects")
                line_count += 1
                yield row
    except OSError as exc:
        raise NeutralError(f"cannot read {path.name}: {exc}") from exc
    if not line_count:
        raise NeutralError(f"{path.name} is empty")


def _exact_keys(row: dict, expected: set[str], where: str) -> None:
    if set(row) != expected:
        unknown = sorted(set(row) - expected)
        missing = sorted(expected - set(row))
        raise NeutralError(f"{where} keys differ; unknown={unknown}, missing={missing}")


def _as_int(value: Any, where: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NeutralError(f"{where} must be an integer")
    if minimum is not None and value < minimum:
        raise NeutralError(f"{where} must be >= {minimum}")
    return value


def _as_number(value: Any, where: str, *, minimum: float | None = None) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise NeutralError(f"{where} must be finite numeric")
    if minimum is not None and value < minimum:
        raise NeutralError(f"{where} must be >= {minimum}")
    return value


def _read_response(spec: RunSpec, path: Path) -> ResponseResult:
    records = iter(_read_jsonl(path, spec.limits.line_bytes))
    meta_keys = {
        "record",
        "schema",
        "source_sha256",
        "response_spec_hash",
        "horizon_ps",
        "dark_hits",
        "provenance",
    }
    try:
        metadata = next(records)
    except StopIteration as exc:
        raise NeutralError(f"{path.name} is empty") from exc
    _exact_keys(metadata, meta_keys, "response metadata")
    if metadata.get("record") != "metadata" or metadata.get(
        "schema"
    ) != _response_schema(spec):
        raise NeutralError("unsupported response JSONL schema")
    timeline = []
    outcomes = []
    hits = []
    phase = 0
    outcome_pixel_count = 0
    trigger_keys = {
        "record",
        "ordinal",
        "simulation_event_id",
        "physical_trigger_id",
        "origin_ps",
    }
    outcome_keys = {
        "record",
        "id",
        "simulation_event_id",
        "detector",
        "track_id",
        "parent_track_id",
        "parent_unrecorded",
        "primary",
        "pdg",
        "intersection_index",
        "deposited_charge_e",
        "eligible",
        "excluded_reason",
        "accepted",
        "time_ps",
        "pixel_coordinates",
        "centroid_residual_um",
    }
    hit_keys = {
        "record",
        "id",
        "detector",
        "col",
        "row",
        "time_ps",
        "response_id",
        "noise_id",
    }
    for index, row in enumerate(records, 1):
        record = row.get("record")
        if record == "timeline":
            if phase != 0 or len(timeline) >= min(spec.events, spec.limits.events):
                raise NeutralError("timeline records are out of order or exceed budget")
            _exact_keys(row, trigger_keys, f"timeline record {index}")
            timeline.append(
                Trigger(
                    _as_int(row["ordinal"], "timeline.ordinal", minimum=0),
                    _as_int(
                        row["simulation_event_id"],
                        "timeline.simulation_event_id",
                        minimum=0,
                    ),
                    _as_int(
                        row["physical_trigger_id"],
                        "timeline.physical_trigger_id",
                        minimum=0,
                    ),
                    _as_int(row["origin_ps"], "timeline.origin_ps", minimum=0),
                )
            )
        elif record == "outcome":
            if phase >= 2 or len(outcomes) >= spec.limits.intersections:
                raise NeutralError("outcome records are out of order or exceed budget")
            phase = 1
            _exact_keys(row, outcome_keys, f"outcome record {index}")
            raw_pixels = row["pixel_coordinates"]
            if not isinstance(raw_pixels, list):
                raise NeutralError("outcome.pixel_coordinates must be an array")
            if outcome_pixel_count + len(raw_pixels) > spec.limits.pixels:
                raise NeutralError("outcome pixel coordinates exceed limits.pixels")
            outcome_pixel_count += len(raw_pixels)
            pixels = []
            for pair in raw_pixels:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise NeutralError("outcome pixel coordinate must be a pair")
                pixels.append(
                    (_as_int(pair[0], "pixel.col"), _as_int(pair[1], "pixel.row"))
                )
            raw_residual = row["centroid_residual_um"]
            residual = None
            if raw_residual is not None:
                if not isinstance(raw_residual, list) or len(raw_residual) != 2:
                    raise NeutralError("centroid_residual_um must be null or a pair")
                residual = (
                    _as_number(raw_residual[0], "centroid_residual_um[0]"),
                    _as_number(raw_residual[1], "centroid_residual_um[1]"),
                )
            if (
                not isinstance(row["detector"], str)
                or not isinstance(row["parent_unrecorded"], bool)
                or not isinstance(row["primary"], bool)
                or not isinstance(row["eligible"], bool)
                or not isinstance(row["accepted"], bool)
            ):
                raise NeutralError(
                    "outcome string and boolean fields have invalid types"
                )
            if row["excluded_reason"] is not None and not isinstance(
                row["excluded_reason"], str
            ):
                raise NeutralError("outcome.excluded_reason must be string or null")
            parent = row["parent_track_id"]
            time_ps = row["time_ps"]
            outcomes.append(
                Outcome(
                    _as_int(row["id"], "outcome.id", minimum=1),
                    _as_int(
                        row["simulation_event_id"],
                        "outcome.simulation_event_id",
                        minimum=0,
                    ),
                    row["detector"],
                    _as_int(row["track_id"], "outcome.track_id", minimum=0),
                    (
                        None
                        if parent is None
                        else _as_int(parent, "outcome.parent_track_id", minimum=0)
                    ),
                    row["parent_unrecorded"],
                    row["primary"],
                    _as_int(row["pdg"], "outcome.pdg"),
                    _as_int(
                        row["intersection_index"],
                        "outcome.intersection_index",
                        minimum=0,
                    ),
                    _charge(row["deposited_charge_e"], spec),
                    row["eligible"],
                    row["excluded_reason"],
                    row["accepted"],
                    (
                        None
                        if time_ps is None
                        else _as_int(time_ps, "outcome.time_ps", minimum=0)
                    ),
                    tuple(pixels),
                    residual,
                )
            )
        elif record == "hit":
            phase = 2
            if len(hits) >= spec.limits.pixels:
                raise NeutralError("hit records exceed limits.pixels")
            _exact_keys(row, hit_keys, f"hit record {index}")
            if not isinstance(row["id"], str) or not isinstance(row["detector"], str):
                raise NeutralError("hit ID/detector must be strings")
            response_id = row["response_id"]
            noise_id = row["noise_id"]
            if response_id is not None:
                response_id = _as_int(response_id, "hit.response_id", minimum=1)
            if noise_id is not None and not isinstance(noise_id, str):
                raise NeutralError("hit.noise_id must be a string or null")
            hits.append(
                PixelHit(
                    row["id"],
                    row["detector"],
                    _as_int(row["col"], "hit.col", minimum=0),
                    _as_int(row["row"], "hit.row", minimum=0),
                    _as_int(row["time_ps"], "hit.time_ps", minimum=0),
                    response_id,
                    noise_id,
                )
            )
        else:
            raise NeutralError(f"unknown response record type: {record!r}")
    _as_int(metadata["horizon_ps"], "response.horizon_ps", minimum=1)
    _as_int(metadata["dark_hits"], "response.dark_hits", minimum=0)
    for key in ("source_sha256", "response_spec_hash", "provenance"):
        if not isinstance(metadata[key], str):
            raise NeutralError(f"response metadata {key} must be a string")
    return ResponseResult(
        metadata["schema"],
        metadata["source_sha256"],
        metadata["response_spec_hash"],
        tuple(timeline),
        tuple(outcomes),
        tuple(hits),
        metadata["horizon_ps"],
        metadata["dark_hits"],
        metadata["provenance"],
    )


def _read_frame_set(spec: RunSpec, path: Path) -> FrameSet:
    records = iter(_read_jsonl(path, spec.limits.line_bytes))
    try:
        metadata = next(records)
    except StopIteration as exc:
        raise NeutralError(f"{path.name} is empty") from exc
    _exact_keys(
        metadata,
        {
            "record",
            "schema",
            "readout_spec_hash",
            "horizon_ps",
            "assignments",
            "contributor_links",
        },
        "frame metadata",
    )
    if metadata.get("record") != "metadata" or metadata.get("schema") != FRAME_SCHEMA:
        raise NeutralError("unsupported frames JSONL schema")
    frames = []
    total_frame_pixels = 0
    total_hit_ids = 0
    total_response_ids = 0
    total_noise_ids = 0
    total_contributors = 0
    frame_keys = {
        "record",
        "detector",
        "index",
        "model",
        "start_ps",
        "end_ps",
        "trigger_id",
        "simulation_event_id",
        "pixels",
    }
    pixel_keys = {
        "col",
        "row",
        "hit_ids",
        "response_ids",
        "noise_ids",
        "contributors",
        "noise",
    }
    contributor_keys = {"simulation_event_id", "track_id", "primary", "pdg"}
    for key in ("assignments", "contributor_links"):
        _as_int(metadata[key], f"frames.{key}", minimum=0)
    if metadata["assignments"] > spec.limits.assignments:
        raise NeutralError("frame metadata assignments exceed limits.assignments")
    if metadata["contributor_links"] > spec.limits.contributors:
        raise NeutralError(
            "frame metadata contributor_links exceed limits.contributors"
        )
    for index, row in enumerate(records):
        if len(frames) >= spec.limits.frames:
            raise NeutralError("frame records exceed limits.frames")
        _exact_keys(row, frame_keys, f"frame record {index}")
        if row.get("record") != "frame":
            raise NeutralError(f"unknown frame record type: {row.get('record')!r}")
        if (
            not isinstance(row["detector"], str)
            or not isinstance(row["model"], str)
            or not isinstance(row["pixels"], list)
        ):
            raise NeutralError("frame detector/model/pixels fields have invalid types")
        if total_frame_pixels + len(row["pixels"]) > spec.limits.assignments:
            raise NeutralError("frame pixel records exceed limits.assignments")
        total_frame_pixels += len(row["pixels"])
        pixels = []
        for pindex, pixel in enumerate(row["pixels"]):
            if not isinstance(pixel, dict):
                raise NeutralError("frame pixel must be an object")
            _exact_keys(pixel, pixel_keys, f"frame {index} pixel {pindex}")
            for key in ("hit_ids", "response_ids", "noise_ids", "contributors"):
                if not isinstance(pixel[key], list):
                    raise NeutralError(f"frame pixel {key} must be an array")
            total_hit_ids += len(pixel["hit_ids"])
            total_response_ids += len(pixel["response_ids"])
            total_noise_ids += len(pixel["noise_ids"])
            total_contributors += len(pixel["contributors"])
            if (
                total_hit_ids > spec.limits.assignments
                or total_response_ids > spec.limits.assignments
                or total_noise_ids > spec.limits.assignments
            ):
                raise NeutralError("frame pixel references exceed limits.assignments")
            if total_contributors > spec.limits.contributors:
                raise NeutralError(
                    "frame contributor records exceed limits.contributors"
                )
            if not isinstance(pixel["noise"], bool):
                raise NeutralError("frame pixel noise must be boolean")
            contributors = []
            for cindex, contributor in enumerate(pixel["contributors"]):
                if not isinstance(contributor, dict):
                    raise NeutralError("frame contributor must be an object")
                _exact_keys(contributor, contributor_keys, f"contributor {cindex}")
                if not isinstance(contributor["primary"], bool):
                    raise NeutralError("contributor.primary must be boolean")
                contributors.append(
                    Contributor(
                        _as_int(
                            contributor["simulation_event_id"],
                            "contributor.event_id",
                            minimum=0,
                        ),
                        _as_int(
                            contributor["track_id"], "contributor.track_id", minimum=0
                        ),
                        contributor["primary"],
                        _as_int(contributor["pdg"], "contributor.pdg"),
                    )
                )
            if any(not isinstance(hit_id, str) for hit_id in pixel["hit_ids"]) or any(
                not isinstance(noise_id, str) for noise_id in pixel["noise_ids"]
            ):
                raise NeutralError("frame pixel hit/noise IDs must be strings")
            pixels.append(
                FramePixel(
                    _as_int(pixel["col"], "frame pixel.col", minimum=0),
                    _as_int(pixel["row"], "frame pixel.row", minimum=0),
                    tuple(pixel["hit_ids"]),
                    tuple(
                        _as_int(v, "frame pixel.response_id", minimum=1)
                        for v in pixel["response_ids"]
                    ),
                    tuple(pixel["noise_ids"]),
                    tuple(contributors),
                    pixel["noise"],
                )
            )
        trigger_id = row["trigger_id"]
        event_id = row["simulation_event_id"]
        frames.append(
            Frame(
                row["detector"],
                _as_int(row["index"], "frame.index", minimum=0),
                row["model"],
                _as_int(row["start_ps"], "frame.start_ps", minimum=0),
                _as_int(row["end_ps"], "frame.end_ps", minimum=1),
                (
                    None
                    if trigger_id is None
                    else _as_int(trigger_id, "frame.trigger_id", minimum=0)
                ),
                (
                    None
                    if event_id is None
                    else _as_int(event_id, "frame.simulation_event_id", minimum=0)
                ),
                tuple(pixels),
            )
        )
    if not isinstance(metadata["readout_spec_hash"], str):
        raise NeutralError("frame readout_spec_hash must be a string")
    return FrameSet(
        metadata["schema"],
        metadata["readout_spec_hash"],
        _as_int(metadata["horizon_ps"], "frames.horizon_ps", minimum=1),
        tuple(frames),
        _as_int(metadata["assignments"], "frames.assignments", minimum=0),
        _as_int(metadata["contributor_links"], "frames.contributor_links", minimum=0),
    )


def load_neutral(spec: RunSpec, out: Path) -> tuple[dict, ResponseResult, FrameSet]:
    """Load and strictly reconstruct the typed records from a neutral bundle."""
    out = Path(out)
    metadata = _read_json(out / "metadata.json", spec.limits.line_bytes)
    expected_keys = {
        "schema",
        "writer",
        "acceptance_level",
        "response_provenance",
        "spec_sha256",
        "response_spec_sha256",
        "readout_spec_sha256",
        "source_sha256",
        "files",
        "spec",
        "raw_settings",
        "limitations",
    }
    expected_keys.update(
        {"transport_backend", "deposition_available", "selected_writer"}
    )
    _exact_keys(metadata, expected_keys, "metadata.json")
    if (
        metadata["transport_backend"] != spec.backend
        or metadata["deposition_available"] is not spec.deposition_available
        or metadata["selected_writer"] != spec.writer
    ):
        raise NeutralError(
            "neutral transport/deposition/writer capabilities differ from validated spec"
        )
    if metadata["schema"] != _neutral_schema(spec) or metadata["writer"] != "json":
        raise NeutralError("unsupported neutral metadata schema/writer")
    if canonical_hash(metadata["spec"]) != canonical_hash(
        spec_to_dict(spec)
    ) or metadata["spec_sha256"] != canonical_hash(spec_to_dict(spec)):
        raise NeutralError("neutral spec snapshot does not match requested spec")
    if canonical_hash(metadata["raw_settings"]) != canonical_hash(spec.raw_settings):
        raise NeutralError(
            "neutral raw settings snapshot does not match requested spec"
        )
    if not isinstance(metadata["files"], dict) or set(metadata["files"]) != {
        "response.jsonl",
        "frames.jsonl",
    }:
        raise NeutralError(
            "neutral metadata must declare response.jsonl and frames.jsonl"
        )
    for name, expected_hash in metadata["files"].items():
        if (
            not isinstance(expected_hash, str)
            or file_sha256(out / name) != expected_hash
        ):
            raise NeutralError(f"neutral checksum mismatch for {name}")
    response = _read_response(spec, out / "response.jsonl")
    frames = _read_frame_set(spec, out / "frames.jsonl")
    if metadata["source_sha256"] != response.source_sha256:
        raise NeutralError("metadata and response source hashes differ")
    if metadata["response_spec_sha256"] != response.response_spec_hash:
        raise NeutralError("metadata and response spec hashes differ")
    if metadata["readout_spec_sha256"] != frames.readout_spec_hash:
        raise NeutralError("metadata and readout spec hashes differ")
    if metadata["response_provenance"] != response.provenance:
        raise NeutralError("metadata and response provenance differ")
    expected_limitations = {"physics", "external_response", "transport"}
    if registry.backend_info(spec.backend).neutral_material_limitation is not None:
        expected_limitations.add("material")
    if (
        not isinstance(metadata["limitations"], dict)
        or set(metadata["limitations"]) != expected_limitations
        or any(not isinstance(value, str) for value in metadata["limitations"].values())
    ):
        raise NeutralError("neutral limitations metadata has invalid shape")
    expected_level = neutral_acceptance_level(spec, response.provenance)
    if metadata["acceptance_level"] != expected_level:
        raise NeutralError("neutral acceptance level misstates response provenance")
    return metadata, response, frames


def _intersection_records(spec: RunSpec, out: Path, events) -> tuple[list[dict], str | None]:
    source_path = Path(out) / "intersections.jsonl"
    source_hash = file_sha256(source_path) if source_path.is_file() else None
    if events is None:
        if not source_path.is_file():
            raise NeutralError("verify requires events or intersections.jsonl")
        try:
            rows = load_events(
                source_path,
                spec.detectors,
                limits=spec.limits,
                event_limit=spec.events,
                deposition_available=spec.deposition_available,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise NeutralError(f"invalid intersection records: {exc}") from exc
    else:
        if not isinstance(events, (list, tuple)):
            raise NeutralError("events must be a list or tuple")
        rows = []
        seen = set()
        count = 0
        for event in events:
            try:
                row = validate_event(
                    event,
                    [d.id for d in spec.detectors],
                    deposition_available=spec.deposition_available,
                )
            except EventValidationError as exc:
                raise NeutralError(f"invalid event: {exc}") from exc
            if row["simulation_event_id"] in seen:
                raise NeutralError("duplicate simulation_event_id")
            seen.add(row["simulation_event_id"])
            count += sum(len(plane["mc"]) for plane in row["planes"].values())
            if count > spec.limits.intersections or len(rows) >= spec.events:
                raise NeutralError("events exceed configured budgets")
            rows.append(row)
    if len(rows) != spec.events:
        raise NeutralError("event count differs from spec.events")
    return rows, source_hash


def _verify_outcomes(
    spec: RunSpec, response: ResponseResult, events: list[dict]
) -> None:
    detectors = {detector.id: detector for detector in spec.detectors}
    try:
        expected_timeline = schedule(events, spec)
    except ValueError as exc:
        raise NeutralError(f"cannot recreate event timeline: {exc}") from exc
    if response.timeline != expected_timeline:
        raise NeutralError(
            "stored response timeline differs from deterministic schedule"
        )
    if len(response.outcomes) > spec.limits.intersections:
        raise NeutralError("response outcome count exceeds limits.intersections")
    registry = ResponseRegistry()
    compiled = {}
    if response.provenance == "builtin":
        for detector in spec.detectors:
            try:
                compiled[detector.id] = registry.compile(detector)
            except ValueError as exc:
                raise NeutralError(
                    f"cannot validate built-in response model: {exc}"
                ) from exc
    elif response.provenance != "external":
        raise NeutralError(f"unknown response provenance {response.provenance!r}")
    trigger_by_event = {
        trigger.simulation_event_id: trigger for trigger in response.timeline
    }

    expected_count = 0
    outcome_index = 0
    for event in events:
        tracks = {track["track_id"]: track for track in event["tracks"]}
        for detector_id, plane in event["planes"].items():
            detector = detectors[detector_id]
            for intersection_index, mc in enumerate(plane["mc"]):
                expected_count += 1
                if outcome_index >= len(response.outcomes):
                    raise NeutralError(
                        "missing response outcome for intersection"
                    )
                outcome = response.outcomes[outcome_index]
                track = tracks[mc["track_id"]]
                exact = (
                    outcome.id == outcome_index + 1
                    and outcome.simulation_event_id == event["simulation_event_id"]
                    and outcome.detector == detector_id
                    and outcome.track_id == mc["track_id"]
                    and outcome.parent_track_id == track["parent_track_id"]
                    and outcome.parent_unrecorded == track["parent_unrecorded"]
                    and outcome.primary == track["primary"]
                    and outcome.pdg == mc["pdg"]
                    and outcome.intersection_index == intersection_index
                    and type(outcome.deposited_charge_e) is type(mc["deposited_e"])
                    and outcome.deposited_charge_e == mc["deposited_e"]
                )
                if not exact:
                    raise NeutralError(
                        "response outcome identity/truth differs from the input"
                    )
                reason = None
                if abs(mc["pdg"]) in NEUTRAL_PDG:
                    reason = "neutral_or_unmeasured_pdg"
                elif mc["deposited_e"] is not None and mc["deposited_e"] <= 0:
                    reason = "nonpositive_deposit"
                model_result = None
                if reason is None and response.provenance == "builtin":
                    model_result = compiled[detector_id](detector, mc["local_mm"])
                    if model_result is None:
                        reason = "outside_matrix"
                if reason is not None:
                    if (
                        outcome.eligible
                        or outcome.accepted
                        or outcome.excluded_reason != reason
                        or outcome.time_ps is not None
                    ):
                        raise NeutralError(
                            "response eligibility/exclusion differs from the input"
                        )
                elif not outcome.eligible or outcome.excluded_reason is not None:
                    if (
                        response.provenance != "external"
                        or outcome.excluded_reason != "outside_matrix"
                    ):
                        raise NeutralError(
                            "eligible intersection has invalid response exclusion"
                        )
                if outcome.accepted and model_result is not None:
                    pixels, residual = model_result
                    if (
                        outcome.pixel_coordinates != pixels
                        or outcome.centroid_residual_um != residual
                    ):
                        raise NeutralError(
                            "accepted built-in response pattern differs from kernel geometry"
                        )
                if outcome.eligible and detector.response.jitter_ns == 0:
                    expected_time_ps = max(
                        0,
                        trigger_by_event[event["simulation_event_id"]].origin_ps
                        + round(
                            (mc["global_time_ns"] + detector.response.response_delay_ns)
                            * 1000
                        ),
                    )
                    if outcome.time_ps != expected_time_ps:
                        raise NeutralError(
                            "zero-jitter response time differs from input time/delay"
                        )
                if not outcome.eligible and outcome.accepted:
                    raise NeutralError("ineligible response outcome cannot be accepted")
                outcome_index += 1
    if outcome_index != len(response.outcomes) or expected_count != len(
        response.outcomes
    ):
        raise NeutralError(
            "response outcomes do not cover intersections exactly once"
        )


def verify_neutral(spec: RunSpec, out: Path, events=None) -> dict[str, Any]:
    """Verify checksums and relational truth without rerunning response RNG."""
    if not isinstance(spec, RunSpec) or spec.schema_version != 4:
        raise NeutralError("neutral verification requires a schema-4 RunSpec")
    metadata, response, frames = load_neutral(spec, out)
    event_rows, source_hash = _intersection_records(spec, out, events)
    if source_hash is not None and response.source_sha256 != source_hash:
        raise NeutralError("response source hash differs from intersections.jsonl")
    _verify_outcomes(spec, response, event_rows)
    try:
        expected_frames = build_readout(response, spec)
    except ValueError as exc:
        raise NeutralError(
            f"stored response/readout relationship is invalid: {exc}"
        ) from exc
    if expected_frames != frames:
        raise NeutralError("stored frames differ from regrouped response hits")
    return {
        "valid": True,
        "writer": "json",
        "acceptance_level": metadata["acceptance_level"],
        "response_provenance": response.provenance,
        "events": len(event_rows),
        "intersections": len(response.outcomes),
        "hits": len(response.hits),
        "frames": len(frames.frames),
        "horizon_ps": response.horizon_ps,
        "source_verified": source_hash is not None,
        "source_sha256": response.source_sha256,
        "metadata_sha256": file_sha256(Path(out) / "metadata.json"),
    }
