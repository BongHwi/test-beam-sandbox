"""Pure detector-aware grouping of responded pixel hits into readout frames."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Iterator

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
from . import response as _response
from .specs import Limits, RunSpec, readout_fingerprint, response_fingerprint


class ReadoutError(ValueError):
    """Invalid response/readout relationship or a readout resource-limit failure."""


@dataclass(frozen=True)
class _AssignmentPlan:
    frame_count: int
    assignment_count: int
    contributor_upper_bound: int
    frame_counts: tuple[tuple[str, int], ...]
    origins: tuple[int, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _limits(spec: RunSpec, requested: Limits | None) -> Limits:
    configured = spec.limits
    if not isinstance(configured, Limits):
        raise ReadoutError("spec.limits must be Limits")
    effective = configured if requested is None else requested
    if not isinstance(effective, Limits):
        raise ReadoutError("limits must be Limits")
    for name in configured.__dataclass_fields__:
        value = getattr(effective, name)
        maximum = getattr(configured, name)
        if not _is_int(value) or value < 1 or value > maximum:
            raise ReadoutError(f"limits.{name} must be an integer in [1, {maximum}]")
    return effective


def _validate_trigger_timeline(
    result: ResponseResult, spec: RunSpec, limits: Limits
) -> tuple[int, ...]:
    if not isinstance(result.timeline, tuple) or len(result.timeline) != spec.events:
        raise ReadoutError("response timeline count must equal spec.events")
    if len(result.timeline) > limits.events:
        raise ReadoutError(
            f"timeline event count exceeds limits.events={limits.events}"
        )
    origins = []
    event_ids = set()
    previous = -1
    for ordinal, trigger in enumerate(result.timeline):
        if not isinstance(trigger, Trigger):
            raise ReadoutError(f"timeline[{ordinal}] must be Trigger")
        if trigger.ordinal != ordinal or not _is_int(trigger.ordinal):
            raise ReadoutError(
                f"timeline[{ordinal}].ordinal must equal its zero-based index"
            )
        if not _is_int(trigger.simulation_event_id) or trigger.simulation_event_id < 0:
            raise ReadoutError(
                f"timeline[{ordinal}].simulation_event_id must be nonnegative"
            )
        if trigger.simulation_event_id in event_ids:
            raise ReadoutError("timeline simulation_event_id values must be unique")
        event_ids.add(trigger.simulation_event_id)
        expected_number = _response.trigger_number(ordinal)
        if (
            not _is_int(trigger.physical_trigger_id)
            or trigger.physical_trigger_id != expected_number
        ):
            raise ReadoutError(
                f"timeline[{ordinal}].physical_trigger_id must equal "
                f"{expected_number}"
            )
        if not _is_int(trigger.origin_ps) or not 0 <= trigger.origin_ps <= 2**63 - 1:
            raise ReadoutError(
                f"timeline[{ordinal}].origin_ps must be a supported nonnegative ps integer"
            )
        if trigger.origin_ps < previous:
            raise ReadoutError("timeline trigger origins must be nondecreasing")
        previous = trigger.origin_ps
        origins.append(trigger.origin_ps)
    return tuple(origins)


def _validate_records(
    result: ResponseResult,
    spec: RunSpec,
    limits: Limits,
) -> tuple[dict[str, Outcome | None], dict[int, Outcome]]:
    if not isinstance(result, ResponseResult):
        raise ReadoutError("result must be ResponseResult")
    if result.schema != "tracksim.response.v2":
        raise ReadoutError(
            "response records must use schema tracksim.response.v2"
        )
    if not isinstance(result.schema, str) or not result.schema:
        raise ReadoutError("response result schema must be a nonempty string")
    if not isinstance(
        result.response_spec_hash, str
    ) or result.response_spec_hash != response_fingerprint(spec):
        raise ReadoutError("response result fingerprint does not match spec")
    if not _is_int(result.horizon_ps) or not 1 <= result.horizon_ps <= 2**63 - 1:
        raise ReadoutError("response horizon must be a positive supported ps integer")
    if (
        not _is_int(result.dark_hits)
        or result.dark_hits < 0
        or result.dark_hits > limits.dark_hits
    ):
        raise ReadoutError(
            f"response dark hit count exceeds limits.dark_hits={limits.dark_hits}"
        )
    if (
        not isinstance(result.outcomes, tuple)
        or len(result.outcomes) > limits.intersections
    ):
        raise ReadoutError(
            f"response outcomes exceed limits.intersections={limits.intersections}"
        )
    if not isinstance(result.hits, tuple) or len(result.hits) > limits.pixels:
        raise ReadoutError(f"response hits exceed limits.pixels={limits.pixels}")

    detector_by_id = {detector.id: detector for detector in spec.detectors}
    if not detector_by_id or len(detector_by_id) != len(spec.detectors):
        raise ReadoutError("spec must declare unique detectors")
    if len(detector_by_id) > limits.detectors:
        raise ReadoutError(
            f"detector count exceeds limits.detectors={limits.detectors}"
        )

    outcomes: dict[int, Outcome] = {}
    expected_hits: dict[str, tuple[Outcome, int, int, int]] = {}
    timeline_event_ids = {trigger.simulation_event_id for trigger in result.timeline}
    intersection_keys = set()
    latest_eligible_ps = 0
    for index, outcome in enumerate(result.outcomes, 1):
        if not isinstance(outcome, Outcome):
            raise ReadoutError(f"outcomes[{index - 1}] must be Outcome")
        if not _is_int(outcome.id) or outcome.id != index:
            raise ReadoutError(
                "outcome IDs must be a one-based response-order sequence"
            )
        if outcome.id in outcomes:
            raise ReadoutError(f"duplicate outcome ID {outcome.id}")
        if outcome.detector not in detector_by_id:
            raise ReadoutError(
                f"outcome {outcome.id} refers to unknown detector {outcome.detector!r}"
            )
        if (
            not _is_int(outcome.simulation_event_id)
            or outcome.simulation_event_id not in timeline_event_ids
        ):
            raise ReadoutError(
                f"outcome {outcome.id} refers to an unknown simulation event"
            )
        if not _is_int(outcome.track_id) or outcome.track_id < 0:
            raise ReadoutError(f"outcome {outcome.id}.track_id must be nonnegative")
        if outcome.parent_track_id is not None and (
            not _is_int(outcome.parent_track_id) or outcome.parent_track_id < 0
        ):
            raise ReadoutError(f"outcome {outcome.id}.parent_track_id is invalid")
        if not isinstance(outcome.parent_unrecorded, bool) or not isinstance(
            outcome.primary, bool
        ):
            raise ReadoutError(f"outcome {outcome.id} ancestry flags must be bool")
        if outcome.primary and outcome.parent_track_id is not None:
            raise ReadoutError(
                f"primary outcome {outcome.id} cannot have a recorded parent"
            )
        if outcome.parent_track_id is not None and outcome.parent_unrecorded:
            raise ReadoutError(
                f"outcome {outcome.id} cannot have both a recorded and unrecorded parent"
            )
        if (
            not _is_int(outcome.pdg)
            or not _is_int(outcome.intersection_index)
            or outcome.intersection_index < 0
        ):
            raise ReadoutError(
                f"outcome {outcome.id} PDG/intersection index is invalid"
            )
        if not isinstance(outcome.pixel_coordinates, tuple):
            raise ReadoutError(
                f"outcome {outcome.id} pixel_coordinates must be a tuple"
            )
        intersection_key = (
            outcome.simulation_event_id,
            outcome.detector,
            outcome.intersection_index,
        )
        if intersection_key in intersection_keys:
            raise ReadoutError(
                f"duplicate response intersection identity {intersection_key}"
            )
        intersection_keys.add(intersection_key)
        if not isinstance(outcome.eligible, bool) or not isinstance(
            outcome.accepted, bool
        ):
            raise ReadoutError(
                f"outcome {outcome.id} eligibility/acceptance flags must be bool"
            )
        if outcome.accepted and not outcome.eligible:
            raise ReadoutError(f"ineligible outcome {outcome.id} cannot be accepted")
        if not spec.deposition_available:
            if outcome.deposited_charge_e is not None:
                raise ReadoutError(
                    "fast response must declare unmodeled deposited charge as null"
                )
        elif (
            isinstance(outcome.deposited_charge_e, bool)
            or not isinstance(outcome.deposited_charge_e, (int, float))
            or not math.isfinite(outcome.deposited_charge_e)
            or outcome.deposited_charge_e < 0
        ):
            raise ReadoutError(
                f"outcome {outcome.id}.deposited_charge_e must be finite and nonnegative"
            )
        if outcome.eligible:
            if (
                not _is_int(outcome.time_ps)
                or not 0 <= outcome.time_ps < result.horizon_ps
            ):
                raise ReadoutError(
                    f"eligible outcome {outcome.id} needs a time inside the response horizon"
                )
            latest_eligible_ps = max(latest_eligible_ps, outcome.time_ps)
            if outcome.excluded_reason is not None:
                raise ReadoutError(
                    f"eligible outcome {outcome.id} cannot have an exclusion reason"
                )
        elif (
            outcome.time_ps is not None or outcome.accepted or outcome.pixel_coordinates
        ):
            raise ReadoutError(
                f"ineligible outcome {outcome.id} cannot carry time or pixels"
            )
        elif (
            not isinstance(outcome.excluded_reason, str) or not outcome.excluded_reason
        ):
            raise ReadoutError(
                f"ineligible outcome {outcome.id} needs an exclusion reason"
            )
        if outcome.accepted:
            if not outcome.pixel_coordinates:
                raise ReadoutError(
                    f"accepted outcome {outcome.id} has no emitted pixels"
                )
            detector = detector_by_id[outcome.detector]
            for pixel_index, coordinate in enumerate(outcome.pixel_coordinates):
                if (
                    not isinstance(coordinate, tuple)
                    or len(coordinate) != 2
                    or not _is_int(coordinate[0])
                    or not _is_int(coordinate[1])
                ):
                    raise ReadoutError(
                        f"outcome {outcome.id} has an invalid pixel coordinate"
                    )
                col, row = coordinate
                if (
                    not 0 <= col < detector.pixels[0]
                    or not 0 <= row < detector.pixels[1]
                ):
                    raise ReadoutError(
                        f"outcome {outcome.id} pixel is outside {outcome.detector}"
                    )
                hit_id = f"s:{outcome.id}:{pixel_index}"
                if hit_id in expected_hits:
                    raise ReadoutError(f"duplicate expected signal hit ID {hit_id}")
                expected_hits[hit_id] = (outcome, col, row, outcome.time_ps)
        elif outcome.pixel_coordinates:
            raise ReadoutError(
                f"rejected outcome {outcome.id} cannot carry emitted pixels"
            )
        outcomes[outcome.id] = outcome

    if (
        not isinstance(result.source_sha256, str)
        or len(result.source_sha256) != 64
        or any(
            character not in "0123456789abcdef" for character in result.source_sha256
        )
    ):
        raise ReadoutError("response source hash must be a lowercase SHA256 digest")
    if not isinstance(result.provenance, str) or not result.provenance:
        raise ReadoutError("response provenance must be a nonempty string")

    actual_signal_ids = set()
    noise_ids = set()
    hit_ids = set()
    hit_outcomes: dict[str, Outcome | None] = {}
    for index, hit in enumerate(result.hits):
        if not isinstance(hit, PixelHit):
            raise ReadoutError(f"hits[{index}] must be PixelHit")
        if not isinstance(hit.id, str) or not hit.id or hit.id in hit_ids:
            raise ReadoutError(f"hits[{index}] has an empty or duplicate ID")
        hit_ids.add(hit.id)
        detector = detector_by_id.get(hit.detector)
        if detector is None:
            raise ReadoutError(
                f"hit {hit.id} refers to unknown detector {hit.detector!r}"
            )
        if not _is_int(hit.col) or not _is_int(hit.row):
            raise ReadoutError(f"hit {hit.id} coordinates must be integers")
        if (
            not 0 <= hit.col < detector.pixels[0]
            or not 0 <= hit.row < detector.pixels[1]
        ):
            raise ReadoutError(f"hit {hit.id} is outside detector {hit.detector}")
        if not _is_int(hit.time_ps) or not 0 <= hit.time_ps < result.horizon_ps:
            raise ReadoutError(f"hit {hit.id} time is outside the response horizon")
        if hit.response_id is not None:
            expected = expected_hits.get(hit.id)
            if (
                not _is_int(hit.response_id)
                or hit.noise_id is not None
                or expected is None
            ):
                raise ReadoutError(
                    f"signal hit {hit.id} has an unknown response outcome"
                )
            outcome, col, row, time_ps = expected
            if (
                hit.response_id != outcome.id
                or hit.detector != outcome.detector
                or (hit.col, hit.row, hit.time_ps) != (col, row, time_ps)
            ):
                raise ReadoutError(
                    f"signal hit {hit.id} disagrees with its response outcome"
                )
            actual_signal_ids.add(hit.id)
            hit_outcomes[hit.id] = outcome
        else:
            if (
                not isinstance(hit.noise_id, str)
                or not hit.noise_id
                or hit.id != hit.noise_id
            ):
                raise ReadoutError(f"noise hit {hit.id} must carry its own noise_id")
            if hit.noise_id in noise_ids:
                raise ReadoutError(f"duplicate noise ID {hit.noise_id}")
            noise_ids.add(hit.noise_id)
            hit_outcomes[hit.id] = None
    if actual_signal_ids != set(expected_hits):
        missing = sorted(set(expected_hits) - actual_signal_ids)
        extra = sorted(actual_signal_ids - set(expected_hits))
        raise ReadoutError(
            f"signal hit/outcome coverage mismatch; missing={missing[:3]}, extra={extra[:3]}"
        )
    if len(noise_ids) != result.dark_hits:
        raise ReadoutError(
            f"dark hit records ({len(noise_ids)}) differ from response.dark_hits ({result.dark_hits})"
        )
    duration_ps = round(spec.timeline.duration_s * 1_000_000_000_000)
    expected_horizon = max(
        duration_ps, latest_eligible_ps + spec.timeline.tail_padding_ps, 1
    )
    if result.horizon_ps != expected_horizon:
        raise ReadoutError(
            f"response horizon {result.horizon_ps} differs from timeline contract {expected_horizon}"
        )
    return hit_outcomes, outcomes


def _validate_readout(detector) -> None:
    readout = detector.readout
    if readout.model == "triggered":
        if (
            not _is_int(readout.gate_ps)
            or readout.gate_ps <= 0
            or readout.window_cycles is not None
            or readout.window_ps is not None
        ):
            raise ReadoutError(
                f"triggered detector {detector.id} has invalid readout settings"
            )
    elif readout.model == "continuous":
        if (
            not _is_int(readout.window_ps)
            or readout.window_ps <= 0
            or readout.gate_ps is not None
        ):
            raise ReadoutError(
                f"continuous detector {detector.id} has invalid readout settings"
            )
    else:
        raise ReadoutError(
            f"unsupported readout model {readout.model!r} for {detector.id}"
        )


def _frame_count(detector, horizon_ps: int, trigger_count: int) -> int:
    _validate_readout(detector)
    readout = detector.readout
    if readout.model == "triggered":
        return trigger_count
    return (horizon_ps + readout.window_ps - 1) // readout.window_ps


def _target_count(hit: PixelHit, detector, origins: tuple[int, ...]) -> int:
    readout = detector.readout
    if readout.model == "triggered":
        first = bisect_right(origins, hit.time_ps - readout.gate_ps)
        stop = bisect_right(origins, hit.time_ps)
        return stop - first
    return 1


def _preflight(
    result: ResponseResult, spec: RunSpec, limits: Limits
) -> _AssignmentPlan:
    origins = _validate_trigger_timeline(result, spec, limits)
    hit_outcomes, _ = _validate_records(result, spec, limits)
    frame_counts = []
    total_frames = 0
    for detector in spec.detectors:
        count = _frame_count(detector, result.horizon_ps, len(origins))
        total_frames += count
        frame_counts.append((detector.id, count))
        if total_frames > limits.frames:
            raise ReadoutError(
                f"readout frame count exceeds limits.frames={limits.frames}"
            )

    assignments = 0
    contributor_upper_bound = 0
    detector_by_id = {detector.id: detector for detector in spec.detectors}
    for hit in result.hits:
        detector = detector_by_id[hit.detector]
        count = _target_count(hit, detector, origins)
        assignments += count
        contributor_upper_bound += count * (
            3 if hit_outcomes[hit.id] is not None else 2
        )
        if assignments > limits.assignments:
            raise ReadoutError(
                f"readout assignments exceed limits.assignments={limits.assignments}"
            )
        if contributor_upper_bound > limits.contributors:
            raise ReadoutError(
                f"readout contributor upper bound exceeds limits.contributors={limits.contributors}"
            )
    return _AssignmentPlan(
        total_frames,
        assignments,
        contributor_upper_bound,
        tuple(frame_counts),
        origins,
    )


def assemble_frames(
    spec: RunSpec,
    detector_id: str,
    start_ps: int,
    end_ps: int,
    *,
    timeline: Sequence[Trigger],
    hits: Iterable[PixelHit],
    outcomes: Mapping[int, Outcome],
    horizon_ps: int | None = None,
) -> tuple[Frame, ...]:
    """Materialize the frames of one detector whose start lies in ``[start_ps, end_ps)``.

    An empty interval (``start_ps >= end_ps``) returns ``()``.

    A triggered detector has one frame per trigger of ``timeline`` with
    ``start_ps <= origin_ps < end_ps``, in ordinal order. A continuous detector
    has one frame per window index ``i`` with ``start_ps <= i * window_ps <
    end_ps``, limited to ``i < ceil(horizon_ps / window_ps)`` when ``horizon_ps``
    is given.

    A frame holds the hits whose time lies in its interval, taken from ``hits``
    in the given order. Hits of other detectors, and hits outside the selected
    frames, are ignored. A signal hit whose ``response_id`` is missing from
    ``outcomes`` raises ``ReadoutError``. The result depends only on the
    interval, so adjacent intervals give the same frames as one long interval.
    """
    detector = next((d for d in spec.detectors if d.id == detector_id), None)
    if detector is None:
        raise ReadoutError(f"unknown detector {detector_id!r}")
    if not _is_int(start_ps) or not _is_int(end_ps) or start_ps < 0 or end_ps < 0:
        raise ReadoutError("frame interval must be nonnegative integers")
    if start_ps >= end_ps:
        return ()
    if horizon_ps is not None and (not _is_int(horizon_ps) or horizon_ps < 1):
        raise ReadoutError("horizon_ps must be a positive integer")
    _validate_readout(detector)
    readout = detector.readout

    if readout.model == "triggered":
        gate = readout.gate_ps
        selected = [
            trigger
            for trigger in timeline
            if start_ps <= trigger.origin_ps < end_ps
        ]
        origins = tuple(trigger.origin_ps for trigger in selected)
        if any(later < earlier for earlier, later in zip(origins, origins[1:])):
            raise ReadoutError("timeline trigger origins must be nondecreasing")
        frame_meta = {
            trigger.ordinal: (
                readout.model,
                trigger.origin_ps,
                trigger.origin_ps + gate,
                trigger.physical_trigger_id,
                trigger.simulation_event_id,
            )
            for trigger in selected
        }
        ordinals = tuple(trigger.ordinal for trigger in selected)

        def targets(time_ps: int) -> Iterator[int]:
            first = bisect_right(origins, time_ps - gate)
            stop = bisect_right(origins, time_ps)
            for position in range(first, stop):
                if origins[position] <= time_ps < origins[position] + gate:
                    yield ordinals[position]

    else:
        window = readout.window_ps
        first_index = -(-start_ps // window)
        stop_index = -(-end_ps // window)
        if horizon_ps is not None:
            stop_index = min(stop_index, -(-horizon_ps // window))
        frame_meta = {
            index: (readout.model, index * window, (index + 1) * window, None, None)
            for index in range(first_index, stop_index)
        }

        def targets(time_ps: int) -> Iterator[int]:
            yield time_ps // window

    grouped: dict[int, dict[tuple[int, int], dict[str, object]]] = {}
    for hit in hits:
        if hit.detector != detector_id:
            continue
        outcome = None
        if hit.response_id is not None:
            outcome = outcomes.get(hit.response_id)
            if outcome is None:
                raise ReadoutError(
                    f"signal hit {hit.id} refers to unknown outcome {hit.response_id}"
                )
        for frame_index in targets(hit.time_ps):
            if frame_index not in frame_meta:
                continue
            pixel_key = (hit.col, hit.row)
            frame_pixels = grouped.setdefault(frame_index, {})
            pixel = frame_pixels.setdefault(
                pixel_key,
                {
                    "hit_ids": [],
                    "response_ids": [],
                    "noise_ids": [],
                    "contributors": [],
                    "noise": False,
                },
            )
            pixel["hit_ids"].append(hit.id)
            if hit.response_id is not None:
                if hit.response_id not in pixel["response_ids"]:
                    pixel["response_ids"].append(hit.response_id)
                contributor = Contributor(
                    outcome.simulation_event_id,
                    outcome.track_id,
                    outcome.primary,
                    outcome.pdg,
                )
                if contributor not in pixel["contributors"]:
                    pixel["contributors"].append(contributor)
            else:
                pixel["noise"] = True
                if hit.noise_id not in pixel["noise_ids"]:
                    pixel["noise_ids"].append(hit.noise_id)

    frames = []
    for frame_index in sorted(frame_meta):
        model, start, end, trigger_id, simulation_event_id = frame_meta[frame_index]
        pixels = [
            FramePixel(
                col,
                row,
                tuple(data["hit_ids"]),
                tuple(data["response_ids"]),
                tuple(data["noise_ids"]),
                tuple(data["contributors"]),
                bool(data["noise"]),
            )
            for (col, row), data in grouped.get(frame_index, {}).items()
        ]
        pixels.sort(key=lambda pixel: (pixel.row, pixel.col))
        frames.append(
            Frame(
                detector_id,
                frame_index,
                model,
                start,
                end,
                trigger_id,
                simulation_event_id,
                tuple(pixels),
            )
        )
    return tuple(frames)


def frame_totals(frames: Iterable[Frame]) -> tuple[int, int]:
    """Return ``(assignments, contributor_links)`` summed over ``frames``."""
    assignments = 0
    contributor_links = 0
    for frame in frames:
        for pixel in frame.pixels:
            assignments += len(pixel.hit_ids)
            contributor_links += len(pixel.contributors)
    return assignments, contributor_links


def assemble_frame_set(
    spec: RunSpec, frames: Iterable[Frame], horizon_ps: int
) -> FrameSet:
    """Order frames by declared detector, then frame index, and total them."""
    order = {detector.id: position for position, detector in enumerate(spec.detectors)}
    ordered = []
    for frame in frames:
        if frame.detector not in order:
            raise ReadoutError(f"frame refers to unknown detector {frame.detector!r}")
        ordered.append(frame)
    ordered.sort(key=lambda frame: (order[frame.detector], frame.index))
    for earlier, later in zip(ordered, ordered[1:]):
        if (earlier.detector, earlier.index) == (later.detector, later.index):
            raise ReadoutError(
                f"duplicate frame {later.detector}[{later.index}] in frame set"
            )
    assignments, contributor_links = frame_totals(ordered)
    return FrameSet(
        "tracksim.frame-set.v1",
        readout_fingerprint(spec),
        horizon_ps,
        tuple(ordered),
        assignments,
        contributor_links,
    )


def build_readout(
    result: ResponseResult,
    spec: RunSpec,
    *,
    limits: Limits | None = None,
) -> FrameSet:
    """Group an immutable response result into per-detector frames/windows.

    This stage performs no scheduling, random draws, file I/O or input mutation.
    Its cumulative frame, assignment and conservative contributor budgets are
    checked before any frame or assignment collection is materialized.
    """
    if not isinstance(spec, RunSpec):
        raise ReadoutError("spec must be RunSpec")
    if not isinstance(result, ResponseResult):
        raise ReadoutError("result must be ResponseResult")
    effective_limits = _limits(spec, limits)
    plan = _preflight(result, spec, effective_limits)
    _, outcomes = _validate_records(result, spec, effective_limits)

    hits_by_detector: dict[str, list[PixelHit]] = {
        detector.id: [] for detector in spec.detectors
    }
    for hit in result.hits:
        hits_by_detector[hit.detector].append(hit)

    end_ps = max(result.horizon_ps, plan.origins[-1] + 1 if plan.origins else 0)
    frame_rows: list[Frame] = []
    for detector in spec.detectors:
        frame_rows.extend(
            assemble_frames(
                spec,
                detector.id,
                0,
                end_ps,
                timeline=result.timeline,
                hits=hits_by_detector[detector.id],
                outcomes=outcomes,
                horizon_ps=result.horizon_ps,
            )
        )
    actual_assignments, contributor_links = frame_totals(frame_rows)
    if actual_assignments != plan.assignment_count:
        raise ReadoutError(
            "materialized readout assignments differ from preflight count"
        )
    if len(frame_rows) != plan.frame_count:
        raise ReadoutError("materialized frame count differs from preflight count")
    if (
        contributor_links > plan.contributor_upper_bound
        or contributor_links > effective_limits.contributors
    ):
        raise ReadoutError(
            "materialized contributor links exceed preflight or configured budget"
        )
    return assemble_frame_set(spec, frame_rows, result.horizon_ps)
