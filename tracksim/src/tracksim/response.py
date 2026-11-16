"""Trigger scheduling and empirical response over validated intersections."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from .contracts import validate_seed
from .errors import ResponseError
from .events import EventValidationError, validate_event
from .records import Outcome, PixelHit, ResponseResult, Trigger
from .response_models import ResponseRegistry, _poisson, _stable_normal, _stable_uniform
from .specs import DetectorSpec, RunSpec, response_fingerprint


PS_PER_SECOND = 1_000_000_000_000
PS_PER_NS = 1_000
MAX_TIMESTAMP_PS = (1 << 63) - 1
NEUTRAL_PDG = {12, 14, 16, 18, 22, 111, 130, 310, 311, 2112, 3122, 3322}

def trigger_number(ordinal: int) -> int:
    """Return the trigger number recorded for a zero-based trigger ordinal."""
    return ordinal


def _check_source_sha256(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ResponseError("source_sha256 must be a lowercase SHA256 hex digest")


def _validated_event(event: dict, spec: RunSpec, detector_ids: list[str]) -> dict:
    """Return the validated copy of one crossing event, or raise ResponseError."""
    try:
        row = validate_event(
            event, detector_ids, deposition_available=spec.deposition_available
        )
    except EventValidationError as exc:
        raise ResponseError(str(exc)) from exc
    primaries = [track for track in row["tracks"] if track["primary"]]
    if primaries[0]["pdg"] != 11:
        raise ResponseError("electron response requires primary PDG 11")
    if not spec.deposition_available and len(row["tracks"]) != 1:
        raise ResponseError("fast primary transport cannot declare secondary tracks")
    return row


def _intersection_count(row: dict) -> int:
    return sum(len(plane["mc"]) for plane in row["planes"].values())


def _intersection_limit_error(spec: RunSpec) -> ResponseError:
    return ResponseError(
        f"intersection count exceeds limits.intersections={spec.limits.intersections}"
    )


def _prepare_events(events: Sequence[dict], spec: RunSpec) -> tuple[dict, ...]:
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ResponseError("events must be a sequence of intersection records")
    if len(events) != spec.events:
        raise ResponseError(
            f"spec.events={spec.events} does not match {len(events)} input events"
        )
    if len(events) > spec.limits.events:
        raise ResponseError(f"event count exceeds limits.events={spec.limits.events}")
    detector_ids = [detector.id for detector in spec.detectors]
    validated = []
    intersection_count = 0
    seen_event_ids = set()
    for event in events:
        row = _validated_event(event, spec, detector_ids)
        event_id = row["simulation_event_id"]
        if event_id in seen_event_ids:
            raise ResponseError(f"duplicate simulation_event_id {event_id}")
        seen_event_ids.add(event_id)
        intersection_count += _intersection_count(row)
        if intersection_count > spec.limits.intersections:
            raise _intersection_limit_error(spec)
        validated.append(row)
    return tuple(validated)


class TriggerScheduler:
    """Incremental trigger origins: one exponential gap per trigger after the first.

    The origin of the next trigger is drawn at most once, so ``peek_origin_ps``
    may be called any number of times between calls to ``next``.
    """

    def __init__(self, spec: RunSpec) -> None:
        try:
            seed = validate_seed(spec.seed)
        except ValueError as exc:
            raise ResponseError(str(exc)) from exc
        rate_hz = spec.timeline.rate_hz
        if not math.isfinite(rate_hz) or rate_hz <= 0:
            raise ResponseError("timeline.rate_hz must be finite and positive")
        self._rate_hz = rate_hz
        self._rng = random.Random(seed)
        self._cursor = 0.0
        self._ordinal = 0
        self._pending: int | None = None
        self._failure: ResponseError | None = None

    @property
    def ordinal(self) -> int:
        """Ordinal of the next trigger to be issued."""
        return self._ordinal

    def peek_origin_ps(self) -> int:
        """Return the origin of the next trigger without consuming its ordinal."""
        if self._failure is not None:
            raise self._failure
        if self._pending is None:
            try:
                self._pending = self._draw()
            except ResponseError as exc:
                self._failure = exc
                raise
        return self._pending

    def _draw(self) -> int:
        if self._ordinal:
            self._cursor += self._rng.expovariate(self._rate_hz)
        cursor = self._cursor
        if not math.isfinite(cursor) or cursor < 0:
            raise ResponseError("scheduled trigger time is not finite")
        if cursor > MAX_TIMESTAMP_PS / PS_PER_SECOND:
            raise ResponseError("scheduled trigger exceeds supported timestamp range")
        origin_ps = round(cursor * PS_PER_SECOND)
        if origin_ps < 0 or origin_ps > MAX_TIMESTAMP_PS:
            raise ResponseError("scheduled trigger exceeds supported timestamp range")
        return origin_ps

    def next(self, simulation_event_id: int) -> Trigger:
        """Issue the next trigger for the given simulation event."""
        origin_ps = self.peek_origin_ps()
        trigger = Trigger(
            ordinal=self._ordinal,
            simulation_event_id=simulation_event_id,
            physical_trigger_id=trigger_number(self._ordinal),
            origin_ps=origin_ps,
        )
        self._ordinal += 1
        self._pending = None
        return trigger


def _schedule_validated(events: tuple[dict, ...], spec: RunSpec) -> tuple[Trigger, ...]:
    scheduler = TriggerScheduler(spec)
    return tuple(scheduler.next(event["simulation_event_id"]) for event in events)


def schedule(events: Sequence[dict], spec: RunSpec) -> tuple[Trigger, ...]:
    """Assign absolute trigger origins without changing event order."""
    validated = _prepare_events(events, spec)
    return _schedule_validated(validated, spec)


class _EventResponder:
    """Carried response state for a run: kernels, response IDs and running totals.

    Batch and stream generation both call ``respond`` once per event, in event
    order, so they share every numeric step.
    """

    def __init__(self, spec: RunSpec, registry: ResponseRegistry | None) -> None:
        self.spec = spec
        registry = registry or ResponseRegistry()
        self.compiled = {}
        self.provenance = "builtin"
        for detector in spec.detectors:
            self.compiled[detector.id] = registry.compile(detector)
            if not registry.is_builtin(detector.response.model):
                self.provenance = "external"
        self.detectors = {detector.id: detector for detector in spec.detectors}
        self.response_id = 0
        self.signal_pixel_count = 0
        self.latest_eligible_ps = 0

    def respond(
        self, event: dict, trigger: Trigger
    ) -> tuple[list[Outcome], list[PixelHit]]:
        """Return the outcomes and signal hits of one validated event."""
        spec = self.spec
        compiled = self.compiled
        detectors = self.detectors
        response_id = self.response_id
        signal_pixel_count = self.signal_pixel_count
        latest_eligible_ps = self.latest_eligible_ps
        outcomes: list[Outcome] = []
        signal_hits: list[PixelHit] = []
        event_id = event["simulation_event_id"]
        tracks = {row["track_id"]: row for row in event["tracks"]}
        raw_planes = event["planes"]
        for detector_id, raw_plane in raw_planes.items():
            detector = detectors[detector_id]
            response = detector.response
            kernel = compiled[detector_id]
            for intersection_index, mc in enumerate(raw_plane["mc"]):
                response_id += 1
                track_id = mc["track_id"]
                track = tracks[track_id]
                pdg = track["pdg"]
                deposited = mc["deposited_e"]
                reason = None
                if abs(pdg) in NEUTRAL_PDG:
                    reason = "neutral_or_unmeasured_pdg"
                elif deposited is not None and deposited <= 0:
                    reason = "nonpositive_deposit"

                kernel_result = None
                if reason is None:
                    try:
                        kernel_result = kernel(detector, mc["local_mm"])
                    except (ArithmeticError, TypeError, ValueError) as exc:
                        if isinstance(exc, ResponseError):
                            raise
                        raise ResponseError(
                            f"response kernel failed for {detector_id}: {exc}"
                        ) from exc
                    if kernel_result is None:
                        reason = "outside_matrix"

                accepted = False
                time_ps = None
                coordinates: tuple[tuple[int, int], ...] = ()
                residual = None
                if reason is None:
                    key = (
                        f"{spec.seed}:{spec.device_seed}:{detector_id}:"
                        f"{event_id}:{track_id}:{intersection_index}"
                    )
                    accepted = _stable_uniform("eff:" + key) < response.efficiency
                    jitter = response.jitter_ns * _stable_normal("time:" + key)
                    response_time_ns = (
                        mc["global_time_ns"] + response.response_delay_ns + jitter
                    )
                    if not math.isfinite(response_time_ns):
                        raise ResponseError(
                            f"response time is not finite at {detector_id}, event {event_id}, track {track_id}"
                        )
                    try:
                        time_ps = max(
                            0,
                            trigger.origin_ps + round(response_time_ns * PS_PER_NS),
                        )
                    except ArithmeticError as exc:
                        raise ResponseError(
                            "response time exceeds supported timestamp range "
                            f"at {detector_id}, event {event_id}, track {track_id}"
                        ) from exc
                    if time_ps > MAX_TIMESTAMP_PS:
                        raise ResponseError(
                            "response time exceeds supported timestamp range "
                            f"at {detector_id}, event {event_id}, track {track_id}"
                        )
                    latest_eligible_ps = max(latest_eligible_ps, time_ps)
                    if accepted:
                        assert kernel_result is not None
                        if (
                            not isinstance(kernel_result, tuple)
                            or len(kernel_result) != 2
                        ):
                            raise ResponseError(
                                f"response kernel for {detector_id} returned an invalid result"
                            )
                        raw_coordinates, raw_residual = kernel_result
                        if not isinstance(raw_coordinates, (tuple, list)):
                            raise ResponseError(
                                f"response kernel for {detector_id} returned invalid pixels"
                            )
                        coordinate_rows = []
                        for coordinate in raw_coordinates:
                            if (
                                not isinstance(coordinate, (tuple, list))
                                or len(coordinate) != 2
                                or any(
                                    isinstance(value, bool)
                                    or not isinstance(value, int)
                                    for value in coordinate
                                )
                            ):
                                raise ResponseError(
                                    f"response kernel for {detector_id} returned invalid pixel coordinates"
                                )
                            coordinate_rows.append((coordinate[0], coordinate[1]))
                        coordinates = tuple(coordinate_rows)
                        if not coordinates:
                            raise ResponseError(
                                f"response kernel for {detector_id} emitted no pixels"
                            )
                        if any(
                            not isinstance(col, int)
                            or isinstance(col, bool)
                            or not isinstance(row, int)
                            or isinstance(row, bool)
                            or col < 0
                            or row < 0
                            or col >= detector.pixels[0]
                            or row >= detector.pixels[1]
                            for col, row in coordinates
                        ):
                            raise ResponseError(
                                f"response kernel for {detector_id} emitted an out-of-range pixel"
                            )
                        if len(set(coordinates)) != len(coordinates):
                            raise ResponseError(
                                f"response kernel for {detector_id} emitted duplicate pixels"
                            )
                        if (
                            not isinstance(raw_residual, (tuple, list))
                            or len(raw_residual) != 2
                            or any(
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not math.isfinite(value)
                                for value in raw_residual
                            )
                        ):
                            raise ResponseError(
                                f"response kernel for {detector_id} returned invalid residual"
                            )
                        residual = (float(raw_residual[0]), float(raw_residual[1]))
                        if signal_pixel_count + len(coordinates) > spec.limits.pixels:
                            raise ResponseError(
                                "signal pixel count exceeds "
                                f"limits.pixels={spec.limits.pixels}"
                            )
                        for pixel_index, (col, row) in enumerate(coordinates):
                            signal_hits.append(
                                PixelHit(
                                    id=f"s:{response_id}:{pixel_index}",
                                    detector=detector_id,
                                    col=col,
                                    row=row,
                                    time_ps=time_ps,
                                    response_id=response_id,
                                    noise_id=None,
                                )
                            )
                        signal_pixel_count += len(coordinates)

                outcomes.append(
                    Outcome(
                        id=response_id,
                        simulation_event_id=event_id,
                        detector=detector_id,
                        track_id=track_id,
                        parent_track_id=track["parent_track_id"],
                        parent_unrecorded=track["parent_unrecorded"],
                        primary=track["primary"],
                        pdg=pdg,
                        intersection_index=intersection_index,
                        deposited_charge_e=deposited,
                        eligible=reason is None,
                        excluded_reason=reason,
                        accepted=accepted,
                        time_ps=time_ps,
                        pixel_coordinates=coordinates,
                        centroid_residual_um=residual,
                    )
                )
        self.response_id = response_id
        self.signal_pixel_count = signal_pixel_count
        self.latest_eligible_ps = latest_eligible_ps
        return outcomes, signal_hits


def _duration_ps(spec: RunSpec) -> int:
    duration_s = spec.timeline.duration_s
    if (
        isinstance(duration_s, bool)
        or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s)
        or duration_s < 0
        or duration_s > MAX_TIMESTAMP_PS / PS_PER_SECOND
    ):
        raise ResponseError("duration exceeds supported timestamp range")
    return round(duration_s * PS_PER_SECOND)


def _tail_padding_ps(spec: RunSpec) -> int:
    tail_padding_ps = spec.timeline.tail_padding_ps
    if isinstance(tail_padding_ps, bool) or not isinstance(tail_padding_ps, int):
        raise ResponseError("timeline.tail_padding_ps must be an integer")
    if tail_padding_ps < 1:
        raise ResponseError("timeline.tail_padding_ps must be positive")
    return tail_padding_ps


def _horizon_ps(spec: RunSpec, latest_eligible_ps: int | None) -> int:
    """Return the response horizon; ``None`` ignores the signal-time term."""
    duration_ps = _duration_ps(spec)
    tail_padding_ps = _tail_padding_ps(spec)
    signal_end_ps = 0 if latest_eligible_ps is None else latest_eligible_ps + tail_padding_ps
    horizon_ps = max(duration_ps, signal_end_ps, 1)
    if horizon_ps > MAX_TIMESTAMP_PS:
        raise ResponseError("response horizon exceeds supported timestamp range")
    return horizon_ps


def _dark_means(
    spec: RunSpec, horizon_ps: int
) -> tuple[list[tuple[DetectorSpec, float]], float]:
    """Return per-detector dark-noise means and their total, within the budget."""
    noise_means: list[tuple[DetectorSpec, float]] = []
    expected_dark_count = 0.0
    for detector in spec.detectors:
        rate = detector.response.dark_hit_rate_per_pixel_hz
        mean = (
            rate * detector.pixels[0] * detector.pixels[1] * horizon_ps / PS_PER_SECOND
        )
        if not math.isfinite(mean) or mean < 0:
            raise ResponseError(
                f"dark-noise mean for {detector.id} must be finite and nonnegative"
            )
        expected_dark_count += mean
        if expected_dark_count > spec.limits.dark_hits:
            raise ResponseError(
                f"expected dark-noise count {expected_dark_count:g} exceeds budget "
                f"{spec.limits.dark_hits}"
            )
        noise_means.append((detector, mean))
    return noise_means, expected_dark_count


def _check_expected_pixels(
    spec: RunSpec, signal_pixel_count: int, expected_dark_count: float
) -> None:
    if signal_pixel_count + expected_dark_count > spec.limits.pixels:
        raise ResponseError(
            "expected total pixel count exceeds " f"limits.pixels={spec.limits.pixels}"
        )


def _check_sampled_pixels(
    spec: RunSpec, signal_pixel_count: int, dark_count: int
) -> None:
    if signal_pixel_count + dark_count > spec.limits.pixels:
        raise ResponseError(
            "sampled total pixel count exceeds " f"limits.pixels={spec.limits.pixels}"
        )


def _dark_tail(
    spec: RunSpec, horizon_ps: int, signal_pixel_count: int
) -> tuple[tuple[PixelHit, ...], int]:
    """Draw dark-noise hits over ``[0, horizon_ps)`` for every detector.

    Fixed two-pass draw order: all detector counts first, then coordinates and
    times in declared detector order.
    """
    noise_rng = random.Random(f"{spec.seed}:{spec.device_seed}:dark-noise")
    noise_means, expected_dark_count = _dark_means(spec, horizon_ps)
    _check_expected_pixels(spec, signal_pixel_count, expected_dark_count)

    noise_counts = []
    dark_count = 0
    for detector, mean in noise_means:
        count = _poisson(noise_rng, mean, spec.limits.dark_hits)
        dark_count += count
        if dark_count > spec.limits.dark_hits:
            raise ResponseError(
                f"sampled dark-noise count exceeds limits.dark_hits={spec.limits.dark_hits}"
            )
        noise_counts.append((detector, count))
    _check_sampled_pixels(spec, signal_pixel_count, dark_count)

    noise_hits = []
    for detector, count in noise_counts:
        for draw_ordinal in range(count):
            col = noise_rng.randrange(detector.pixels[0])
            row = noise_rng.randrange(detector.pixels[1])
            time_ps = noise_rng.randrange(max(1, horizon_ps))
            noise_id = f"n:{detector.id}:{draw_ordinal}"
            noise_hits.append(
                PixelHit(
                    id=noise_id,
                    detector=detector.id,
                    col=col,
                    row=row,
                    time_ps=time_ps,
                    response_id=None,
                    noise_id=noise_id,
                )
            )
    return tuple(noise_hits), dark_count


def _response_schema(spec: RunSpec) -> str:
    return "tracksim.response.v2"


def generate_response(
    events: Sequence[dict],
    spec: RunSpec,
    *,
    source_sha256: str,
    registry: ResponseRegistry | None = None,
) -> ResponseResult:
    """Generate immutable response outcomes, signal hits and dark hits."""
    _check_source_sha256(source_sha256)
    validated = _prepare_events(events, spec)
    timeline = _schedule_validated(validated, spec)
    responder = _EventResponder(spec, registry)

    outcomes: list[Outcome] = []
    signal_hits: list[PixelHit] = []
    for event, trigger in zip(validated, timeline):
        event_outcomes, event_hits = responder.respond(event, trigger)
        outcomes.extend(event_outcomes)
        signal_hits.extend(event_hits)

    horizon_ps = _horizon_ps(spec, responder.latest_eligible_ps)
    noise_hits, dark_count = _dark_tail(spec, horizon_ps, responder.signal_pixel_count)

    return ResponseResult(
        schema=_response_schema(spec),
        source_sha256=source_sha256,
        response_spec_hash=response_fingerprint(spec),
        timeline=timeline,
        outcomes=tuple(outcomes),
        hits=tuple(signal_hits) + noise_hits,
        horizon_ps=horizon_ps,
        dark_hits=dark_count,
        provenance=responder.provenance,
    )
