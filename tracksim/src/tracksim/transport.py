"""Transport backend facade with explicit, validated backend dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from . import registry
from .runtime import run_path
from .specs import RunSpec, normalize_config


class TransportBackendProtocol(Protocol):
    name: str

    def simulate(self, spec: RunSpec, out: Path) -> Path: ...

    def verify(self, out: Path, spec: RunSpec) -> dict: ...

    def required_files(self, spec: RunSpec) -> tuple[str, ...]: ...

    def runtime_identity(self, out: Path) -> dict: ...

    def validate_runtime_record(
        self, spec: RunSpec, record: object, python: dict
    ) -> None: ...

    def preflight_identity(self, spec: RunSpec) -> dict: ...


TransportBackend = TransportBackendProtocol


def backend_for(spec: RunSpec) -> TransportBackendProtocol:
    """Return the backend selected by a normalized spec."""
    return registry.backend_info(spec.backend).factory()


def required_files(spec: RunSpec) -> tuple[str, ...]:
    """Return the selected backend's transport-stage artifact requirements."""
    return backend_for(spec).required_files(spec)


def simulate_run(out: Path, settings: dict) -> Path:
    spec = normalize_config(settings)
    return backend_for(spec).simulate(spec, out)


def verify_transport(out: Path, spec: RunSpec | None = None) -> dict:
    """Dispatch transport verification using the validated spec backend."""
    out = run_path(out)
    if spec is None:
        spec = normalize_config(json.loads((out / "settings.json").read_text()))
    return backend_for(spec).verify(out, spec)


__all__ = [
    "TransportBackend",
    "TransportBackendProtocol",
    "backend_for",
    "required_files",
    "simulate_run",
    "verify_transport",
]
