"""Validation of intersection records, independent of readout."""

from __future__ import annotations

import json
import math
from pathlib import Path


class EventValidationError(ValueError):
    """An event violates the shared event-local truth contract."""


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EventValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise EventValidationError(f"{label} requires three coordinates")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in value
    ):
        raise EventValidationError(f"{label} must be finite")


def validate_event(event, detector_ids, *, deposition_available=True):
    if not isinstance(event, dict):
        raise EventValidationError("event must be an object")
    if not isinstance(deposition_available, bool):
        raise EventValidationError("deposition_available must be bool")
    if not deposition_available and (
        event.get("schema") != "tracksim.crossings.v2"
        or event.get("transport") != "fast_electron"
    ):
        raise EventValidationError(
            "unmodeled deposition requires a fast crossing schema"
        )
    eid = _integer(event.get("simulation_event_id"), "simulation_event_id")
    rows = event.get("tracks")
    if not isinstance(rows, list) or not rows:
        raise EventValidationError("tracks must be nonempty")
    tracks = {}
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise EventValidationError("track must be an object")
        tid = _integer(row.get("track_id"), "track_id")
        if tid in tracks:
            raise EventValidationError("duplicate event-local track ID")
        if "parent_track_id" not in row:
            raise EventValidationError("parent_track_id is required")
        parent = row.get("parent_track_id")
        if parent is not None:
            _integer(parent, "parent_track_id")
        unrecorded = row.get("parent_unrecorded", False)
        primary = row.get("primary", parent is None and not unrecorded)
        if not isinstance(unrecorded, bool) or not isinstance(primary, bool):
            raise EventValidationError("primary/parent_unrecorded must be boolean")
        if (parent is not None and unrecorded) or (
            primary and (parent is not None or unrecorded)
        ):
            raise EventValidationError("invalid primary/parent state")
        pdg = row.get("pdg")
        if isinstance(pdg, bool) or not isinstance(pdg, int) or abs(pdg) > 2**31 - 1:
            raise EventValidationError("invalid PDG code")
        track = {
            **row,
            "parent_track_id": parent,
            "primary": primary,
            "parent_unrecorded": unrecorded,
        }
        tracks[tid] = track
        normalized.append(track)
    if sum(t["primary"] for t in normalized) != 1:
        raise EventValidationError("exactly one primary track is required")
    for tid in tracks:
        visited = set()
        cursor = tid
        while cursor is not None:
            if cursor not in tracks:
                raise EventValidationError("parent is not event-local")
            if cursor in visited:
                raise EventValidationError("track ancestry cycle")
            visited.add(cursor)
            cursor = tracks[cursor].get("parent_track_id")
    planes = event.get("planes")
    expected = set(detector_ids)
    if not isinstance(planes, dict) or set(planes) != expected:
        raise EventValidationError("event detector set does not match declared spec")
    for name, plane in planes.items():
        if (
            not isinstance(plane, dict)
            or "pixels" in plane
            or not isinstance(plane.get("mc"), list)
        ):
            raise EventValidationError("plane must contain intersections, not pixels")
        for index, mc in enumerate(plane["mc"]):
            if not isinstance(mc, dict):
                raise EventValidationError("intersection must be an object")
            tid = _integer(mc.get("track_id"), "intersection track_id")
            if tid not in tracks:
                raise EventValidationError("intersection names a missing track")
            if (
                isinstance(mc.get("pdg"), bool)
                or not isinstance(mc.get("pdg"), int)
                or mc.get("pdg") != tracks[tid]["pdg"]
                or not isinstance(mc.get("primary"), bool)
                or mc["primary"] != tracks[tid]["primary"]
            ):
                raise EventValidationError(
                    "intersection PDG/primary disagrees with track"
                )
            for field in ("local_mm", "global_mid_mm"):
                _vector(mc.get(field), field)
            if not deposition_available and (
                "deposited_e" not in mc or mc["deposited_e"] is not None
            ):
                raise EventValidationError(
                    "fast crossings require explicit null deposited_e"
                )
            for field in (
                ("deposited_e", "global_time_ns")
                if deposition_available
                else ("global_time_ns",)
            ):
                value = mc.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise EventValidationError(
                        f"{field} must be finite and nonnegative"
                    )
    return {**event, "tracks": normalized}


def load_events(
    path: Path,
    detectors,
    *,
    limits=None,
    event_limit=None,
    deposition_available=True,
):
    ids = [d.id if hasattr(d, "id") else d for d in detectors]
    line_limit = limits.line_bytes if limits else 64 * 1024 * 1024
    if event_limit is None:
        event_limit = limits.events if limits else 1_000_000
    if (
        isinstance(event_limit, bool)
        or not isinstance(event_limit, int)
        or not 1 <= event_limit <= 1_000_000
    ):
        raise EventValidationError("invalid event parsing ceiling")
    intersection_limit = limits.intersections if limits else 2_000_000
    events = []
    seen = set()
    count = 0
    with Path(path).open("rb") as source:
        while line := source.readline(line_limit + 1):
            if len(line) > line_limit:
                raise EventValidationError("intersection line exceeds budget")
            if not line.strip():
                continue
            event = validate_event(
                json.loads(line), ids, deposition_available=deposition_available
            )
            eid = event["simulation_event_id"]
            if eid in seen:
                raise EventValidationError("duplicate simulation event ID")
            seen.add(eid)
            count += sum(len(p["mc"]) for p in event["planes"].values())
            if len(events) >= event_limit or count > intersection_limit:
                raise EventValidationError("event/intersection budget exceeded")
            events.append(event)
    if not events:
        raise EventValidationError("empty intersection stream")
    return events
