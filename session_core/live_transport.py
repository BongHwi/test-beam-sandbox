# SPDX-License-Identifier: LGPL-3.0-or-later
"""Bounded, deterministic continuation of the public tracksim streaming API.

The trigger scheduler runs for the entire DAQ run. Response streams are
renewed at fixed event boundaries, without introducing a time gap or losing
hits that cross a boundary. Seeds are derived only from the configured source
seeds and segment index; polling and wall-clock timing do not affect them.
"""
import copy
from collections import deque
from dataclasses import replace
import hashlib

from tracksim.fast_transport import iter_transport
from tracksim.errors import ResponseError
from tracksim.readout import assemble_frames
from tracksim.records import Trigger
from tracksim.specs import normalize_config
from tracksim.stream import ResponseStream, TriggerScheduler

from .trigger import TriggerDecision


class LiveTransport:
    SEGMENT_EVENTS = 1024

    def __init__(self, settings, active_at, trigger=None, busy_ps=0):
        self.decision = TriggerDecision(trigger, settings['beam']['position_mm'][2], busy_ps) if trigger else None
        self.extra_crossings = []
        self.particle_truth = []
        self.settings = copy.deepcopy(settings)
        self.active_at = active_at
        n = min(self.SEGMENT_EVENTS, self.settings.get('limits', {}).get('events', self.SEGMENT_EVENTS))
        self.segment_events = n
        self.settings['events'] = n
        self.spec = normalize_config(self.settings)
        self.scheduler = TriggerScheduler(self.spec)
        self.next_global = 0
        self.carry = None
        self.pending_triggers = deque()
        self.segment = -1
        self.planned = []
        self.position = 0
        self.response = None
        self.probe = None
        self.events = None
        self.segment_start = 0
        self.next_segment_start = 0
        self.timeline = []
        self.hits = []
        self.outcomes = {}
        self.crossings = []
        self.frame_cursor = {d.id: 0 for d in self.spec.detectors}
        self.closed = False
        self.limit_reason = None
        self.committed_through_ps = 0
        self.current_ps = 0

    def _trigger(self):
        result = self.scheduler.next(self.next_global)
        self.next_global += 1
        return result

    def _take_trigger(self):
        return self.pending_triggers.popleft() if self.pending_triggers else self._trigger()

    def _derived_seed(self, name):
        source = f"{name}:{self.settings[name]}:{self.segment}".encode()
        return int.from_bytes(hashlib.sha256(source).digest()[:4], 'big') & 0x7fffffff

    def _start_segment(self):
        self.segment += 1
        first = self.carry if self.carry is not None else self._take_trigger()
        self.planned = [first] + [self._take_trigger() for _ in range(self.segment_events - 1)]
        self.carry = self._take_trigger()
        self.position = 0
        self.segment_start = first.origin_ps
        self.next_segment_start = self.carry.origin_ps
        settings = copy.deepcopy(self.settings)
        settings['seed'] = self._derived_seed('seed')
        settings['device_seed'] = self._derived_seed('device_seed')
        # Pin enough horizon for all delayed responses, then keep only this
        # segment's nonoverlapping dark-noise interval.
        max_delay = max((max(0, d['response'].get('response_delay_ns', 0) +
                             8.6 * d['response'].get('jitter_ns', 0))
                         for d in settings['detectors']), default=0)
        horizon_ps = max(self.next_segment_start - self.segment_start,
                         self.planned[-1].origin_ps - self.segment_start + int(max_delay * 1000))
        horizon_ps += settings['timeline']['tail_padding_ps'] + 1_000_000_000
        settings['timeline']['duration_s'] = (horizon_ps + 1000) / 1_000_000_000_000
        segment_spec = normalize_config(settings)
        self.response = ResponseStream(segment_spec, pin_horizon=True)
        self.probe = ResponseStream(segment_spec, pin_horizon=True)
        self.events = iter(iter_transport(segment_spec))
        offset = self.segment_start
        for hit in self.response.dark_hits or ():
            absolute = hit.time_ps + offset
            if absolute < self.next_segment_start:
                prefix = f'{self.segment}:'
                self.hits.append(replace(hit, id=prefix + hit.id, noise_id=prefix + hit.noise_id,
                                         time_ps=absolute))

    def _close_segment(self, probe_failed=False):
        if self.response is not None:
            if self.position:
                self.response.close(source_sha256='0' * 64, partial=True)
                if not probe_failed:self.probe.close(source_sha256='0' * 64, partial=True)
            self.response = self.probe = self.events = None

    def _rollover_limit(self):
        boundary = self.planned[self.position].origin_ps
        pending = self.planned[self.position:] + [self.carry]
        for trigger in reversed(pending):self.pending_triggers.appendleft(trigger)
        self.carry = None
        prefix = f'{self.segment}:'
        self.hits = [h for h in self.hits if not (h.response_id is None and
                     h.id.startswith(prefix) and h.time_ps >= boundary)]
        self._close_segment(probe_failed=True)

    def _consume(self, global_trigger):
        event, trace = next(self.events)
        local = Trigger(ordinal=self.position, simulation_event_id=self.position,
                        physical_trigger_id=self.position,
                        origin_ps=global_trigger.origin_ps - self.segment_start)
        self.probe.feed([(event, local)])
        chunk = self.response.feed([(event, local)])
        enabled = self.active_at(global_trigger.origin_ps)
        filtered = {}; permitted = set()
        for detector, plane in event['planes'].items():
            keep = []
            for index, crossing in enumerate(plane['mc']):
                crossing_ps = global_trigger.origin_ps + int(crossing['global_time_ns'] * 1000)
                if enabled:
                    self.committed_through_ps = max(self.committed_through_ps, crossing_ps + 1)
                if enabled and self.active_at(crossing_ps):
                    keep.append(crossing); permitted.add((detector, index))
            filtered[detector] = dict(plane, mc=keep)
        allowed = {o.id for o in chunk.outcomes if (o.detector, o.intersection_index) in permitted}
        base = self.segment * self.spec.limits.intersections
        event_base = global_trigger.ordinal - self.position
        for outcome in chunk.outcomes:
            shifted = replace(outcome, id=base + outcome.id,
                              simulation_event_id=event_base + outcome.simulation_event_id,
                              time_ps=None if outcome.time_ps is None else outcome.time_ps + self.segment_start)
            self.outcomes[shifted.id] = shifted
        for hit in chunk.hits:
            if hit.response_id in allowed:
                self.hits.append(replace(hit, id=f'{self.segment}:' + hit.id,
                                         response_id=base + hit.response_id,
                                         time_ps=hit.time_ps + self.segment_start))
        if self.decision and enabled:
            decision = self.decision.decide(global_trigger.origin_ps, trace)
            decision['particle_id'] = global_trigger.physical_trigger_id
            self.particle_truth.append(decision)
            self.extra_crossings.append((global_trigger, filtered))
            if decision['trigger']:
                emitted = replace(global_trigger, ordinal=decision['trigger_number'],
                                  physical_trigger_id=decision['trigger_number'])
                self.timeline.append(emitted)
                self.crossings.append((emitted, filtered, True))
        else:
            if enabled:self.timeline.append(global_trigger)
            self.crossings.append((global_trigger, filtered, enabled))
        self.position += 1
        if self.position == self.segment_events:
            self._close_segment()

    def next_trigger_ps(self):
        """Origin of the next trigger not yet consumed, if already planned."""
        if self.response is not None and self.position < len(self.planned):
            return self.planned[self.position].origin_ps
        if self.pending_triggers: return self.pending_triggers[0].origin_ps
        return self.carry.origin_ps if self.carry is not None else None

    def watermark(self, detector):
        d = next(d for d in self.spec.detectors if d.id == detector)
        if self.response is not None:
            return max(0, self.segment_start + self.response.frames_final_before_ps(detector))
        if self.carry is None:
            return 0
        low = int((d.response.response_delay_ns - 8.6 * abs(d.response.jitter_ns)) * 1000) - 1
        return max(0, min(self.current_ps, self.carry.origin_ps + low - d.readout.gate_ps))

    def advance_to(self, end_ps):
        if self.closed or not isinstance(end_ps, int) or end_ps < self.current_ps:
            raise ValueError('invalid continuous transport advance')
        while True:
            if self.response is None and (self.carry is None or self.carry.origin_ps < end_ps):
                self._start_segment()
            if self.response is None or self.planned[self.position].origin_ps >= end_ps:
                break
            try:self._consume(self.planned[self.position])
            except ResponseError as exc:
                if self.position and ('limits.pixels=' in str(exc) or 'limits.intersections=' in str(exc)):
                    self._rollover_limit()
                    continue
                raise
        self.current_ps = end_ps
        if self.response is not None:
            local = max(0, end_ps - self.segment_start)
            self.response.advance_to(local)
            self.probe.advance_to(local)
        return self._final_frames()

    def _final_frames(self, final=False):
        result = {}
        for detector in self.spec.detectors:
            lower = self.frame_cursor[detector.id]
            upper = self.current_ps if final else self.watermark(detector.id)
            upper = max(lower, upper)
            result[detector.id] = assemble_frames(self.spec, detector.id, lower, upper,
                timeline=self.timeline, hits=self.hits, outcomes=self.outcomes)
            self.frame_cursor[detector.id] = upper
        # No future frame starts before the smallest cursor. All earlier hits
        # and their unused outcomes can be released without changing a frame.
        lower = min(self.frame_cursor.values())
        self.timeline = [t for t in self.timeline if t.origin_ps >= lower]
        self.hits = [h for h in self.hits if h.time_ps >= lower]
        needed = {h.response_id for h in self.hits if h.response_id is not None}
        self.outcomes = {key: val for key, val in self.outcomes.items() if key in needed}
        return result

    def close(self):
        if self.closed:raise RuntimeError('transport already closed')
        self._close_segment()
        self.closed = True
        return self._final_frames(final=True)
