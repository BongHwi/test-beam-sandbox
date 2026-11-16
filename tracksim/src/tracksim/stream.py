"""Incremental response generation with the same numeric core as ``generate_response``."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from . import response as _response
from .errors import ResponseError
from .fast_transport import _json_line
from .records import Outcome, PixelHit, ResponseResult, Trigger
from .response import (
    MAX_TIMESTAMP_PS,
    TriggerScheduler,
    _check_expected_pixels,
    _check_sampled_pixels,
    _check_source_sha256,
    _dark_means,
    _dark_tail,
    _duration_ps,
    _EventResponder,
    _horizon_ps,
    _intersection_count,
    _intersection_limit_error,
    _response_schema,
    _tail_padding_ps,
    _validated_event,
)
from .response_models import JITTER_NORMAL_BOUND, ResponseRegistry
from .specs import RunSpec, response_fingerprint, validate_capabilities


__all__ = [
    "CrossingDigest",
    "ResponseChunk",
    "ResponseStream",
    "ResponseTail",
    "TriggerScheduler",
    "assemble_result",
]

_FAILED_MESSAGE = "response stream failed earlier; create a new stream"


@dataclass(frozen=True, slots=True)
class ResponseChunk:
    """Records produced by one ``feed`` call, in batch order."""

    timeline: tuple[Trigger, ...]
    outcomes: tuple[Outcome, ...]
    hits: tuple[PixelHit, ...]


@dataclass(frozen=True, slots=True)
class ResponseTail:
    """Run-level records produced by ``close``; ``hits`` are the dark hits."""

    schema: str
    source_sha256: str
    response_spec_hash: str
    events: int
    horizon_ps: int
    dark_hits: int
    hits: tuple[PixelHit, ...]
    provenance: str


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class ResponseStream:
    """Feed events and triggers in order; collect the records of a whole run.

    ``feed`` returns outcomes and signal hits for the events it was given.
    Dark hits depend on the final horizon, so ``close`` produces them, unless
    ``pin_horizon`` fixes the horizon to the configured duration. A pinned
    stream draws its dark hits at construction and fails as soon as a response
    would end beyond that horizon.

    The stream keeps only counters and per-detector parameters, never
    per-event data. After any exception from ``feed`` or from the computation
    in ``close`` the stream is failed and every later call raises.
    """

    def __init__(
        self,
        spec: RunSpec,
        *,
        registry: ResponseRegistry | None = None,
        pin_horizon: bool = False,
    ) -> None:
        validate_capabilities(spec, full_pipeline=False)
        self._spec = spec
        self._responder = _EventResponder(spec, registry)
        self._detector_ids = [detector.id for detector in spec.detectors]
        self._detectors = {detector.id: detector for detector in spec.detectors}
        self._duration_ps = _duration_ps(spec)
        self._tail_padding_ps = _tail_padding_ps(spec)
        self._offsets = {
            detector.id: self._response_offset_ps(detector)
            for detector in spec.detectors
        }
        self._state = "open"
        self._events_consumed = 0
        self._last_event_id: int | None = None
        self._last_origin = 0
        self._floor = 0
        self._intersection_count = 0
        self._pinned = bool(pin_horizon)
        self._pinned_horizon_ps = 0
        self._expected_dark = 0.0
        self._dark: tuple[PixelHit, ...] | None = None
        self._dark_count = 0
        self._horizon_ps_final: int | None = None
        if self._pinned:
            horizon_ps = _horizon_ps(spec, None)
            self._pinned_horizon_ps = horizon_ps
            self._expected_dark = _dark_means(spec, horizon_ps)[1]
            self._dark, self._dark_count = _dark_tail(spec, horizon_ps, 0)

    @staticmethod
    def _response_offset_ps(detector) -> int:
        """Lower bound of a response time relative to its trigger origin, in ps.

        The jitter term is bounded by ``JITTER_NORMAL_BOUND`` standard deviations
        and the crossing time is nonnegative; one picosecond absorbs rounding.
        """
        response = detector.response
        low_ns = response.response_delay_ns - JITTER_NORMAL_BOUND * abs(
            response.jitter_ns
        )
        try:
            return math.floor(1000 * low_ns) - 1
        except (ArithmeticError, ValueError):
            return -MAX_TIMESTAMP_PS

    # state -----------------------------------------------------------------

    def _require_open(self) -> None:
        if self._state == "failed":
            raise ResponseError(_FAILED_MESSAGE)
        if self._state == "closed":
            raise ResponseError("response stream is closed")

    @property
    def events_consumed(self) -> int:
        return self._events_consumed

    @property
    def dark_hits(self) -> tuple[PixelHit, ...] | None:
        """Dark hits: known from construction when pinned, else after ``close``."""
        return self._dark

    @property
    def horizon_lower_bound_ps(self) -> int:
        """A horizon never exceeded by the final one; exact when pinned."""
        if self._state == "failed":
            raise ResponseError(_FAILED_MESSAGE)
        if self._horizon_ps_final is not None:
            return self._horizon_ps_final
        if self._pinned:
            return self._pinned_horizon_ps
        return max(
            self._duration_ps,
            self._responder.latest_eligible_ps + self._tail_padding_ps,
            1,
        )

    # feeding ---------------------------------------------------------------

    def feed(self, pairs: Iterable[tuple[Mapping, Trigger]]) -> ResponseChunk:
        """Process ``(event, trigger)`` pairs in order and return their records."""
        self._require_open()
        timeline: list[Trigger] = []
        outcomes: list[Outcome] = []
        hits: list[PixelHit] = []
        try:
            for pair in pairs:
                try:
                    event, trigger = pair
                except (TypeError, ValueError):
                    raise ResponseError("feed expects (event, trigger) pairs") from None
                event_outcomes, event_hits = self._feed_one(event, trigger)
                timeline.append(trigger)
                outcomes.extend(event_outcomes)
                hits.extend(event_hits)
        except BaseException:
            self._state = "failed"
            raise
        return ResponseChunk(tuple(timeline), tuple(outcomes), tuple(hits))

    def _feed_one(self, event, trigger):
        spec = self._spec
        row = _validated_event(event, spec, self._detector_ids)
        event_id = row["simulation_event_id"]
        if self._last_event_id is not None and event_id <= self._last_event_id:
            if event_id == self._last_event_id:
                raise ResponseError(f"duplicate simulation_event_id {event_id}")
            raise ResponseError(
                f"simulation_event_id {event_id} does not increase "
                f"after {self._last_event_id}"
            )
        ordinal = self._events_consumed
        if ordinal + 1 > spec.events:
            raise ResponseError(f"stream received more events than spec.events={spec.events}")
        if ordinal + 1 > spec.limits.events:
            raise ResponseError(f"event count exceeds limits.events={spec.limits.events}")
        crossings = self._intersection_count + _intersection_count(row)
        if crossings > spec.limits.intersections:
            raise _intersection_limit_error(spec)
        self._check_trigger(trigger, ordinal, event_id)

        outcomes, hits = self._responder.respond(row, trigger)

        if self._pinned:
            latest = self._responder.latest_eligible_ps
            if latest + self._tail_padding_ps > self._pinned_horizon_ps:
                raise ResponseError("pinned dark-noise horizon exceeded")
            signal = self._responder.signal_pixel_count
            _check_expected_pixels(spec, signal, self._expected_dark)
            _check_sampled_pixels(spec, signal, self._dark_count)

        self._events_consumed = ordinal + 1
        self._last_event_id = event_id
        self._last_origin = trigger.origin_ps
        self._floor = max(self._floor, trigger.origin_ps)
        self._intersection_count = crossings
        return outcomes, hits

    def _check_trigger(self, trigger, ordinal: int, event_id: int) -> None:
        if not isinstance(trigger, Trigger):
            raise ResponseError("trigger must be Trigger")
        if not _is_int(trigger.ordinal) or trigger.ordinal != ordinal:
            raise ResponseError(f"trigger ordinal must equal {ordinal}")
        if trigger.simulation_event_id != event_id:
            raise ResponseError(
                f"trigger simulation_event_id {trigger.simulation_event_id} "
                f"does not match event {event_id}"
            )
        expected_number = _response.trigger_number(ordinal)
        if (
            not _is_int(trigger.physical_trigger_id)
            or trigger.physical_trigger_id != expected_number
        ):
            raise ResponseError(f"trigger physical_trigger_id must equal {expected_number}")
        origin = trigger.origin_ps
        if not _is_int(origin) or not 0 <= origin <= MAX_TIMESTAMP_PS:
            raise ResponseError(
                "trigger origin_ps must be a supported nonnegative ps integer"
            )
        if origin < self._last_origin:
            raise ResponseError("trigger origins must be nondecreasing")
        if origin < self._floor:
            raise ResponseError("trigger origin is below the advance_to floor")

    def advance_to(self, origin_floor_ps: int) -> None:
        """Promise that no later trigger has an origin below ``origin_floor_ps``."""
        self._require_open()
        if not _is_int(origin_floor_ps) or not 0 <= origin_floor_ps <= MAX_TIMESTAMP_PS:
            raise ResponseError("origin floor must be a supported nonnegative ps integer")
        self._floor = max(self._floor, origin_floor_ps)

    # watermarks ------------------------------------------------------------

    def _detector(self, detector_id: str):
        try:
            return self._detectors[detector_id]
        except KeyError:
            raise ResponseError(f"unknown detector {detector_id!r}") from None

    def hit_watermark_ps(self, detector_id: str | None = None) -> int:
        """Lower bound for the time of every later signal outcome or hit.

        Without ``detector_id`` the minimum over all detectors is returned.
        """
        if self._state == "failed":
            raise ResponseError(_FAILED_MESSAGE)
        if detector_id is None:
            return min(self.hit_watermark_ps(name) for name in self._detector_ids)
        self._detector(detector_id)
        return max(0, self._floor + self._offsets[detector_id])

    def frames_final_before_ps(self, detector_id: str) -> int:
        """Frames of the detector that start below the returned time are final.

        Final means the frame exists in the batch readout and its content will
        not change, given every signal hit returned so far plus the dark hits
        (known when pinned or after ``close``).
        """
        if self._state == "failed":
            raise ResponseError(_FAILED_MESSAGE)
        detector = self._detector(detector_id)
        if self._state == "closed":
            return max(self._horizon_ps_final, self._last_origin + 1, self._floor)
        if detector.response.dark_hit_rate_per_pixel_hz > 0 and not self._pinned:
            return 0
        readout = detector.readout
        if readout.model == "triggered":
            extent = readout.gate_ps
        elif readout.model == "continuous":
            extent = readout.window_ps
        else:
            raise ResponseError(
                f"unsupported readout model {readout.model!r} for {detector_id}"
            )
        bound = min(self._floor, self.hit_watermark_ps(detector_id) - extent)
        if readout.model == "continuous":
            bound = min(bound, self.horizon_lower_bound_ps)
        return max(0, bound)

    # closing ---------------------------------------------------------------

    def close(self, *, source_sha256: str, partial: bool = False) -> ResponseTail:
        """End the run and return its run-level records.

        Argument errors leave the stream open. An error while computing the
        horizon or the dark hits fails the stream.
        """
        self._require_open()
        _check_source_sha256(source_sha256)
        count = self._events_consumed
        if count == 0:
            raise ResponseError("response stream has no events")
        spec = self._spec
        if not partial and count != spec.events:
            raise ResponseError(
                f"spec.events={spec.events} does not match {count} input events"
            )
        try:
            horizon_ps = _horizon_ps(spec, self._responder.latest_eligible_ps)
            if self._pinned:
                if horizon_ps != self._pinned_horizon_ps:
                    raise ResponseError("pinned dark-noise horizon exceeded")
                dark, dark_count = self._dark, self._dark_count
            else:
                dark, dark_count = _dark_tail(
                    spec, horizon_ps, self._responder.signal_pixel_count
                )
            fingerprint_spec = replace(spec, events=count) if partial else spec
            tail = ResponseTail(
                schema=_response_schema(spec),
                source_sha256=source_sha256,
                response_spec_hash=response_fingerprint(fingerprint_spec),
                events=count,
                horizon_ps=horizon_ps,
                dark_hits=dark_count,
                hits=dark,
                provenance=self._responder.provenance,
            )
        except BaseException:
            self._state = "failed"
            raise
        self._dark = dark
        self._dark_count = dark_count
        self._horizon_ps_final = horizon_ps
        self._state = "closed"
        return tail


class CrossingDigest:
    """SHA-256 over the serialized crossing events of a run."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()

    def update(self, event: Mapping) -> None:
        self._hash.update(_json_line(event))

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


def assemble_result(
    chunks: Iterable[ResponseChunk], tail: ResponseTail
) -> ResponseResult:
    """Join the chunks of a run and its tail into one ``ResponseResult``."""
    timeline: list[Trigger] = []
    outcomes: list[Outcome] = []
    hits: list[PixelHit] = []
    for chunk in chunks:
        timeline.extend(chunk.timeline)
        outcomes.extend(chunk.outcomes)
        hits.extend(chunk.hits)
    hits.extend(tail.hits)
    return ResponseResult(
        schema=tail.schema,
        source_sha256=tail.source_sha256,
        response_spec_hash=tail.response_spec_hash,
        timeline=tuple(timeline),
        outcomes=tuple(outcomes),
        hits=tuple(hits),
        horizon_ps=tail.horizon_ps,
        dark_hits=tail.dark_hits,
        provenance=tail.provenance,
    )
