# SPDX-License-Identifier: LGPL-3.0-or-later
"""Streaming transport and response using the public tracksim library API.

The caller owns the session barrier, wall clock, and client delivery.  This
module does not start or stop a run and never writes operator-visible logs.
"""
from tracksim.errors import ResponseError
from tracksim.fast_transport import iter_transport
from tracksim.readout import assemble_frames
from tracksim.specs import normalize_config
from tracksim.stream import CrossingDigest, ResponseStream, TriggerScheduler
from dataclasses import replace

from .trigger import TriggerDecision


class Transport:
    def __init__(self, settings, active_at=None, trigger=None, busy_ps=0):
        self.decision = TriggerDecision(trigger, settings['beam']['position_mm'][2], busy_ps) if trigger else None
        self.extra_crossings = []
        self.particle_truth = []
        self.spec = normalize_config(settings)
        self.active_at = active_at if active_at is not None else (lambda _time: True)
        self.scheduler = TriggerScheduler(self.spec)
        self.response = ResponseStream(self.spec, pin_horizon=True)
        self.probe = ResponseStream(self.spec, pin_horizon=True)
        self.events = iter(iter_transport(self.spec))
        self.digest = CrossingDigest()
        self.timeline = []
        self.hits = list(self.response.dark_hits or ())
        self.outcomes = {}
        self.crossings = []
        self.frame_cursor = {d.id: 0 for d in self.spec.detectors}
        self.closed = False
        self.limit_reason = None
        self.intersections = 0
        self.committed_through_ps = 0

    def advance_to(self, end_ps):
        """Consume scheduled events below end_ps and return final frames.

        A later call cannot change any returned frame. The response stream's
        finality watermark accounts for both the readout gate and response
        delays; it must not be replaced by the latest trigger time.
        """
        if self.closed or not isinstance(end_ps, int) or end_ps < 0:
            raise ValueError('invalid transport advance')
        while (self.response.events_consumed < min(self.spec.events, self.spec.limits.events) and
               self.scheduler.peek_origin_ps() < end_ps):
            event, trace = next(self.events)
            count = sum(len(p['mc']) for p in event['planes'].values())
            if self.intersections + count > self.spec.limits.intersections:
                self.limit_reason = 'intersections'
                break
            trigger = self.scheduler.next(event['simulation_event_id'])
            # A second stream receives each candidate first. A limit error
            # poisons that probe only; the live prefix remains closable.
            try:
                self.probe.feed([(event, trigger)])
            except ResponseError as exc:
                if 'limits.pixels=' not in str(exc):
                    raise
                self.limit_reason = 'pixels'
                break
            chunk = self.response.feed([(event, trigger)])
            self.digest.update(event)
            self.intersections += count
            enabled = self.active_at(trigger.origin_ps)
            filtered = {}
            permitted = set()
            for detector, plane in event['planes'].items():
                keep = []
                for index, crossing in enumerate(plane['mc']):
                    crossing_ps = trigger.origin_ps + int(crossing['global_time_ns'] * 1000)
                    if enabled:
                        self.committed_through_ps = max(self.committed_through_ps, crossing_ps + 1)
                    if enabled and self.active_at(crossing_ps):
                        keep.append(crossing)
                        permitted.add((detector, index))
                filtered[detector] = dict(plane, mc=keep)
            allowed_ids = {outcome.id for outcome in chunk.outcomes
                           if (outcome.detector, outcome.intersection_index) in permitted}
            self.hits.extend(hit for hit in chunk.hits if hit.response_id in allowed_ids)
            self.outcomes.update((outcome.id, outcome) for outcome in chunk.outcomes)
            if self.decision and enabled:
                decision = self.decision.decide(trigger.origin_ps, trace)
                decision['particle_id'] = trigger.physical_trigger_id
                self.particle_truth.append(decision)
                self.extra_crossings.append((trigger, filtered))
                if decision['trigger']:
                    emitted = replace(trigger, ordinal=decision['trigger_number'],
                                      physical_trigger_id=decision['trigger_number'])
                    self.timeline.append(emitted)
                    self.crossings.append((emitted, filtered, True))
            else:
                if self.decision is None: self.timeline.extend(chunk.timeline)
                self.crossings.append((trigger, filtered, enabled))
        # The next scheduled origin is a lower bound on all future origins.
        # Limit the promise to end_ps so changing polling granularity cannot
        # affect which future input remains legal.
        self.response.advance_to(end_ps)
        return self._final_frames()

    def _final_frames(self, final=False):
        frames = {}
        for detector in self.spec.detectors:
            lower = self.frame_cursor[detector.id]
            upper = (self.response.horizon_lower_bound_ps if final else
                     self.response.frames_final_before_ps(detector.id))
            upper = max(lower, upper)
            frames[detector.id] = assemble_frames(
                self.spec, detector.id, lower, upper,
                timeline=self.timeline, hits=self.hits, outcomes=self.outcomes,
                horizon_ps=self.response.horizon_lower_bound_ps)
            self.frame_cursor[detector.id] = upper
        return frames

    def close(self):
        if self.closed:
            raise RuntimeError('transport already closed')
        if not self.response.events_consumed:
            # Public tracksim deliberately does not allow closing an empty
            # response stream. There are no generated frames to finalize.
            self.closed = True
            return {d.id: () for d in self.spec.detectors}
        self.response.close(source_sha256=self.digest.hexdigest(), partial=True)
        self.closed = True
        return self._final_frames(final=True)

    def next_trigger_ps(self):
        """Origin of the next scheduled trigger, or None after the event limit."""
        if self.closed or self.response.events_consumed >= min(self.spec.events, self.spec.limits.events):
            return None
        return self.scheduler.peek_origin_ps()
