"""Deterministic Gaussian-core primary-electron transport backend."""

from __future__ import annotations

import hashlib
from itertools import zip_longest
import json
import math
import random
from pathlib import Path

from .beam import (
    AIR_X0_MM,
    MAX_POLAR_RAD,
    ELECTRON_MASS_GEV,
    SILICON_X0_MM,
    covariance_factor,
    electron_kinematics,
    material_reference,
)
from .events import validate_event
from .geometry import global_to_local
from .provenance import file_hash, python_identity, source_hashes, write_json
from .runtime import run_path
from .specs import (
    RunSpec,
    canonical_hash,
    normalize_config,
    spec_to_dict,
    transport_fingerprint,
    validate_capabilities,
    geometry_rows,
)
from .scattering import air_step, highland_coefficient

ALGORITHM = "gaussian-primary-v1"
TRANSPORT_SCHEMA = "tracksim.transport.v2"
CROSSING_SCHEMA = "tracksim.crossings.v2"
TRACE_SCHEMA = "tracksim.fast-trace.v1"
FAST_SCOPE = (
    "Gaussian primary electron core; no deposition, bremsstrahlung, or secondaries"
)
SPEED_OF_LIGHT_MM_PER_NS = 299.792458
MAX_AIR_SUBSTEPS = 64
MAX_SURFACES_PER_EVENT = 64 + 128
MAX_TRANSPORT_EVENTS = 1_000_000

_REQUIRED_FILES = (
    "settings.json",
    "geometry.json",
    "normalized-spec.json",
    "source-snapshot.json",
    "python-runtime.json",
    "runtime.json",
    "intersections.jsonl",
    "fast-trace.jsonl",
    "transport-envelope.json",
)
_INPUT_FILES = ("settings.json", "geometry.json", "normalized-spec.json")


def _json_line(value: dict) -> bytes:
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _require_fast_spec(spec: RunSpec) -> RunSpec:
    if not isinstance(spec, RunSpec) or spec.schema_version != 4:
        raise ValueError("fast electron transport requires a schema-4 RunSpec")
    validate_capabilities(spec, full_pipeline=False)
    if spec.backend != "fast_electron" or not spec.transport:
        raise ValueError("fast electron transport requires backend='fast_electron'")
    if spec.transport.algorithm != ALGORITHM:
        raise ValueError("unsupported fast electron transport algorithm")
    canonical = normalize_config(spec.raw_settings)
    if canonical_hash(spec_to_dict(canonical)) != canonical_hash(spec_to_dict(spec)):
        raise ValueError("RunSpec must match its validated input configuration")
    return spec


def _check_polar(tx: float, ty: float) -> None:
    angle = math.atan(math.hypot(tx, ty))
    if not math.isfinite(angle) or angle >= MAX_POLAR_RAD:
        raise ValueError("generated electron slope is outside the 0.05 rad domain")


def _check_state(state: tuple[float, float, float, float], z_mm: float) -> None:
    if (
        not math.isfinite(z_mm)
        or max(abs(value) for value in (*state[:2], z_mm)) > 10_000
    ):
        raise ValueError("generated electron position is outside the 10 m domain")
    if any(not math.isfinite(value) for value in state):
        raise ValueError("generated electron state is not finite")
    _check_polar(state[2], state[3])


def _surface_rows(spec: RunSpec):
    detector_rows = [
        (detector.position_mm[2], index, detector)
        for index, detector in enumerate(spec.detectors)
    ]
    extra_rows = [
        (layer.z_mm, index, layer)
        for index, layer in enumerate(spec.transport.extra_layers)
    ]
    rows = [(z, "detector", index, row) for z, index, row in detector_rows]
    rows.extend((z, "extra", index, row) for z, index, row in extra_rows)
    return sorted(rows, key=lambda row: row[0])


def _inside_matrix(detector, local_mm) -> bool:
    x, y = local_mm[:2]
    px, py = detector.pitch_um
    half_x, half_y = px / 2000.0, py / 2000.0
    max_x = (detector.pixels[0] - 0.5) * px / 1000.0
    max_y = (detector.pixels[1] - 0.5) * py / 1000.0
    return -half_x <= x < max_x and -half_y <= y < max_y


def _finite_values(value, length: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"fast {label} must contain {length} values")
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(item)
        for item in value
    ):
        raise ValueError(f"fast {label} must contain finite numbers")
    return tuple(float(item) for item in value)


def _require_close(actual: float, expected: float, label: str, *, position=False):
    if not math.isfinite(actual) or not math.isfinite(expected):
        raise ValueError(f"fast {label} must be finite")
    if position:
        if abs(actual - expected) > 1e-9:
            raise ValueError(f"fast {label} coordinate mismatch")
    elif not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"fast {label} mismatch")


def _validate_event_trace(spec: RunSpec, event: dict, trace: dict, event_id: int):
    try:
        validate_event(
            event,
            [detector.id for detector in spec.detectors],
            deposition_available=False,
        )
    except ValueError as exc:
        raise ValueError(f"fast crossing event contract failed: {exc}") from exc
    expected_trace_keys = {
        "schema",
        "simulation_event_id",
        "source_state",
        "beta",
        "prefactor",
        "surfaces",
    }
    if not isinstance(trace, dict) or set(trace) != expected_trace_keys:
        raise ValueError("fast trace record has an invalid schema")
    if (
        trace.get("schema") != TRACE_SCHEMA
        or trace.get("simulation_event_id") != event_id
    ):
        raise ValueError("fast trace schema/event ID mismatch")

    source = _finite_values(trace.get("source_state"), 5, "source_state")
    x, y, tx, ty, momentum = source
    _check_state((x, y, tx, ty), spec.beam.position_mm[2])
    if momentum <= 0:
        raise ValueError("fast trace source momentum is nonpositive")
    beta = momentum / math.hypot(momentum, ELECTRON_MASS_GEV)
    trace_beta = trace.get("beta")
    prefactor = trace.get("prefactor")
    for value, label in ((trace_beta, "beta"), (prefactor, "prefactor")):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"fast trace {label} must be finite")
    _require_close(float(trace_beta), beta, "trace beta")
    material = material_reference(
        spec.detectors,
        spec.beam.position_mm[2],
        spec.transport.air,
        spec.transport.extra_layers,
    )
    expected_prefactor = highland_coefficient(
        momentum,
        beta,
        material["reference_budget"],
        spec.transport.scattering,
    )
    _require_close(float(prefactor), expected_prefactor, "trace scattering prefactor")

    expected_rows = _surface_rows(spec)
    surfaces = trace.get("surfaces")
    if not isinstance(surfaces, list) or len(surfaces) != len(expected_rows):
        raise ValueError(
            "fast trace surface count differs from the normalized geometry"
        )
    plane_ids = [detector.id for detector in spec.detectors]
    if list(event.get("planes", {})) != plane_ids:
        raise ValueError("fast crossing planes differ from declared detector order")

    previous_state = (x, y, tx, ty)
    previous_z = spec.beam.position_mm[2]
    previous_time = 0.0
    surface_keys = {
        "key",
        "z_mm",
        "pre_state",
        "air_delta",
        "air_budget",
        "assigned_sheet_budget",
        "sheet_delta",
        "crossing",
    }
    for surface, (z_mm, kind, index, item) in zip(surfaces, expected_rows):
        if not isinstance(surface, dict) or set(surface) != surface_keys:
            raise ValueError("fast trace surface has an invalid schema")
        expected_key = f"detector:{item.id}" if kind == "detector" else f"extra:{index}"
        if surface.get("key") != expected_key:
            raise ValueError(
                "fast trace surface key differs from the normalized geometry"
            )
        if (
            isinstance(surface.get("z_mm"), bool)
            or not isinstance(surface.get("z_mm"), (int, float))
            or not math.isfinite(surface["z_mm"])
        ):
            raise ValueError("fast trace surface z must be finite")
        _require_close(float(surface["z_mm"]), z_mm, "trace surface z")
        pre = _finite_values(surface.get("pre_state"), 5, "pre_state")
        pre_state = pre[:4]
        pre_time = pre[4]
        _check_state(pre_state, z_mm)
        if pre_time < previous_time:
            raise ValueError("fast trace time is not monotonic")

        air_delta = _finite_values(surface.get("air_delta"), 4, "air_delta")
        air_budget = surface.get("air_budget")
        assigned_budget = surface.get("assigned_sheet_budget")
        for value, label in (
            (air_budget, "air budget"),
            (assigned_budget, "sheet budget"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"fast trace {label} must be finite and nonnegative")
        sheet_delta = _finite_values(surface.get("sheet_delta"), 2, "sheet_delta")
        if not isinstance(surface.get("crossing"), bool):
            raise ValueError("fast trace crossing must be boolean")

        length = z_mm - previous_z
        if length <= 0:
            raise ValueError("fast trace surfaces are not strictly downstream")
        incoming_tx, incoming_ty = previous_state[2:]
        path_factor = math.sqrt(
            1.0 + incoming_tx * incoming_tx + incoming_ty * incoming_ty
        )
        expected_air_budget = (
            length * path_factor / AIR_X0_MM if spec.transport.air else 0.0
        )
        _require_close(float(air_budget), expected_air_budget, "air budget")
        expected_time = previous_time + length * path_factor / (
            beta * SPEED_OF_LIGHT_MM_PER_NS
        )
        _require_close(pre_time, expected_time, "trace flight time")
        for index, (actual, base, delta) in enumerate(
            zip(pre_state, previous_state, air_delta)
        ):
            _require_close(
                actual,
                base + delta,
                "air state increment",
                position=index < 2,
            )
        if not spec.transport.air or float(prefactor) == 0:
            expected_delta = (
                incoming_tx * length,
                incoming_ty * length,
                0.0,
                0.0,
            )
            for index, (actual, expected) in enumerate(zip(air_delta, expected_delta)):
                _require_close(actual, expected, "air propagation", position=index < 2)

        crossing = True
        expected_sheet_budget = 0.0
        if kind == "detector":
            detector = item
            local = global_to_local(detector, [pre_state[0], pre_state[1], z_mm])
            crossing = _inside_matrix(detector, local)
            if crossing:
                expected_sheet_budget = (
                    (detector.active_um + detector.passive_um)
                    / 1000.0
                    / SILICON_X0_MM
                    * math.sqrt(1.0 + pre_state[2] ** 2 + pre_state[3] ** 2)
                )
            crossings = event["planes"][detector.id]["mc"]
            if len(crossings) != int(crossing):
                raise ValueError("fast crossing presence differs from sensor footprint")
            if crossing:
                mc = crossings[0]
                for actual, expected in zip(
                    mc["global_mid_mm"], (pre_state[0], pre_state[1], z_mm)
                ):
                    _require_close(
                        float(actual), expected, "crossing global", position=True
                    )
                for actual, expected in zip(mc["local_mm"], local):
                    _require_close(
                        float(actual), expected, "crossing local", position=True
                    )
                _require_close(float(mc["global_time_ns"]), pre_time, "crossing time")
                if (
                    mc.get("track_id") != 0
                    or mc.get("pdg") != 11
                    or mc.get("deposited_e") is not None
                ):
                    raise ValueError(
                        "fast crossing identity/deposition differs from capability"
                    )
        else:
            expected_sheet_budget = item.material_budget * math.sqrt(
                1.0 + pre_state[2] ** 2 + pre_state[3] ** 2
            )
        if surface["crossing"] is not crossing:
            raise ValueError("fast trace crossing flag differs from sensor footprint")
        _require_close(float(assigned_budget), expected_sheet_budget, "sheet budget")
        if expected_sheet_budget == 0 or float(prefactor) == 0:
            if sheet_delta != (0.0, 0.0):
                raise ValueError(
                    "fast zero-material/scattering sheet has a nonzero kick"
                )

        post_state = (
            pre_state[0],
            pre_state[1],
            pre_state[2] + sheet_delta[0],
            pre_state[3] + sheet_delta[1],
        )
        _check_state(post_state, z_mm)
        previous_state = post_state
        previous_z = z_mm
        previous_time = pre_time


def _apply_sheet(tx: float, ty: float, budget: float, prefactor: float, rng):
    if budget <= 0 or prefactor <= 0:
        return (tx, ty), (0.0, 0.0)
    variance = prefactor * budget
    if not math.isfinite(variance) or variance < 0:
        raise ValueError("sheet scattering variance is invalid")
    root = math.sqrt(variance)
    dtx = root * rng.gauss(0.0, 1.0)
    dty = root * rng.gauss(0.0, 1.0)
    return (tx + dtx, ty + dty), (dtx, dty)


def _propagate_event(spec: RunSpec, event_id: int, air_substeps: int):
    beam = spec.beam
    transport = spec.transport
    rng = random.Random(f"fast_electron:v1:{spec.seed}:{event_id}")
    factor = covariance_factor(transport.phase_space_covariance)
    standard = [rng.gauss(0.0, 1.0) for _ in range(5)]
    mean = (
        beam.position_mm[0],
        beam.position_mm[1],
        beam.direction[0] / beam.direction[2],
        beam.direction[1] / beam.direction[2],
        0.0,
    )
    sampled = tuple(
        mean[i] + sum(factor[i][j] * standard[j] for j in range(5)) for i in range(5)
    )
    x, y, tx, ty, delta_p = sampled
    nominal_momentum, _ = electron_kinematics(beam.kinetic_energy_gev)
    momentum = nominal_momentum * (1.0 + delta_p)
    if not math.isfinite(momentum) or momentum <= 0:
        raise ValueError("generated electron momentum is nonpositive or non-finite")
    beta = momentum / math.hypot(momentum, ELECTRON_MASS_GEV)
    _check_state((x, y, tx, ty), beam.position_mm[2])

    material = material_reference(
        spec.detectors, beam.position_mm[2], transport.air, transport.extra_layers
    )
    prefactor = highland_coefficient(
        momentum, beta, material["reference_budget"], transport.scattering
    )
    state = (x, y, tx, ty)
    time_ns = 0.0
    current_z = beam.position_mm[2]
    surfaces = []
    planes = {detector.id: {"mc": []} for detector in spec.detectors}
    surface_rows = _surface_rows(spec)
    if len(surface_rows) > MAX_SURFACES_PER_EVENT:
        raise ValueError(
            "fast transport surface count exceeds 64 detectors + 128 layers"
        )

    for z_mm, kind, index, item in surface_rows:
        length = z_mm - current_z
        if not math.isfinite(length) or length <= 0:
            raise ValueError("fast transport surfaces must be strictly downstream")
        dx = dy = dtx = dty = air_budget = 0.0
        sublength = length / air_substeps
        for _ in range(air_substeps):
            incoming_tx, incoming_ty = state[2], state[3]
            step_state, step_delta, step_budget = air_step(
                *state,
                sublength,
                prefactor,
                transport.air,
                rng,
            )
            time_ns += (
                sublength
                * math.sqrt(1.0 + incoming_tx * incoming_tx + incoming_ty * incoming_ty)
                / (beta * SPEED_OF_LIGHT_MM_PER_NS)
            )
            dx += step_delta[0]
            dy += step_delta[1]
            dtx += step_delta[2]
            dty += step_delta[3]
            air_budget += step_budget
            state = step_state
            _check_state(state, current_z + sublength)
            current_z += sublength

        pre_state = [*state, time_ns]
        assigned_sheet_budget = 0.0
        sheet_delta = (0.0, 0.0)
        crossing = True
        if kind == "detector":
            detector = item
            global_mid = [state[0], state[1], z_mm]
            local = global_to_local(detector, global_mid)
            crossing = _inside_matrix(detector, local)
            if crossing:
                assigned_sheet_budget = (
                    (detector.active_um + detector.passive_um)
                    / 1000.0
                    / SILICON_X0_MM
                    * math.sqrt(1.0 + state[2] * state[2] + state[3] * state[3])
                )
                planes[detector.id]["mc"].append(
                    {
                        "track_id": 0,
                        "primary": True,
                        "pdg": 11,
                        "local_mm": list(local),
                        "global_mid_mm": global_mid,
                        "deposited_e": None,
                        "global_time_ns": time_ns,
                    }
                )
        else:
            assigned_sheet_budget = item.material_budget * math.sqrt(
                1.0 + state[2] * state[2] + state[3] * state[3]
            )

        check_budget = prefactor * assigned_sheet_budget
        if not math.isfinite(assigned_sheet_budget) or not math.isfinite(check_budget):
            raise ValueError("assigned sheet material or variance is not finite")
        (new_tx, new_ty), sheet_delta = _apply_sheet(
            state[2], state[3], assigned_sheet_budget, prefactor, rng
        )
        state = (state[0], state[1], new_tx, new_ty)
        _check_state(state, z_mm)
        surfaces.append(
            {
                "key": (
                    f"detector:{item.id}" if kind == "detector" else f"extra:{index}"
                ),
                "z_mm": z_mm,
                "pre_state": pre_state,
                "air_delta": [dx, dy, dtx, dty],
                "air_budget": air_budget,
                "assigned_sheet_budget": assigned_sheet_budget,
                "sheet_delta": list(sheet_delta),
                "crossing": crossing,
            }
        )

    event = {
        "schema": CROSSING_SCHEMA,
        "transport": "fast_electron",
        "simulation_event_id": event_id,
        "tracks": [
            {
                "track_id": 0,
                "parent_track_id": None,
                "parent_unrecorded": False,
                "primary": True,
                "pdg": 11,
            }
        ],
        "planes": planes,
    }
    trace = {
        "schema": TRACE_SCHEMA,
        "simulation_event_id": event_id,
        "source_state": [x, y, tx, ty, momentum],
        "beta": beta,
        "prefactor": prefactor,
        "surfaces": surfaces,
    }
    return event, trace


def propagate_event(spec: RunSpec, event_id: int, *, air_substeps: int = 1):
    """Generate one validated common crossing event and compact transport trace."""
    _require_fast_spec(spec)
    if isinstance(event_id, bool) or not isinstance(event_id, int):
        raise ValueError("event_id must be an integer")
    if not 0 <= event_id < spec.events:
        raise ValueError("event_id is outside the configured event range")
    if isinstance(air_substeps, bool) or not isinstance(air_substeps, int):
        raise ValueError("air_substeps must be an integer")
    if not 1 <= air_substeps <= MAX_AIR_SUBSTEPS:
        raise ValueError("air_substeps must be in [1, 64]")
    return _propagate_event(spec, event_id, air_substeps)


def iter_transport(
    spec: RunSpec,
    *,
    air_substeps: int = 1,
    start: int = 0,
    stop: int | None = None,
):
    """Yield ``(event, trace)`` records in simulation-event order.

    ``start`` and ``stop`` select the half-open event range ``[start, stop)``;
    ``stop=None`` means ``spec.events``. Every event is generated independently,
    so slices concatenate to the full stream. Arguments are validated when the
    function is called, before the first record is produced.
    """
    _require_fast_spec(spec)
    if isinstance(air_substeps, bool) or not isinstance(air_substeps, int):
        raise ValueError("air_substeps must be an integer")
    if not 1 <= air_substeps <= MAX_AIR_SUBSTEPS:
        raise ValueError("air_substeps must be in [1, 64]")
    if stop is None:
        stop = spec.events
    for name, value in (("start", start), ("stop", stop)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if not 0 <= start <= stop <= spec.events:
        raise ValueError(
            f"event range must satisfy 0 <= start <= stop <= {spec.events}"
        )
    return (
        _propagate_event(spec, event_id, air_substeps)
        for event_id in range(start, stop)
    )


def _iter_jsonl(path: Path, line_limit: int, max_rows: int, label: str):
    count = 0
    with path.open("rb") as source:
        while raw := source.readline(line_limit + 1):
            if len(raw) > line_limit:
                raise ValueError(f"{label} line exceeds limits.line_bytes")
            if count >= max_rows:
                raise ValueError(f"{label} exceeds the expected event count")
            if not raw.endswith(b"\n"):
                raise ValueError(f"{label} contains an incomplete JSONL line")
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError(f"{label} rows must be JSON objects")
            count += 1
            yield raw, row


class FastElectronBackend:
    name = "fast_electron"

    def required_files(self, spec: RunSpec) -> tuple[str, ...]:
        _require_fast_spec(spec)
        return _REQUIRED_FILES

    def runtime_identity(self, out: Path) -> dict:
        out = run_path(out)
        runtime = json.loads((out / "runtime.json").read_text())
        if set(runtime) != {"backend", "algorithm", "python"}:
            raise ValueError("fast runtime identity has an invalid schema")
        if runtime.get("backend") != self.name or runtime.get("algorithm") != ALGORITHM:
            raise ValueError("fast runtime identity names a different transport")
        return runtime

    def validate_runtime_record(
        self, spec: RunSpec, record: object, python: dict
    ) -> None:
        """Bind a stored runtime record to this backend and the recorded Python."""
        if not isinstance(record, dict):
            raise ValueError("transport runtime identity must be an object")
        if (
            set(record) != {"backend", "algorithm", "python"}
            or record.get("backend") != "fast_electron"
            or record.get("algorithm") != spec.transport.algorithm
            or record.get("python") != python
        ):
            raise ValueError("fast transport runtime identity is invalid")

    def preflight_identity(self, spec: RunSpec) -> dict:
        """Identity fields printed by the preflight command."""
        return {"algorithm": ALGORITHM}

    def simulate(self, spec: RunSpec, out: Path) -> Path:
        _require_fast_spec(spec)
        if spec.events > MAX_TRANSPORT_EVENTS:
            raise ValueError(
                f"fast transport events exceed the {MAX_TRANSPORT_EVENTS} stage limit"
            )
        out = run_path(out)
        if out.exists():
            raise FileExistsError(f"run output already exists: {out}")
        out.mkdir(parents=True, exist_ok=False)
        pending: list[Path] = []
        published: list[Path] = []
        try:
            write_json(out / "run-state.json", {"status": "simulating"})
            started_sources = source_hashes()
            started_python = python_identity()
            write_json(out / "source-snapshot.json", started_sources)
            write_json(out / "python-runtime.json", started_python)
            write_json(
                out / "runtime.json",
                {
                    "backend": self.name,
                    "algorithm": ALGORITHM,
                    "python": started_python,
                },
            )
            write_json(out / "settings.json", spec.raw_settings)
            write_json(out / "geometry.json", geometry_rows(spec))
            write_json(out / "normalized-spec.json", spec_to_dict(spec))

            intersections_pending = out / "intersections.jsonl.pending"
            trace_pending = out / "fast-trace.jsonl.pending"
            pending.extend((intersections_pending, trace_pending))
            intersection_count = 0
            surface_count = 0
            with (
                intersections_pending.open("xb") as intersections,
                trace_pending.open("xb") as traces,
            ):
                for event, trace in iter_transport(spec):
                    event_count = sum(
                        len(plane["mc"]) for plane in event["planes"].values()
                    )
                    if intersection_count + event_count > spec.limits.intersections:
                        raise ValueError(
                            "fast intersections exceed limits.intersections"
                        )
                    if len(trace["surfaces"]) > MAX_SURFACES_PER_EVENT:
                        raise ValueError("fast surface count exceeds supported bound")
                    event_line = _json_line(event)
                    trace_line = _json_line(trace)
                    if (
                        len(event_line) > spec.limits.line_bytes
                        or len(trace_line) > spec.limits.line_bytes
                    ):
                        raise ValueError(
                            "fast transport JSONL line exceeds limits.line_bytes"
                        )
                    intersections.write(event_line)
                    traces.write(trace_line)
                    intersection_count += event_count
                    surface_count += len(trace["surfaces"])

            if source_hashes() != started_sources:
                raise RuntimeError("source changed during fast transport")
            if python_identity() != started_python:
                raise RuntimeError("Python runtime changed during fast transport")
            intersections_path = out / "intersections.jsonl"
            trace_path = out / "fast-trace.jsonl"
            intersections_pending.replace(intersections_path)
            pending.remove(intersections_pending)
            published.append(intersections_path)
            trace_pending.replace(trace_path)
            pending.remove(trace_pending)
            published.append(trace_path)
            material = material_reference(
                spec.detectors,
                spec.beam.position_mm[2],
                spec.transport.air,
                spec.transport.extra_layers,
            )
            envelope = {
                "schema": TRANSPORT_SCHEMA,
                "backend": self.name,
                "algorithm": ALGORITHM,
                "spec_version": 4,
                "deposition_available": False,
                "transport_spec_hash": transport_fingerprint(spec),
                "normalized_spec_sha256": file_hash(out / "normalized-spec.json"),
                "parsing_limits": {
                    "events": spec.events,
                    "intersections": spec.limits.intersections,
                    "line_bytes": spec.limits.line_bytes,
                    "surfaces_per_event": MAX_SURFACES_PER_EVENT,
                },
                "detector_ids": [detector.id for detector in spec.detectors],
                "input_hashes": {name: file_hash(out / name) for name in _INPUT_FILES},
                "intersections_sha256": file_hash(intersections_path),
                "trace_sha256": file_hash(trace_path),
                "runtime": json.loads((out / "runtime.json").read_text()),
                "python": started_python,
                "material_reference": material,
                "report": {
                    "events": spec.events,
                    "intersections": intersection_count,
                    "surfaces": surface_count,
                },
                "scope": FAST_SCOPE,
            }
            if source_hashes() != started_sources:
                raise RuntimeError("source changed before fast envelope publication")
            if python_identity() != started_python:
                raise RuntimeError(
                    "Python runtime changed before fast envelope publication"
                )
            write_json(out / "transport-envelope.json", envelope)
            write_json(
                out / "run-state.json",
                {"status": "transport-complete", "events": spec.events},
            )
        except Exception as exc:
            for path in pending + published:
                path.unlink(missing_ok=True)
            (out / "transport-envelope.json").unlink(missing_ok=True)
            write_json(out / "run-state.json", {"status": "failed", "error": str(exc)})
            raise
        return out

    def verify(self, out: Path, spec: RunSpec) -> dict:
        """Validate provenance and replay transport without touching the run."""
        out = run_path(out)
        caller_spec = _require_fast_spec(spec)
        settings = json.loads((out / "settings.json").read_text())
        stored_spec = _require_fast_spec(normalize_config(settings))
        stored_normalized = json.loads((out / "normalized-spec.json").read_text())
        if canonical_hash(stored_normalized) != canonical_hash(
            spec_to_dict(stored_spec)
        ):
            raise ValueError("stored normalized spec differs from settings.json")
        if canonical_hash(
            json.loads((out / "geometry.json").read_text())
        ) != canonical_hash(geometry_rows(stored_spec)):
            raise ValueError("stored geometry differs from settings.json")
        if transport_fingerprint(caller_spec) != transport_fingerprint(stored_spec):
            raise ValueError("fast transport fingerprint differs from requested spec")

        envelope = json.loads((out / "transport-envelope.json").read_text())
        required_keys = {
            "schema",
            "backend",
            "algorithm",
            "spec_version",
            "deposition_available",
            "transport_spec_hash",
            "normalized_spec_sha256",
            "parsing_limits",
            "detector_ids",
            "input_hashes",
            "intersections_sha256",
            "trace_sha256",
            "runtime",
            "python",
            "material_reference",
            "report",
            "scope",
        }
        if not isinstance(envelope, dict) or set(envelope) != required_keys:
            raise ValueError("fast transport envelope has an invalid schema")
        if (
            envelope.get("schema") != TRANSPORT_SCHEMA
            or envelope.get("backend") != caller_spec.backend
            or envelope.get("algorithm") != caller_spec.transport.algorithm
            or envelope.get("spec_version") != caller_spec.schema_version
            or envelope.get("deposition_available") is not False
            or envelope.get("scope") != FAST_SCOPE
            or envelope.get("transport_spec_hash") != transport_fingerprint(stored_spec)
            or envelope.get("detector_ids") != [d.id for d in stored_spec.detectors]
        ):
            raise ValueError("fast transport capability/spec metadata mismatch")
        if envelope.get("normalized_spec_sha256") != file_hash(
            out / "normalized-spec.json"
        ):
            raise ValueError("fast normalized spec hash mismatch")
        expected_inputs = {name: file_hash(out / name) for name in _INPUT_FILES}
        if envelope.get("input_hashes") != expected_inputs:
            raise ValueError("fast transport configuration hash mismatch")
        expected_limits = {
            "events": stored_spec.events,
            "intersections": stored_spec.limits.intersections,
            "line_bytes": stored_spec.limits.line_bytes,
            "surfaces_per_event": MAX_SURFACES_PER_EVENT,
        }
        if envelope.get("parsing_limits") != expected_limits:
            raise ValueError(
                "fast transport parsing limits differ from normalized spec"
            )
        for name in _REQUIRED_FILES:
            if not (out / name).is_file():
                raise ValueError(f"fast transport artifact is missing: {name}")
        runtime = self.runtime_identity(out)
        if runtime != envelope.get("runtime") or runtime.get("python") != envelope.get(
            "python"
        ):
            raise ValueError(
                "fast runtime identity differs from the transport envelope"
            )
        if envelope.get("python") != json.loads(
            (out / "python-runtime.json").read_text()
        ):
            raise ValueError(
                "fast Python identity differs from the simulation-start identity"
            )
        if envelope.get("intersections_sha256") != file_hash(
            out / "intersections.jsonl"
        ):
            raise ValueError("fast intersection stream hash mismatch")
        if envelope.get("trace_sha256") != file_hash(out / "fast-trace.jsonl"):
            raise ValueError("fast trace stream hash mismatch")

        events = _iter_jsonl(
            out / "intersections.jsonl",
            caller_spec.limits.line_bytes,
            stored_spec.events,
            "intersections.jsonl",
        )
        traces = _iter_jsonl(
            out / "fast-trace.jsonl",
            caller_spec.limits.line_bytes,
            stored_spec.events,
            "fast-trace.jsonl",
        )
        intersection_count = 0
        surface_count = 0
        expected_plane_ids = [detector.id for detector in stored_spec.detectors]
        event_count = 0
        for event_row, trace_row in zip_longest(events, traces):
            if event_row is None or trace_row is None:
                raise ValueError("fast event/trace count differs from the stored spec")
            _, event = event_row
            _, trace = trace_row
            event_index = event_count
            event_count += 1
            if event.get("simulation_event_id") != event_index:
                raise ValueError("fast event IDs are not complete and ordered")
            if trace.get("simulation_event_id") != event_index:
                raise ValueError("fast trace IDs are not complete and ordered")
            if (
                event.get("schema") != CROSSING_SCHEMA
                or event.get("transport") != self.name
            ):
                raise ValueError("fast crossing schema/backend mismatch")
            if list(event.get("planes", {})) != expected_plane_ids:
                raise ValueError(
                    "fast crossing planes differ from declared detector order"
                )
            if event.get("tracks") != [
                {
                    "track_id": 0,
                    "parent_track_id": None,
                    "parent_unrecorded": False,
                    "primary": True,
                    "pdg": 11,
                }
            ]:
                raise ValueError(
                    "fast crossing event must contain one electron primary"
                )
            _validate_event_trace(stored_spec, event, trace, event_index)
            intersection_count += sum(
                len(plane["mc"]) for plane in event["planes"].values()
            )
            if intersection_count > caller_spec.limits.intersections:
                raise ValueError(
                    "fast intersections exceed caller limits.intersections"
                )
            surfaces = trace.get("surfaces", [])
            if len(surfaces) > MAX_SURFACES_PER_EVENT:
                raise ValueError("fast trace exceeds supported surface count")
            surface_count += len(surfaces)
        if event_count != stored_spec.events:
            raise ValueError("fast event/trace count differs from the stored spec")
        expected_report = {
            "events": stored_spec.events,
            "intersections": intersection_count,
            "surfaces": surface_count,
        }
        if envelope.get("report") != expected_report:
            raise ValueError("fast transport counts differ from envelope report")
        if intersection_count > caller_spec.limits.intersections:
            raise ValueError("fast intersections exceed caller limits.intersections")

        # Compare exact canonical lines against deterministic algorithm replay.
        replay_intersections = hashlib.sha256()
        replay_trace = hashlib.sha256()
        for event, trace in iter_transport(stored_spec):
            replay_intersections.update(_json_line(event))
            replay_trace.update(_json_line(trace))
        if replay_intersections.hexdigest() != envelope["intersections_sha256"]:
            raise ValueError(
                "fast intersections differ from deterministic transport replay"
            )
        if replay_trace.hexdigest() != envelope["trace_sha256"]:
            raise ValueError("fast trace differs from deterministic transport replay")

        expected_material = material_reference(
            stored_spec.detectors,
            stored_spec.beam.position_mm[2],
            stored_spec.transport.air,
            stored_spec.transport.extra_layers,
        )
        if envelope.get("material_reference") != expected_material:
            raise ValueError("fast material reference differs from normalized spec")
        current_python = python_identity()
        return {
            "valid": True,
            "backend": self.name,
            "algorithm": ALGORITHM,
            "events": event_count,
            "intersections": intersection_count,
            "surfaces": surface_count,
            "python_status": (
                "matches" if current_python == envelope["python"] else "drifted"
            ),
            "deposition_available": False,
            "scope": envelope["scope"],
        }
