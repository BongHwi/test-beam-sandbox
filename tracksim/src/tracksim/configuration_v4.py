"""Schema-4 configuration normalisation."""

from __future__ import annotations
import math
from . import registry
from .beam import MAX_POLAR_RAD, covariance_factor, material_reference
from .contracts import validate_seed
from .specs import (
    BeamSpec,
    MaterialLayerSpec,
    RunSpec,
    TransportSpec,
    _integer,
    _json_copy,
    _keys,
    _limits,
    _normalize_detectors,
    _normalize_timeline,
    _number,
    _vector,
    validate_capabilities,
)
from .registry import TransportContext

# Supported kinetic-energy domain of the analytic model, in GeV.
SUPPORTED_KINETIC_ENERGY_GEV = (3, 6)


def _names(names) -> str:
    return ", ".join(names)


def normalize_v4(settings: dict) -> RunSpec:
    keys = {
        "schema_version",
        "preset",
        "backend",
        "writer",
        "events",
        "seed",
        "device_seed",
        "workers",
        "beam",
        "transport",
        "timeline",
        "detectors",
        "assumptions",
        "limits",
    }
    _keys(settings, keys, keys - {"assumptions", "limits"}, "configuration")
    preset, backend, writer = (
        settings["preset"],
        settings["backend"],
        settings["writer"],
    )
    if preset not in registry.preset_names():
        raise ValueError(
            f"unsupported schema 4 preset {preset!r}; "
            f"registered presets: {_names(registry.preset_names())}"
        )
    preset_entry = registry.preset(preset)
    if backend not in registry.backend_names():
        raise ValueError(
            f"unsupported transport backend: {backend!r}; "
            f"registered backends: {_names(registry.backend_names())}"
        )
    info = registry.backend_info(backend)
    if writer not in registry.writer_names():
        raise ValueError(
            f"unsupported writer {writer!r}; "
            f"registered writers: {_names(registry.writer_names())}"
        )
    events = _integer(settings["events"], "events", 1, 1_000_000)
    workers = _integer(settings["workers"], "workers", 1, info.max_workers)
    seed = validate_seed(settings["seed"])
    device_seed = validate_seed(settings["device_seed"], "device_seed")
    bc = settings["beam"]
    beam_keys = {
        "particle",
        "kinetic_energy_gev",
        "position_mm",
        "direction",
        "phase_space_covariance",
    }
    _keys(bc, beam_keys, beam_keys, "beam")
    if bc["particle"] != "e-":
        raise ValueError("schema 4 supports electron particle='e-' only")
    energy = _number(
        bc["kinetic_energy_gev"],
        "beam.kinetic_energy_gev",
        minimum=SUPPORTED_KINETIC_ENERGY_GEV[0],
        maximum=SUPPORTED_KINETIC_ENERGY_GEV[1],
    )
    position = _vector(bc["position_mm"], 3, "beam.position_mm")
    if max(map(abs, position)) > 10_000:
        raise ValueError("electron source coordinates exceed the 10 m domain")
    direction = _vector(bc["direction"], 3, "beam.direction")
    scale = max(map(abs, direction))
    if scale == 0:
        raise ValueError("beam.direction must be nonzero")
    scaled = tuple(x / scale for x in direction)
    norm = math.sqrt(sum(x * x for x in scaled))
    direction = tuple(x / norm for x in scaled)
    if (
        direction[2] <= 0
        or math.atan2(math.hypot(direction[0], direction[1]), direction[2])
        >= MAX_POLAR_RAD
    ):
        raise ValueError(
            "electron beam must be forward with polar angle below 0.05 rad"
        )
    covariance_factor(bc["phase_space_covariance"])
    raw = bc["phase_space_covariance"]
    covariance = tuple(
        tuple((float(raw[i][j]) + float(raw[j][i])) / 2 for j in range(5))
        for i in range(5)
    )
    maxima = (1000, 1000, 0.005, 0.005, 0.05)
    if any(math.sqrt(covariance[i][i]) > maxima[i] for i in range(5)):
        raise ValueError(
            "electron source covariance exceeds the spatial/slope/momentum domain"
        )
    timeline = _normalize_timeline(settings["timeline"])
    if timeline.duration_s > (2**63 - 1) / 10**12:
        raise ValueError("electron timeline exceeds supported ps timestamps")
    limits = _limits(settings.get("limits"))
    detectors = tuple(
        _normalize_detectors(settings["detectors"], limits, timeline.clock_hz)
    )
    if any(max(map(abs, d.position_mm)) > 10_000 for d in detectors):
        raise ValueError("electron detector coordinates exceed the 10 m domain")
    if max(d.position_mm[2] for d in detectors) - position[2] > 1000:
        raise ValueError("electron propagation exceeds the 1 m domain")
    assumptions = settings.get("assumptions", {})
    if not isinstance(assumptions, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in assumptions.items()
    ):
        raise ValueError("assumptions must map strings to strings")
    assumptions = {
        **assumptions,
        "synthetic": "synthetic electron source",
        "sensor_response": "empirical nominal-MIP proxy; no intrinsic energy dependence",
        "deposition": info.deposition_assumption,
    }
    detectors = preset_entry.apply(detectors, timeline)
    transport, step, extra = info.normalize_transport(
        TransportContext(
            config=settings["transport"],
            backend=backend,
            source_position_mm=position,
            direction=direction,
            covariance=covariance,
            detectors=detectors,
        )
    )
    assumptions.update(extra)
    spec = RunSpec(
        4,
        preset,
        events,
        seed,
        device_seed,
        workers,
        BeamSpec("e-", energy, position, direction, math.sqrt(covariance[0][0])),
        step,
        timeline,
        detectors,
        writer,
        limits,
        _json_copy(settings),
        _json_copy(assumptions),
        transport,
    )
    validate_capabilities(spec, full_pipeline=False)
    return spec


def _normalize_fast_transport(ctx: TransportContext):
    """Normalise the transport block of the fast backend.

    Returns ``(TransportSpec, step_um, assumption_texts)``.
    """
    tc = ctx.config
    backend = ctx.backend
    position = ctx.source_position_mm
    direction = ctx.direction
    covariance = ctx.covariance
    detectors = ctx.detectors
    _keys(
        tc,
        {"scattering", "air", "extra_layers"},
        {"scattering", "air", "extra_layers"},
        "transport",
    )
    if not isinstance(tc["scattering"], bool) or not isinstance(tc["air"], bool):
        raise ValueError("transport scattering and air must be booleans")
    layers = tc["extra_layers"]
    if not isinstance(layers, list) or len(layers) > 128:
        raise ValueError("transport.extra_layers must contain at most 128 layers")
    extras = []
    for i, layer in enumerate(layers):
        _keys(
            layer,
            {"z_mm", "material_budget"},
            {"z_mm", "material_budget"},
            f"extra_layers[{i}]",
        )
        extras.append(
            MaterialLayerSpec(
                _number(layer["z_mm"], "extra layer z"),
                _number(
                    layer["material_budget"],
                    "extra layer material_budget",
                    minimum=0,
                ),
            )
        )
    zs = [d.position_mm[2] for d in detectors]
    last_z = max(zs)
    if (
        len(set(zs)) != len(zs)
        or min(zs) <= position[2]
        or last_z - position[2] > 1000
    ):
        raise ValueError(
            "fast sensors must have distinct downstream z within a 1 m propagation range"
        )
    for d in detectors:
        if d.orientation_deg[0] != 0 or d.orientation_deg[1] != 0:
            raise ValueError(
                "fast transport requires global-z parallel sensors; x/y tilt is unsupported"
            )
        if max(map(abs, d.position_mm)) > 10_000 or any(
            n * pitch / 1000 > 1000 for n, pitch in zip(d.pixels, d.pitch_um)
        ):
            raise ValueError(
                "fast detector coordinates or extent exceed the supported domain"
            )
    ez = [layer.z_mm for layer in extras]
    if (
        ez != sorted(ez)
        or len(set(ez)) != len(ez)
        or any(z <= position[2] or z >= last_z or z in zs for z in ez)
    ):
        raise ValueError(
            "extra layers must be sorted, distinct and strictly inside the flight range, away from sensors"
        )
    transport = TransportSpec(
        backend,
        covariance,
        tc["scattering"],
        tc["air"],
        tuple(extras),
        "gaussian-primary-v1",
    )
    profile = material_reference(detectors, position[2], transport.air, extras)
    if profile["possible_budget_upper_bound"] > 0.02:
        raise ValueError("conservative possible material budget exceeds 0.02 X0")
    if transport.scattering and 0 < profile["reference_budget"] < 1e-6:
        raise ValueError(
            "positive reference material budget is below the supported 1e-6 X0 floor"
        )
    step = 0.0
    extra = {
        "transport": "Gaussian primary core with apparatus-wide normalization; no bremsstrahlung/secondaries",
        "geometry": "center-footprint thin sheets; no finite-edge or passive-only traversal",
        "timing": "incoming-slope paraxial flight time; not a realized stochastic path",
    }
    return transport, step, extra
