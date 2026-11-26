# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unix-domain service for one shared, clocked stimulus source.

The service observes client joins and stops; it is not a run controller.  All
operator-facing configuration is geometry only. Source conditions are supplied
through an environment variable and are never included in diagnostics.
"""
import argparse
from collections import deque
import copy
import json
import os
from pathlib import Path
import socketserver
import threading
import time
import uuid

from integration.trigger_logic import clock_cycle
from .beam import Beam
from .live_transport import LiveTransport
from .transport import Transport


class Session:
    def __init__(self, geometry, private, beam, truth_dir=None, event_log=None):
        self.geometry = geometry
        self.continuous = bool(geometry.get('continuous', False))
        if self.continuous and geometry.get('clock', 'paced') != 'paced':
            raise ValueError('continuous sessions require a paced clock')
        self.private = private
        self.beam = beam
        self.truth_dir = Path(truth_dir) if truth_dir else None
        self.event_log = Path(event_log) if event_log else None
        self.expected = {p['serial']: p for p in geometry['participants']}
        if len(self.expected) != len(geometry['participants']):
            raise ValueError('duplicate participant')
        self.lock = threading.RLock()
        self.serial_number = 0
        self.instance = uuid.uuid4().hex
        self.clients = {}
        self.transport = None
        self.started = None
        self.clock_ps = 0
        self.last_completed_clock_ps = 0
        self.committed_ps = 0
        self.boundary = None
        self.frames = {}
        self.crossings = {}
        self.active_ids = []
        self.active_base = 0
        self.crossing_cursor = 0
        self.exposed = -1
        self.source_finished = False
        self.source_reason = None
        self.join_deadline = None
        self.missing = set()
        self.lead_logged = False
        self.truth_spool = None
        self.particle_spool = None
        self.extra_crossings = []
        self.extra_base = 0
        # Optional external sink acknowledgement, independent of run control.
        self.sink_epoch = None
        self.sink_counts = {}
        self.board_logic = None
        self.trigger_counts = [0] * 256
        self.hist_next = 0
        self.hist_previous = 0
        self.hist_pulses = deque()
        self.board_enabled = bool(geometry.get('trigger_board', False))
        self.board_log = []
        self.board_base = 0

    def _record(self, kind, serial):
        if self.event_log is not None:
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log.open('a', encoding='utf-8') as out:
                out.write(json.dumps({'kind': kind, 'serial': serial,
                                      'session_run': self.serial_number + (self.transport is None),
                                      'monotonic_ns': time.monotonic_ns()}) + '\n')

    def _active(self):
        return [c for c in self.clients.values() if not c['lost']]

    def _lose(self, serial, reason):
        client = self.clients.get(serial)
        if client is not None and not client['lost'] and not client['released']:
            client['lost'] = True
            client['released'] = True
            self.missing.add(serial)
            self._record(reason, serial)

    def disconnected(self, serial, original):
        with self.lock:
            if self.clients.get(serial) is original:
                self._lose(serial, 'disconnected')

    def _expire(self):
        now = time.monotonic()
        if self.transport is None and self.clients and now >= self.join_deadline:
            for serial in sorted(set(self.expected) - set(self.clients)):
                self.missing.add(serial)
                self._record('join_timeout', serial)
            self._start()
        if self.transport is not None and self.boundary is None:
            for serial, client in self.clients.items():
                if not client['lost'] and not client['released'] and now - client['last_seen'] >= 10:
                    self._lose(serial, 'response_timeout')

    def sink_message(self, req):
        epoch, sinks = req.get('epoch'), req.get('sinks')
        if not isinstance(epoch, str) or not epoch or len(epoch) > 128:
            raise ValueError('invalid sink epoch')
        if not isinstance(sinks, list) or not sinks or any(not isinstance(x, str) or not x for x in sinks) or len(set(sinks)) != len(sinks):
            raise ValueError('invalid sinks')
        operation = req['op']
        if operation == 'sink_begin':
            if self.sink_epoch != epoch:
                if self.clients and not all(c['released'] for c in self.clients.values()):
                    raise ValueError('previous participants have not released')
                self.sink_epoch = epoch
                self.sink_counts = {name: None for name in sinks}
        elif operation == 'sink_progress':
            if self.sink_epoch is None:
                # A source restarted between runs may first see a progress update.
                self.sink_epoch = epoch
                self.sink_counts = {name: None for name in sinks}
            if self.sink_epoch != epoch or set(self.sink_counts) != set(sinks):
                raise ValueError('unexpected sink epoch')
            sink, count, initialized = req.get('sink'), req.get('events'), req.get('initialized')
            if sink not in self.sink_counts or type(count) is not int or count < 0 or type(initialized) is not bool:
                raise ValueError('invalid sink progress')
            if initialized:
                prior = self.sink_counts[sink]
                self.sink_counts[sink] = count if prior is None else max(prior, count)
        return {'kind': operation}

    def join(self, req):
        serial = req['serial']
        if self.clients and all(c['released'] for c in self.clients.values()):
            self._histogram_until(self.clock_ps)
            self.hist_next = 0
            self.hist_pulses.clear()
            self.clients = {}
            self.board_log = []
            self.board_base = 0
            self.transport = None
            self.started = None
            self.last_completed_clock_ps = self.clock_ps
            self.clock_ps = 0
            self.committed_ps = 0
            self.boundary = None
            self.exposed = -1
            self.source_finished = False
            self.source_reason = None
            self.join_deadline = None
            self.missing = set()
            self.lead_logged = False
        if self.clients:
            self._expire()
        if serial not in self.expected or serial in self.clients or serial in self.missing or self.transport:
            raise ValueError('unknown or duplicate participant')
        participant = self.expected[serial]
        detector = next(d for d in self.geometry['settings']['detectors']
                        if d['id'] == participant['detector'])
        if req['pixels'] != detector['pixels']:
            raise ValueError('participant geometry mismatch')
        form = req.get('form', 'frame')
        if form not in ('frame', 'crossing'):
            raise ValueError('invalid request form')
        drain = max(int(req.get('drain_ps', 0)), int(req.get('response', {}).get('gate_ps', 0)))
        if not 0 <= drain <= 90_000_000_000:
            raise ValueError('requested readout guard exceeds session limit')
        sink_required = req.get('sink_required', False)
        if type(sink_required) is not bool:
            raise ValueError('invalid sink acknowledgement requirement')
        self.clients[serial] = dict(form=form, response=copy.deepcopy(req.get('response', {})),
                                    run=req.get('run'), next=0, ack=-1, processed_ps=0,
                                    stopped=False, released=False, drain_ps=drain, tail_next=None,
                                    sink_required=sink_required, lost=False, extra_next=0, last_seen=time.monotonic())
        if self.join_deadline is None:
            self.join_deadline = time.monotonic() + 10
        if len(self.clients) == len(self.expected):
            self._start()
        reply = {'kind': 'joined'}
        if self.continuous: reply['continuous'] = True
        return reply

    def _start(self):
        settings = copy.deepcopy(self.geometry['settings'])
        for key in ('seed', 'device_seed'):
            settings[key] = self.private[key]
        for key in ('position_mm', 'direction', 'phase_space_covariance'):
            settings['beam'][key] = self.private['beam'][key]
        settings['timeline']['rate_hz'] = self.private['rate_hz']
        by_detector = {self.expected[s]['detector']: c for s, c in self.clients.items()}
        for detector in settings['detectors']:
            client = by_detector.get(detector['id'])
            response = client['response'] if client else {}
            detector['response'] = {
                'model': 'boundary_cluster_mean',
                'cluster_mean': response.get('cluster_mean', 1.0),
                'efficiency': response.get('efficiency', 0.0),
                'response_delay_ns': response.get('response_delay_ns', 0),
                'jitter_ns': response.get('jitter_ns', 0),
                'dark_hit_rate_per_pixel_hz': response.get('dark_hit_rate_per_pixel_hz', 0),
            }
            detector['readout'] = {'model': 'triggered',
                                   'gate_ps': response.get('gate_ps', 1)}
        trigger = copy.deepcopy(self.geometry.get('trigger'))
        source = 'board' if trigger and self.board_logic is not None else 'setup'
        if trigger and self.board_logic is not None:
            trigger.update(self.board_logic)
        busy_ps = max((c['response'].get('gate_ps', 0) for c in self._active()), default=0)
        self.transport = (LiveTransport(settings, self.beam.active_at, trigger, busy_ps) if self.continuous
                          else Transport(settings, self.beam.active_at, trigger, busy_ps))
        self.extra_crossings = []
        self.extra_base = 0
        if trigger and self.event_log is not None:
            with self.event_log.open('a', encoding='utf-8') as out:
                out.write(json.dumps({'kind':'trigger_conditions', 'trg':trigger['trg'],
                                      'veto':trigger['veto'], 'busy_ps':busy_ps, 'source':source}) + '\n')
        self.serial_number += 1
        self.beam.begin(self.serial_number)
        self.started = time.monotonic_ns()
        for client in self.clients.values():
            client['last_seen'] = time.monotonic()
        self.frames = {d.id: {} for d in self.transport.spec.detectors}
        self.crossings = {}
        self.active_ids = []
        self.active_base = 0
        self.crossing_cursor = 0
        if self.continuous and self.truth_dir:
            self.truth_dir.mkdir(parents=True, exist_ok=True)
            self.truth_spool = (self.truth_dir / ('.crossings-' + self.instance + '.jsonl')).open('w+', encoding='utf-8')
        if self.truth_dir and trigger:
            self.truth_dir.mkdir(parents=True, exist_ok=True)
            self.particle_spool = (self.truth_dir / ('.particles-' + self.instance + '.jsonl')).open('w+', encoding='utf-8')

    def _active_id(self, index):
        return self.active_ids[index - self.active_base]

    def _store_crossings(self):
        self.extra_crossings.extend(self.transport.extra_crossings)
        self.transport.extra_crossings.clear()
        fired_masks = {}
        for particle in self.transport.particle_truth:
            mask = sum(1 << channel for channel in particle['fired'])
            if particle['trigger']:
                fired_masks[particle['trigger_number']] = mask
            if mask:
                cycle = clock_cycle(particle['time_ps'])
                if self.hist_pulses and self.hist_pulses[-1][0] == cycle:
                    prior, bits = self.hist_pulses.pop()
                    self.hist_pulses.append((prior, bits | mask))
                else:
                    self.hist_pulses.append((cycle, mask))
            if self.particle_spool:
                self.particle_spool.write(json.dumps(particle, separators=(',', ':')) + '\n')
        self.transport.particle_truth.clear()
        for trigger, planes, enabled in self.transport.crossings[self.crossing_cursor:]:
            if enabled:
                if self.board_enabled: self.board_log.append((trigger.physical_trigger_id, trigger.origin_ps,
                                       fired_masks.get(trigger.physical_trigger_id, 0)))
                self.crossings[trigger.physical_trigger_id] = (trigger, planes)
                self.active_ids.append(trigger.physical_trigger_id)
                if self.truth_spool:
                    self.truth_spool.write(json.dumps({'trigger':trigger.physical_trigger_id,
                        'time_ps':trigger.origin_ps,'planes':planes},separators=(',',':'))+'\n')
        if self.continuous:
            self.transport.crossings.clear()
            self.crossing_cursor = 0
        else:
            self.crossing_cursor = len(self.transport.crossings)

    def _histogram_until(self, time_ps):
        """Count completed clock cycles, skipping stretches of idle input."""
        target = clock_cycle(time_ps)
        def idle_until(end):
            if self.hist_next < end and self.hist_previous:
                self.trigger_counts[self.hist_previous << 4] += 1
                self.hist_previous = 0
                self.hist_next += 1
            if self.hist_next < end:
                self.trigger_counts[0] += end - self.hist_next
                self.hist_next = end
        while self.hist_pulses and self.hist_pulses[0][0] < target:
            cycle, mask = self.hist_pulses.popleft()
            if cycle < self.hist_next:
                raise RuntimeError('trigger pulse before the histogram watermark')
            idle_until(cycle)
            self.trigger_counts[(self.hist_previous << 4) | mask] += 1
            self.hist_previous = mask
            self.hist_next = cycle + 1
        idle_until(target)

    def _prune(self):
        if not self.continuous or self.boundary is not None or not self._active():
            return
        through = min(min(c['ack'] for c in self._active()), self.exposed)
        count = max(0, min(len(self.active_ids), through - self.active_base))
        for number in self.active_ids[:count]:
            self.crossings.pop(number, None)
            for frames in self.frames.values(): frames.pop(number, None)
        if count:
            del self.active_ids[:count]
            self.active_base += count
        crossing_clients = [c for c in self._active() if c['form'] == 'crossing']
        through_extra = min((c['extra_next'] for c in crossing_clients), default=self.extra_base + len(self.extra_crossings))
        extra_count = max(0, through_extra - self.extra_base)
        if extra_count:
            del self.extra_crossings[:extra_count]
            self.extra_base += extra_count
        self.beam.compact(self.clock_ps)

    def _advance(self):
        if not self.transport or self.boundary is not None or not self._active():
            return
        elapsed_ps = (time.monotonic_ns() - self.started) * 1000
        if self.geometry.get('clock', 'paced') == 'fast':
            elapsed_ps = 10**30
        limit_ps = int(self.geometry['end_ns']) * 1000 if not self.continuous else 2**63-1
        slowest_ps = min(c['processed_ps'] for c in self._active())
        guard_ps = max(c['drain_ps'] for c in self._active())
        lead_ps = 200_000_000_000 - max(guard_ps, 10_000_000_000) - 1_000_000_000
        end_ps = min(elapsed_ps, limit_ps, slowest_ps + lead_ps)
        end_ps = max(self.clock_ps, end_ps)
        self.beam.advance(end_ps)
        frames = self.transport.advance_to(end_ps)
        for detector, rows in frames.items():
            self.frames[detector].update((frame.trigger_id, frame) for frame in rows)
        self._store_crossings()
        self.clock_ps = end_ps
        self.committed_ps = max(self.committed_ps, end_ps, self.transport.committed_through_ps)
        if not self.lead_logged and self.committed_ps - slowest_ps >= 180_000_000_000:
            self.lead_logged = True
            if self.event_log is not None:
                with self.event_log.open('a', encoding='utf-8') as out:
                    out.write(json.dumps({'kind': 'lead', 'session_run': self.serial_number,
                                          'committed_ps': self.committed_ps,
                                          'slowest_ps': slowest_ps,
                                          'monotonic_ns': time.monotonic_ns()}) + '\n')
        event_limit = min(self.transport.spec.events, self.transport.spec.limits.events)
        if not self.continuous and (end_ps >= limit_ps or self.transport.response.events_consumed >= event_limit or self.transport.limit_reason):
            self.source_reason = (self.transport.limit_reason or
                                  ('events' if self.transport.response.events_consumed >= event_limit else 'end_time'))
            self._finish_source()

    def _finish_source(self):
        if self.boundary is not None:
            return
        self.source_finished = True
        self.boundary = self.active_base + len(self.active_ids) - 1
        self.beam.advance(self.committed_ps)
        self.beam.end(self.committed_ps)
        if self.boundary >= 0:
            last = self.crossings[self._active_id(self.boundary)][0].origin_ps
            drain = max(c['drain_ps'] for c in self._active())
            self.clock_ps = max(self.clock_ps, ((last + drain + 1999) // 1000) * 1000)
        self.committed_ps = max(self.committed_ps, self.clock_ps)
        frames = self.transport.close()
        for detector, rows in frames.items():
            self.frames[detector].update((frame.trigger_id, frame) for frame in rows)
        self._write_truth()

    def _write_truth(self):
        if self.truth_dir is None:
            return
        self.truth_dir.mkdir(parents=True, exist_ok=True)
        runs = sorted({c['run'] for c in self.clients.values() if c['run'] is not None})
        if self.continuous:
            limit = self._active_id(self.boundary) if self.boundary >= self.active_base else -1
            target = self.truth_dir / ('run-%s-%06d.json' % (self.instance, self.serial_number))
            self.truth_spool.flush(); self.truth_spool.seek(0)
            header = {'session_run':self.serial_number, 'eudaq_runs':runs,
                      'end_ps':self.clock_ps, 'join_order':list(self.clients)}
            with target.open('w', encoding='utf-8') as output:
                output.write(json.dumps(header,separators=(',',':'))[:-1] + ',"crossings":[')
                first = True
                for line in self.truth_spool:
                    item=json.loads(line)
                    if item['trigger'] > limit: break
                    if not first: output.write(',')
                    output.write(json.dumps(item,separators=(',',':'))); first=False
                output.write(']')
                if self.particle_spool:
                    self.particle_spool.flush(); self.particle_spool.seek(0)
                    output.write(',"particles":[')
                    first = True
                    for line in self.particle_spool:
                        if not first: output.write(',')
                        output.write(line.strip()); first = False
                    output.write(']')
                output.write('}')
            path=Path(self.truth_spool.name);self.truth_spool.close();self.truth_spool=None;path.unlink()
            if self.particle_spool:
                path=Path(self.particle_spool.name);self.particle_spool.close();self.particle_spool=None;path.unlink()
            return
        data = {'session_run': self.serial_number, 'eudaq_runs': runs,
                'end_ps': self.clock_ps, 'join_order': list(self.clients),
                'crossings': [{'trigger': number, 'time_ps': self.crossings[number][0].origin_ps,
                               'planes': self.crossings[number][1]}
                              for number in self.active_ids[:self.boundary + 1]]}
        if self.particle_spool:
            self.particle_spool.flush(); self.particle_spool.seek(0)
            data['particles'] = [json.loads(line) for line in self.particle_spool]
            path=Path(self.particle_spool.name);self.particle_spool.close();self.particle_spool=None;path.unlink()
        (self.truth_dir / ('run-%s-%06d.json' % (self.instance, self.serial_number))).write_text(json.dumps(data))

    def stop(self, serial):
        self.clients[serial]['stopped'] = True
        if self.transport and self.boundary is None:
            self.boundary = self.exposed
            self.beam.advance(self.committed_ps)
            if self.boundary >= 0:
                last = self.crossings[self._active_id(self.boundary)][0].origin_ps
                drain = max(c['drain_ps'] for c in self._active())
                target = max(self.clock_ps, last + drain + 1000)
                if not self.continuous: target = min(int(self.geometry['end_ns']) * 1000, target)
                target = ((target + 999) // 1000) * 1000
                # Pre-scheduled beam changes remain part of the run through
                # its drain interval. Later manual requests affect the next run.
                self.beam.advance(max(self.committed_ps, target))
                self.beam.end(max(self.committed_ps, target))
                if target > self.clock_ps:
                    rows = self.transport.advance_to(target)
                    for detector, frames in rows.items():
                        self.frames[detector].update((f.trigger_id, f) for f in frames)
                    self._store_crossings()
                    self.clock_ps = target
                self.clock_ps = max(self.clock_ps, ((last + drain + 1999) // 1000) * 1000)
            else:
                self.beam.end(self.committed_ps)
            self.committed_ps = max(self.committed_ps, self.clock_ps)
            frames = self.transport.close()
            for detector, rows in frames.items():
                self.frames[detector].update((f.trigger_id, f) for f in rows)
            self._write_truth()
        return {'kind': 'stopping', 'time_ps': self.clock_ps}

    def next(self, serial, req):
        client = self.clients[serial]
        ack = req.get('ack')
        if ack is not None:
            index = client['ack'] + 1
            if index >= client['next'] or index >= self.active_base + len(self.active_ids) or index < self.active_base or self._active_id(index) != ack:
                raise ValueError('invalid acknowledgement')
            client['ack'] = index
            client['processed_ps'] = max(client['processed_ps'], self.crossings[ack][0].origin_ps)
        if 'processed_ps' in req:
            processed = req['processed_ps']
            if not isinstance(processed, int) or not client['processed_ps'] <= processed <= self.clock_ps:
                raise ValueError('invalid processed time')
            client['processed_ps'] = processed
        self._advance()
        if not self.transport:
            return {'kind': 'wait', 'committed_ps': self.committed_ps}
        index = client['next']
        if client['form'] == 'crossing':
            extra_index = client['extra_next'] - self.extra_base
            if 0 <= extra_index < len(self.extra_crossings):
                extra_trigger, extra_planes = self.extra_crossings[extra_index]
                next_id = self._active_id(index) if self.active_base <= index < self.active_base + len(self.active_ids) else None
                next_time = self.crossings[next_id][0].origin_ps if next_id is not None else None
                if next_time is None or extra_trigger.origin_ps <= next_time:
                    client['extra_next'] += 1
                    detector = self.expected[serial]['detector']
                    spec = next(d for d in self.transport.spec.detectors if d.id == detector)
                    crossings = [{'col': mc['local_mm'][0] * 1000 / spec.pitch_um[0] + 0.5,
                                  'row': mc['local_mm'][1] * 1000 / spec.pitch_um[1] + 0.5,
                                  'time_ps': extra_trigger.origin_ps + int(mc['global_time_ns'] * 1000)}
                                 for mc in extra_planes[detector]['mc']]
                    return {'kind':'crossing_tail', 'crossing_id':extra_trigger.physical_trigger_id,
                            'crossings':crossings, 'committed_ps':self.committed_ps}
        if self.boundary is not None and index > self.boundary:
            # Triggers after the frozen boundary are never emitted as events.
            # Their physical crossings still affect complete readout windows
            # within the final drain interval.
            if client['tail_next'] is None:
                client['tail_next'] = self.boundary + 1
            if client['form'] == 'crossing':
                while client['tail_next'] < self.active_base + len(self.active_ids):
                    number = self._active_id(client['tail_next'])
                    client['tail_next'] += 1
                    detector = self.expected[serial]['detector']
                    crossings = [] if self.transport.decision else self._crossing_payload(detector, number)
                    crossings = [item for item in crossings if item['time_ps'] < self.clock_ps]
                    if crossings:
                        return {'kind': 'crossing_tail', 'crossing_id': number,
                                'crossings': crossings, 'committed_ps': self.committed_ps}
            else:
                client['tail_next'] = self.active_base + len(self.active_ids)
            tails_done = all(c['form'] != 'crossing' or
                             (c['tail_next'] is not None and c['tail_next'] >= self.active_base + len(self.active_ids))
                             for c in self._active())
            require_sink = any(c['sink_required'] for c in self._active())
            sink_done = (not require_sink or
                         (bool(self.sink_counts) and all(value is not None and value >= self.boundary + 1
                                                         for value in self.sink_counts.values())))
            if tails_done and sink_done and all(c['ack'] >= self.boundary for c in self._active()):
                client['released'] = True
                return {'kind': 'finished', 'time_ps': self.clock_ps,
                        'source_finished': self.source_finished, 'limit_reason': self.source_reason,
                        'committed_ps': self.committed_ps}
            return {'kind': 'wait', 'watermark_ps': self.clock_ps, 'committed_ps': self.committed_ps}
        detector = self.expected[serial]['detector']
        number = self._active_id(index) if self.active_base <= index < self.active_base + len(self.active_ids) else None
        frame = self.frames[detector].get(number)
        item = self.crossings.get(number)
        if frame is None or item is None:
            if client['form'] == 'frame' and client['ack'] == client['next'] - 1:
                client['processed_ps'] = self.clock_ps
            if self.boundary is None:
                ready = (self.transport.watermark(detector) if self.continuous
                         else self.transport.response.frames_final_before_ps(detector))
                watermark = min(self.clock_ps, ready)
            else: watermark = self.clock_ps
            return {'kind': 'wait', 'watermark_ps': max(0, watermark), 'committed_ps': self.committed_ps,
                    'retry_ps': self._retry_ps(number is not None)}
        trigger, planes = item
        result = {'kind': 'event', 'trigger': number, 'time_ps': trigger.origin_ps,
                  'committed_ps': self.committed_ps}
        if client['form'] == 'frame':
            result['pixels'] = [[p.col, p.row] for p in frame.pixels]
        else:
            result['crossings'] = [] if self.transport.decision else self._crossing_payload(detector, number)
        client['next'] += 1
        self.exposed = max(self.exposed, index)
        return result

    def _retry_ps(self, pending):
        """Suggest when a waiting reader can expect its next event.

        A trigger already issued only waits for its frame to become final, so
        it is asked again soon. Otherwise the reader waits for the next
        scheduled trigger while the beam is on. The hint never exceeds the
        previous fixed polling interval and does not affect any data.
        """
        idle = 50_000_000_000
        if pending: return 1_000_000_000
        upcoming = self.transport.next_trigger_ps() if self.transport else None
        if upcoming is None or not self.beam.active_at(upcoming): return idle
        return max(1_000_000_000, min(idle, upcoming - self.clock_ps + 1_000_000_000))

    def _crossing_payload(self, detector, number):
        trigger, planes = self.crossings[number]
        spec = next(d for d in self.transport.spec.detectors if d.id == detector)
        return [
            {'col': mc['local_mm'][0] * 1000 / spec.pitch_um[0] + 0.5,
             'row': mc['local_mm'][1] * 1000 / spec.pitch_um[1] + 0.5,
             'time_ps': trigger.origin_ps + int(mc['global_time_ns'] * 1000)}
            for mc in planes[detector]['mc']]

    def board_next(self, req):
        """Accepted physics triggers of one run for a read-only trigger board stream.

        The board is not a participant: it only reads the triggers the session has
        issued, with the scintillator pattern of each, up to the session clock.
        """
        run, cursor = req.get('run'), req.get('cursor', 0)
        if type(run) is not int or type(cursor) is not int or cursor < 0:
            raise ValueError('invalid board request')
        mine = [c for c in self.clients.values() if c['run'] == run]
        if self.transport is None or not mine:
            return {'kind': 'wait', 'watermark_ps': 0}
        self._advance()
        if not self.board_enabled or cursor < self.board_base:
            raise ValueError('board not enabled or records already released')
        # The board is the only reader, so what it has read can be released.
        del self.board_log[:cursor - self.board_base]
        self.board_base = cursor
        records = self.board_log[:512]
        rest = self.board_log[len(records):]
        # A reply that does not hold every record must not promise the clock: the board closes
        # its frames up to the watermark, so it stops before the first record it has not got.
        watermark = min([self.clock_ps] + [r[1] for r in rest])
        # The run is over for the board once the boundary is frozen and everything is read,
        # whatever the msensors still do.
        finished = self.boundary is not None and not rest
        return {'kind': 'board', 'records': [list(r) for r in records], 'next': cursor + len(records),
                'watermark_ps': watermark, 'finished': finished}

    def request(self, req):
        with self.lock:
            op = req.get('op')
            if op == 'trigger_logic':
                from integration.trigger_logic import parse
                trigger = self.geometry.get('trigger')
                if not trigger: raise ValueError('the setup has no scintillators')
                channels = {plate['channel'] for plate in trigger['scintillators']}
                for key in ('trg', 'veto'):
                    text = req.get(key)
                    try:
                        expr = parse(text, allow_empty=key == 'veto')
                        if expr.channels - channels: raise ValueError('unknown channel')
                    except ValueError as exc:
                        raise ValueError(f'[trigger] {key} = {text}: not accepted; expected a valid expression using existing channels ({exc})') from exc
                self.board_logic = {'trg':req['trg'], 'veto':req['veto']}
                return {'kind':'trigger_logic'}
            if op == 'trigger_counts':
                if self.transport is not None: self._histogram_until(self.clock_ps)
                result = {'kind':'trigger_counts', 'clock_ps':self.clock_ps if self.transport is not None else self.last_completed_clock_ps, 'counts':list(self.trigger_counts), 'run':self.serial_number}
                if self.truth_dir:
                    self.truth_dir.mkdir(parents=True, exist_ok=True)
                    with (self.truth_dir / 'trigger-counts.jsonl').open('a', encoding='utf-8') as out:
                        out.write(json.dumps(result, separators=(',', ':')) + '\n')
                return result
            if op == 'board_next': return self.board_next(req)
            if op == 'beam': return self.beam.request(req.get('action'), req.get('origin', 'socket'), self.committed_ps, req.get('id'))
            if op in ('sink_begin', 'sink_progress'): return self.sink_message(req)
            if op == 'join': return self.join(req)
            serial = req.get('serial')
            if serial not in self.clients: raise ValueError('participant not joined')
            self._expire()
            if self.clients[serial]['lost']: raise ValueError('participant removed from this run')
            self.clients[serial]['last_seen'] = time.monotonic()
            if op == 'stop': return self.stop(serial)
            if op == 'next':
                result=self.next(serial, req)
                self._prune()
                return result
            raise ValueError('invalid request')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        serial = original = None
        try:
            while True:
                line = self.rfile.readline(1024 * 1024)
                if not line: return
                with self.server.session.lock:
                    req = {}
                    try:
                        req = json.loads(line)
                        result = self.server.session.request(req)
                        if req.get('op') == 'join':
                            serial = req['serial']
                            original = self.server.session.clients[serial]
                    except Exception as exc:
                        result = {'error': str(exc) if req.get('op') in ('trigger_logic', 'trigger_counts') else 'request rejected'}
                    self.wfile.write(json.dumps(result, separators=(',', ':')).encode() + b'\n')
                    self.wfile.flush()
        finally:
            if serial is not None:
                self.server.session.disconnected(serial, original)


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    # The telescope can connect several producers and the trigger board at once.
    request_queue_size = 64


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--geometry', required=True)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--truth-dir')
    parser.add_argument('--beam-on', action='store_true')
    parser.add_argument('--beam-status-file')
    parser.add_argument('--beam-log-file')
    args = parser.parse_args()
    settings = json.loads(Path(args.geometry).read_text())
    private = json.loads(os.environ['SESSION_SOURCE_CONFIG_JSON'])
    path = Path(args.socket)
    if path.exists(): raise RuntimeError('socket path already exists')
    server = Server(str(path), Handler)
    beam = Beam(args.beam_status_file or str(path) + '.beam.json',
                args.beam_log_file or str(path) + '.beam.log',
                initial=args.beam_on, schedule=private.get('beam_schedule'))
    server.session = Session(settings, private, beam, args.truth_dir, str(path) + '.session.log')
    if args.truth_dir:
        Path(args.truth_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.truth_dir) / 'session.pid').write_text(str(os.getpid()))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
