"""Writer protocol, registry and the neutral JSON writer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import registry
from .neutral import (
    neutral_acceptance_level,
    verify_neutral,
    write_neutral,
)
from .records import FrameSet, ResponseResult
from .specs import RunSpec



@dataclass(frozen=True, slots=True)
class ArtifactSet:
    writer: str
    files: tuple[str, ...]
    acceptance_level: str
    response_provenance: str


@runtime_checkable
class Writer(Protocol):
    def validate_support(self, spec: RunSpec) -> None: ...

    def write(
        self, response: ResponseResult, frames: FrameSet, spec: RunSpec, out: Path
    ) -> ArtifactSet: ...

    def verify(self, spec: RunSpec, out: Path, *, events=None) -> dict: ...


class WriterRegistry:
    """Small explicit registry; named built-ins cannot be replaced."""

    def __init__(self) -> None:
        self._writers: dict[str, Writer] = {
            name: factory() for name, factory in registry.writer_factories().items()
        }
        self._builtin_names = frozenset({"json"})

    def get(self, name: str) -> Writer:
        try:
            return self._writers[name]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unsupported writer {name!r}") from exc

    def register(self, name: str, writer: Writer) -> None:
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ValueError("writer name must be a nonempty identifier")
        if name in self._writers:
            raise ValueError(f"writer {name!r} is already registered")
        if not isinstance(writer, Writer):
            raise ValueError("writer must implement the Writer protocol")
        self._writers[name] = writer

    @property
    def builtin_names(self) -> frozenset[str]:
        return self._builtin_names


class JsonWriter:
    """Versioned neutral JSON writer with checksum and relation verification."""

    name = "json"

    def validate_support(self, spec: RunSpec) -> None:
        if not isinstance(spec, RunSpec) or spec.schema_version != 4:
            raise ValueError("JSON writer requires a schema-4 RunSpec")
        if spec.writer != "json":
            raise ValueError("RunSpec must request writer='json'")

    def write(
        self, response: ResponseResult, frames: FrameSet, spec: RunSpec, out: Path
    ) -> ArtifactSet:
        self.validate_support(spec)
        files = write_neutral(response, frames, spec, out)
        acceptance = neutral_acceptance_level(spec, response.provenance)
        return ArtifactSet("json", files, acceptance, response.provenance)

    def verify(self, spec: RunSpec, out: Path, *, events=None) -> dict:
        self.validate_support(spec)
        return verify_neutral(spec, out, events=events)
