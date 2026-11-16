"""Immutable response and readout records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Trigger:
    ordinal: int
    simulation_event_id: int
    physical_trigger_id: int
    origin_ps: int


@dataclass(frozen=True, slots=True)
class Outcome:
    id: int
    simulation_event_id: int
    detector: str
    track_id: int
    parent_track_id: int | None
    parent_unrecorded: bool
    primary: bool
    pdg: int
    intersection_index: int
    deposited_charge_e: int | float | None
    eligible: bool
    excluded_reason: str | None
    accepted: bool
    time_ps: int | None
    pixel_coordinates: tuple[tuple[int, int], ...]
    centroid_residual_um: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class PixelHit:
    id: str
    detector: str
    col: int
    row: int
    time_ps: int
    response_id: int | None
    noise_id: str | None


@dataclass(frozen=True, slots=True)
class Contributor:
    simulation_event_id: int
    track_id: int
    primary: bool
    pdg: int


@dataclass(frozen=True, slots=True)
class ResponseResult:
    schema: str
    source_sha256: str
    response_spec_hash: str
    timeline: tuple[Trigger, ...]
    outcomes: tuple[Outcome, ...]
    hits: tuple[PixelHit, ...]
    horizon_ps: int
    dark_hits: int
    provenance: str


@dataclass(frozen=True, slots=True)
class FramePixel:
    col: int
    row: int
    hit_ids: tuple[str, ...]
    response_ids: tuple[int, ...]
    noise_ids: tuple[str, ...]
    contributors: tuple[Contributor, ...]
    noise: bool


@dataclass(frozen=True, slots=True)
class Frame:
    detector: str
    index: int
    model: str
    start_ps: int
    end_ps: int
    trigger_id: int | None
    simulation_event_id: int | None
    pixels: tuple[FramePixel, ...]


@dataclass(frozen=True, slots=True)
class FrameSet:
    schema: str
    readout_spec_hash: str
    horizon_ps: int
    frames: tuple[Frame, ...]
    assignments: int
    contributor_links: int
