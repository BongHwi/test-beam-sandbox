"""Versioned, immutable run specifications for TrackSim."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any

from . import registry
from .contracts import validate_seed


@dataclass(frozen=True)
class ResponseSpec:
    model: str
    efficiency: float
    resolution_um: tuple[float, float] | None
    cluster_mean: float | None
    response_delay_ns: float
    jitter_ns: float
    dark_hit_rate_per_pixel_hz: float


@dataclass(frozen=True)
class ReadoutSpec:
    model: str
    gate_ps: int | None
    window_cycles: int | None
    window_ps: int | None


@dataclass(frozen=True)
class DetectorSpec:
    """Planar pixel detector.

    ``label`` is an opaque label a registered preset may assign; the core never
    interprets it. It is serialised only when it is not ``None``.
    """

    id: str
    pixels: tuple[int, int]
    pitch_um: tuple[float, float]
    position_mm: tuple[float, float, float]
    orientation_deg: tuple[float, float, float]
    active_um: float
    passive_um: float
    response: ResponseSpec
    readout: ReadoutSpec
    label: str | None = None


@dataclass(frozen=True)
class BeamSpec:
    particle: str
    kinetic_energy_gev: float
    position_mm: tuple[float, float, float]
    direction: tuple[float, float, float]
    sigma_mm: float


@dataclass(frozen=True)
class TimelineSpec:
    rate_hz: float
    duration_s: float
    tail_padding_ps: int
    clock_hz: int


@dataclass(frozen=True)
class Limits:
    events: int = 250_000
    detectors: int = 64
    intersections: int = 2_000_000
    pixels: int = 2_000_000
    frames: int = 2_000_000
    assignments: int = 5_000_000
    contributors: int = 10_000_000
    dark_hits: int = 100_000
    line_bytes: int = 67_108_864


@dataclass(frozen=True)
class MaterialLayerSpec:
    z_mm: float
    material_budget: float


@dataclass(frozen=True)
class TransportSpec:
    backend: str
    phase_space_covariance: tuple[tuple[float, ...], ...]
    scattering: bool
    air: bool
    extra_layers: tuple[MaterialLayerSpec, ...]
    algorithm: str


@dataclass(frozen=True)
class RunSpec:
    schema_version: int
    preset: str
    events: int
    seed: int
    device_seed: int
    workers: int
    beam: BeamSpec
    step_um: float
    timeline: TimelineSpec
    detectors: tuple[DetectorSpec, ...]
    writer: str
    limits: Limits
    settings_json: str
    assumptions_json: str
    transport: TransportSpec | None = None

    @property
    def backend(self) -> str:
        if self.transport is None:
            raise ValueError("spec declares no transport backend")
        return self.transport.backend

    @property
    def deposition_available(self) -> bool:
        return registry.backend_info(self.backend).deposition_available

    @property
    def raw_settings(self) -> dict[str, Any]:
        return json.loads(self.settings_json)

    @property
    def assumptions(self) -> dict[str, str]:
        return json.loads(self.assumptions_json)


MAX_TIMESTAMP_PS = (1 << 63) - 1
# |_stable_normal| <= sqrt(-2 ln 1e-16) = 8.5839; used for stream watermarks and
# for the response-time range check.
JITTER_NORMAL_BOUND = 8.6

_LIMIT_DEFAULTS = asdict(Limits())
_DETECTOR_KEYS = {
    "id",
    "pixels",
    "pitch_um",
    "position_mm",
    "orientation_deg",
    "active_um",
    "passive_um",
    "response",
    "readout",
}
_RESPONSE_COMMON = {
    "model",
    "efficiency",
    "response_delay_ns",
    "jitter_ns",
    "dark_hit_rate_per_pixel_hz",
}
_SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def _keys(value: Any, allowed: set[str], required: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"unknown {where} key(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing {where} key(s): {', '.join(sorted(missing))}")


def _number(
    value: Any,
    where: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    positive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{where} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{where} must be positive")
    if minimum is not None and result < minimum:
        raise ValueError(f"{where} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{where} must be <= {maximum}")
    return result


def _integer(value: Any, where: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{where} must be in [{minimum}, {maximum}]")
    return value


def _vector(value: Any, length: int, where: str, *, positive: bool = False):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{where} must contain exactly {length} values")
    return tuple(
        _number(item, f"{where}[{i}]", positive=positive)
        for i, item in enumerate(value)
    )


def _limits(value: Any) -> Limits:
    if value is None:
        return Limits()
    _keys(value, set(_LIMIT_DEFAULTS), set(), "limits")
    normalized = {}
    for name, default in _LIMIT_DEFAULTS.items():
        normalized[name] = _integer(
            value.get(name, default), f"limits.{name}", 1, default
        )
    return Limits(**normalized)



def normalize_config(settings: dict) -> RunSpec:
    """Validate input and return an immutable run spec for the declared schema."""
    if not isinstance(settings, dict):
        raise ValueError("configuration root must be an object")
    version = settings.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("schema_version must be an integer")
    if version not in registry.schema_versions():
        raise ValueError(f"unsupported schema_version: {version!r}")
    return registry.schema_handler(version).normalize(settings)


def _normalize_timeline(config) -> TimelineSpec:
    _keys(
        config,
        {"rate_hz", "duration_s", "tail_padding_ps", "clock_hz"},
        {"rate_hz", "duration_s", "tail_padding_ps", "clock_hz"},
        "timeline",
    )
    tc = config
    clock = _integer(tc["clock_hz"], "timeline.clock_hz", 1, 10**12)
    if 10**12 % clock:
        raise ValueError("timeline.clock_hz must divide 10^12 exactly")
    return TimelineSpec(
        _number(tc["rate_hz"], "timeline.rate_hz", positive=True),
        _number(tc["duration_s"], "timeline.duration_s", positive=True),
        _integer(tc["tail_padding_ps"], "timeline.tail_padding_ps", 1, 2**63 - 1),
        clock,
    )


def _normalize_detectors(
    raw_detectors, limits: Limits, clock: int
) -> list[DetectorSpec]:
    if not isinstance(raw_detectors, list) or not raw_detectors:
        raise ValueError("detectors must be a non-empty array")
    if len(raw_detectors) > limits.detectors:
        raise ValueError("detector count exceeds limits.detectors")
    ids = set()
    detectors = []
    for i, item in enumerate(raw_detectors):
        where = f"detectors[{i}]"
        _keys(item, _DETECTOR_KEYS, _DETECTOR_KEYS, where)
        detector_id = item["id"]
        if not isinstance(detector_id, str) or not _SAFE_ID.fullmatch(detector_id):
            raise ValueError(f"{where}.id must be a safe ASCII identifier")
        if detector_id.lower() == "global":
            raise ValueError("detector id 'global' is reserved")
        if detector_id in ids:
            raise ValueError(f"duplicate detector id: {detector_id}")
        ids.add(detector_id)
        px = item["pixels"]
        if not isinstance(px, list) or len(px) != 2:
            raise ValueError(f"{where}.pixels must be [columns, rows]")
        pixels = tuple(_integer(v, f"{where}.pixels", 1, 1_000_000) for v in px)
        pitch = _vector(item["pitch_um"], 2, f"{where}.pitch_um", positive=True)
        response_cfg = item["response"]
        if not isinstance(response_cfg, dict):
            raise ValueError(f"{where}.response must be an object")
        model = response_cfg.get("model")
        if model == "boundary_resolution":
            allowed_response = _RESPONSE_COMMON | {"resolution_um"}
            _keys(response_cfg, allowed_response, allowed_response, f"{where}.response")
            resolution = _vector(
                response_cfg["resolution_um"],
                2,
                f"{where}.response.resolution_um",
                positive=True,
            )
            if any(
                not pitch[i] / math.sqrt(48) - 1e-12
                <= resolution[i]
                <= pitch[i] / math.sqrt(12) + 1e-12
                for i in range(2)
            ):
                raise ValueError(
                    f"{where}.response.resolution_um is outside boundary-kernel domain"
                )
            cluster_mean = None
        elif model == "boundary_cluster_mean":
            allowed_response = _RESPONSE_COMMON | {"cluster_mean"}
            _keys(response_cfg, allowed_response, allowed_response, f"{where}.response")
            resolution = None
            cluster_mean = _number(
                response_cfg["cluster_mean"],
                f"{where}.response.cluster_mean",
                minimum=1,
                maximum=2.25,
            )
        else:
            raise ValueError(f"unsupported response model: {model!r}")
        delay_ns = _number(
            response_cfg["response_delay_ns"],
            f"{where}.response.response_delay_ns",
            minimum=0,
        )
        jitter_ns = _number(
            response_cfg["jitter_ns"], f"{where}.response.jitter_ns", minimum=0
        )
        if not 1000 * delay_ns <= MAX_TIMESTAMP_PS:
            raise ValueError(
                f"{where}.response.response_delay_ns exceeds the supported "
                "timestamp range"
            )
        if not 1000 * (delay_ns + JITTER_NORMAL_BOUND * jitter_ns) <= MAX_TIMESTAMP_PS:
            raise ValueError(
                f"{where}.response.jitter_ns exceeds the supported timestamp "
                "range together with response_delay_ns"
            )
        response = ResponseSpec(
            model,
            _number(
                response_cfg["efficiency"],
                f"{where}.response.efficiency",
                minimum=0,
                maximum=1,
            ),
            resolution,
            cluster_mean,
            delay_ns,
            jitter_ns,
            _number(
                response_cfg["dark_hit_rate_per_pixel_hz"],
                f"{where}.response.dark_hit_rate_per_pixel_hz",
                minimum=0,
            ),
        )
        readout_cfg = item["readout"]
        if not isinstance(readout_cfg, dict):
            raise ValueError(f"{where}.readout must be an object")
        readout_model = readout_cfg.get("model")
        if readout_model == "triggered":
            allowed = {"model", "gate_ns", "gate_ps"}
            _keys(
                readout_cfg,
                allowed,
                {"model"} | (set(readout_cfg) & {"gate_ns", "gate_ps"}),
                f"{where}.readout",
            )
            if ("gate_ns" in readout_cfg) == ("gate_ps" in readout_cfg):
                raise ValueError(
                    f"{where}.readout requires exactly one of gate_ns or gate_ps"
                )
            if "gate_ns" in readout_cfg:
                gate = (
                    _number(
                        readout_cfg["gate_ns"],
                        f"{where}.readout.gate_ns",
                        positive=True,
                    )
                    * 1000
                )
                if not gate.is_integer():
                    raise ValueError(
                        f"{where}.readout.gate_ns must resolve to integer ps"
                    )
                gate_ps = int(gate)
            else:
                gate_ps = _integer(
                    readout_cfg["gate_ps"], f"{where}.readout.gate_ps", 1, 2**63 - 1
                )
            readout = ReadoutSpec("triggered", gate_ps, None, None)
        elif readout_model == "continuous":
            allowed = {"model", "window_cycles", "window_ps"}
            _keys(
                readout_cfg,
                allowed,
                {"model"} | (set(readout_cfg) & {"window_cycles", "window_ps"}),
                f"{where}.readout",
            )
            if ("window_cycles" in readout_cfg) == ("window_ps" in readout_cfg):
                raise ValueError(
                    f"{where}.readout requires exactly one of window_cycles or window_ps"
                )
            if "window_cycles" in readout_cfg:
                cycles = _integer(
                    readout_cfg["window_cycles"],
                    f"{where}.readout.window_cycles",
                    1,
                    2**32 - 1,
                )
                window_ps = cycles * (10**12 // clock)
            else:
                window_ps = _integer(
                    readout_cfg["window_ps"], f"{where}.readout.window_ps", 1, 2**63 - 1
                )
                cycles = None
            readout = ReadoutSpec("continuous", None, cycles, window_ps)
        else:
            raise ValueError(f"unsupported readout model: {readout_model!r}")
        detectors.append(
            DetectorSpec(
                detector_id,
                pixels,
                pitch,
                _vector(item["position_mm"], 3, f"{where}.position_mm"),
                _vector(item["orientation_deg"], 3, f"{where}.orientation_deg"),
                _number(item["active_um"], f"{where}.active_um", positive=True),
                _number(item["passive_um"], f"{where}.passive_um", minimum=0),
                response,
                readout,
            )
        )
    return detectors


def _validate_v4(spec: RunSpec, writer: str) -> None:
    if spec.backend not in registry.backend_names():
        raise ValueError(f"unsupported electron backend: {spec.backend}")
    if writer not in registry.writer_names():
        raise ValueError(f"unsupported electron writer: {writer}")


def validate_capabilities(
    spec: RunSpec,
    *,
    full_pipeline: bool = True,
    writer: str | None = None,
) -> None:
    """Fail closed when a requested writer or backend is unsupported."""
    requested = spec.writer if writer is None else writer
    try:
        handler = registry.schema_handler(spec.schema_version)
    except ValueError:
        raise ValueError(f"unsupported schema version {spec.schema_version}") from None
    handler.validate_capabilities(spec, requested)
    for check in registry.capability_checks():
        check(spec, requested)
    if full_pipeline and (
        not math.isfinite(spec.timeline.duration_s * 10**12)
        or spec.timeline.duration_s * 10**12 > 2**63 - 1
    ):
        raise ValueError("pipeline duration exceeds supported ps timestamps")
    if full_pipeline and spec.events > spec.limits.events:
        raise ValueError(
            f"full pipeline events exceed limits.events={spec.limits.events}"
        )


def geometry_rows(spec: RunSpec) -> list[dict[str, Any]]:
    """Return the geometry rows written to ``geometry.json``."""
    entry = registry.preset(spec.preset)
    if entry.geometry_rows is not None:
        return entry.geometry_rows(spec)
    return [
        {
            "name": d.id,
            "id": d.id,
            "kind": "pixel",
            "pixels": list(d.pixels),
            "pitch_um": list(d.pitch_um),
            "position_mm": list(d.position_mm),
            "orientation_deg": list(d.orientation_deg),
        }
        for d in spec.detectors
    ]


def _detector_dict(detector: DetectorSpec) -> dict[str, Any]:
    value = asdict(detector)
    if value["label"] is None:
        del value["label"]
    return value


def spec_to_dict(spec: RunSpec) -> dict[str, Any]:
    beam = asdict(spec.beam)
    beam.pop("sigma_mm")
    return {
        "schema_version": spec.schema_version,
        "preset": spec.preset,
        "events": spec.events,
        "seed": spec.seed,
        "device_seed": spec.device_seed,
        "workers": spec.workers,
        "backend": spec.backend,
        "beam": beam,
        "transport": asdict(spec.transport),
        "step_um": spec.step_um if spec.deposition_available else None,
        "deposition_available": spec.deposition_available,
        "timeline": asdict(spec.timeline),
        "detectors": [_detector_dict(d) for d in spec.detectors],
        "writer": spec.writer,
        "limits": asdict(spec.limits),
        "assumptions": spec.assumptions,
    }


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transport_fingerprint(spec: RunSpec) -> str:
    value = spec_to_dict(spec)
    for key in (
        "writer",
        "timeline",
        "device_seed",
        "limits",
        "assumptions",
        "preset",
    ):
        value.pop(key)
    value["detectors"] = [
        {k: v for k, v in d.items() if k not in ("response", "readout", "label")}
        for d in value["detectors"]
    ]
    return canonical_hash(value)


def response_fingerprint(spec: RunSpec) -> str:
    digest = canonical_hash(
        {
            "detectors": [
                {
                    "id": d.id,
                    "geometry": {
                        key: getattr(d, key)
                        for key in (
                            "pixels",
                            "pitch_um",
                            "position_mm",
                            "orientation_deg",
                            "active_um",
                            "passive_um",
                        )
                    },
                    "response": asdict(d.response),
                }
                for d in spec.detectors
            ],
            "timeline": {
                "rate_hz": spec.timeline.rate_hz,
                "duration_s": spec.timeline.duration_s,
                "tail_padding_ps": spec.timeline.tail_padding_ps,
            },
            "events": spec.events,
            "seed": spec.seed,
            "device_seed": spec.device_seed,
        }
    )

    return canonical_hash(
        {
            "schema": "tracksim.response-assumptions.v2",
            "fields_hash": digest,
            "deposition_available": spec.deposition_available,
            "primary_pdg": 11,
        }
    )


def readout_fingerprint(spec: RunSpec) -> str:
    return canonical_hash(
        {
            "detectors": [
                {"id": d.id, "readout": asdict(d.readout)} for d in spec.detectors
            ],
            "clock_hz": spec.timeline.clock_hz,
        }
    )


def _json_copy(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)

