"""Extension tables: schema handlers, presets, capability checks, backends, writers.

The tables are process-global and append-only. Built-in entries are present at
import, come first and cannot be replaced. Registration happens by importing the
package that registers; nothing is discovered automatically. Lookups of unknown
names raise ``ValueError`` naming the registered alternatives. The tables are not
thread-safe: register before use.

This module imports no other tracksim module at module level; built-in callables
import lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .specs import DetectorSpec, RunSpec, TimelineSpec, TransportSpec


@dataclass(frozen=True)
class SchemaHandler:
    """Normalisation and capability validation for one configuration schema."""

    version: int
    normalize: Callable[[dict], "RunSpec"]
    validate_capabilities: Callable[["RunSpec", str], None]


@dataclass(frozen=True)
class Preset:
    """Schema-4 preset: a detector transformation and optional geometry rows."""

    name: str
    apply: Callable[
        [tuple["DetectorSpec", ...], "TimelineSpec"], tuple["DetectorSpec", ...]
    ]
    geometry_rows: Callable[["RunSpec"], list[dict]] | None = None


@dataclass(frozen=True)
class TransportContext:
    """Inputs handed to a backend's transport normaliser."""

    config: Any
    backend: str
    source_position_mm: tuple[float, float, float]
    direction: tuple[float, float, float]
    covariance: tuple[tuple[float, ...], ...]
    detectors: tuple["DetectorSpec", ...]


@dataclass(frozen=True)
class BackendInfo:
    """Descriptor of a transport backend and the strings it contributes to output."""

    name: str
    factory: Callable[[], Any]
    normalize_transport: Callable[
        [TransportContext], tuple["TransportSpec", float, dict[str, str]]
    ]
    deposition_available: bool
    max_workers: int
    deposition_assumption: str
    limitations: tuple[str, ...]
    neutral_transport_limitation: str
    neutral_material_limitation: str | None
    acceptance_level: str
    physics_hypothesis: str


_SCHEMAS: dict[int, SchemaHandler] = {}
_PRESETS: dict[str, Preset] = {}
_CHECKS: list[Callable[["RunSpec", str], None]] = []
_BACKENDS: dict[str, BackendInfo] = {}
_WRITERS: dict[str, Callable[[], Any]] = {}


def _check_name(kind: str, name: object) -> None:
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError(f"{kind} name must be an identifier: {name!r}")


def _add(table: dict, kind: str, key: Any, value: Any) -> None:
    if key in table:
        raise ValueError(f"{kind} {key!r} is already registered")
    table[key] = value


def _join(names: tuple[str, ...]) -> str:
    return ", ".join(names)


# Schema handlers


def register_schema(handler: SchemaHandler) -> None:
    if isinstance(handler.version, bool) or not isinstance(handler.version, int):
        raise ValueError(f"schema version must be an integer: {handler.version!r}")
    _add(_SCHEMAS, "schema", handler.version, handler)


def schema_handler(version: int) -> SchemaHandler:
    try:
        return _SCHEMAS[version]
    except (KeyError, TypeError):
        raise ValueError(f"unsupported schema_version: {version!r}") from None


def schema_versions() -> tuple[int, ...]:
    return tuple(_SCHEMAS)


# Schema-4 presets


def register_preset(preset: Preset) -> None:
    _check_name("preset", preset.name)
    _add(_PRESETS, "preset", preset.name, preset)


def preset(name: str) -> Preset:
    try:
        return _PRESETS[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"unsupported schema 4 preset {name!r}; "
            f"registered presets: {_join(preset_names())}"
        ) from None


def preset_names() -> tuple[str, ...]:
    return tuple(_PRESETS)


# Capability checks


def register_capability_check(check: Callable[["RunSpec", str], None]) -> None:
    _CHECKS.append(check)


def capability_checks() -> tuple[Callable[["RunSpec", str], None], ...]:
    return tuple(_CHECKS)


# Transport backends


def register_backend(info: BackendInfo) -> None:
    _check_name("backend", info.name)
    _add(_BACKENDS, "backend", info.name, info)


def backend_info(name: str) -> BackendInfo:
    try:
        return _BACKENDS[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"unsupported transport backend: {name!r}; "
            f"registered backends: {_join(backend_names())}"
        ) from None


def backend_names() -> tuple[str, ...]:
    return tuple(_BACKENDS)


# Writers


def register_writer(name: str, factory: Callable[[], Any]) -> None:
    _check_name("writer", name)
    _add(_WRITERS, "writer", name, factory)


def writer_factories() -> dict[str, Callable[[], Any]]:
    return dict(_WRITERS)


def writer_names() -> tuple[str, ...]:
    return tuple(_WRITERS)


# Built-in entries


def _normalize_v4(settings: dict) -> "RunSpec":
    from .configuration_v4 import normalize_v4

    return normalize_v4(settings)


def _validate_v4(spec: "RunSpec", writer: str) -> None:
    from .specs import _validate_v4 as validate

    validate(spec, writer)


def _fast_backend() -> Any:
    from .fast_transport import FastElectronBackend

    return FastElectronBackend()


def _normalize_fast_transport(ctx: TransportContext):
    from .configuration_v4 import _normalize_fast_transport as normalize

    return normalize(ctx)


def _json_writer() -> Any:
    from .writers import JsonWriter

    return JsonWriter()


_FAST_TRANSPORT_LIMITATION = (
    "constant-momentum Gaussian primary core; no bremsstrahlung or secondary transport"
)

_SCHEMAS[4] = SchemaHandler(4, _normalize_v4, _validate_v4)
_PRESETS["generic"] = Preset("generic", lambda detectors, timeline: detectors, None)
_BACKENDS["fast_electron"] = BackendInfo(
    name="fast_electron",
    factory=_fast_backend,
    normalize_transport=_normalize_fast_transport,
    deposition_available=False,
    max_workers=1,
    deposition_assumption="not modeled; deposited_e is null",
    limitations=(
        _FAST_TRANSPORT_LIMITATION,
        "empirical nominal-MIP sensor response; intrinsic energy dependence is not predicted",
        "structural consistency checks only; this is not a physics validation",
    ),
    neutral_transport_limitation=_FAST_TRANSPORT_LIMITATION,
    neutral_material_limitation="center-footprint silicon/extra thin sheets with configured paraxial air",
    acceptance_level="fast-primary-core-consistency",
    physics_hypothesis="fast-primary-core",
)
_WRITERS["json"] = _json_writer
