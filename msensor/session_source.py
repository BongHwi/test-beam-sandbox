# SPDX-License-Identifier: LGPL-3.0-or-later
"""Client for a shared stimulus source; the device response remains local."""
from collections import deque
import json
import os
import socket
import threading
import time
from .source import Crossing, Trigger


class SessionSource:
    def __init__(self, config, serial):
        self.config = config
        self.serial = serial
        self.lock = threading.RLock()
        self.sock = None
        self.reader = None
        self.pending = []
        self.sent = deque()
        self.ack = -1
        self.last_sent = -1
        self.watermark = 0
        self.processed_ns = 0
        self.end = None
        self.finished = False
        self.limit_reason = None
        self.bounded_history = False

    def _request(self, op, **values):
        with self.lock:
            data = {'op': op, 'serial': self.serial, **values}
            if op == 'next' and self.sent:
                self.ack = self.sent.popleft()
                data['ack'] = self.ack
            self.sock.sendall((json.dumps(data, separators=(',', ':')) + '\n').encode())
            line = self.reader.readline()
            if not line:
                raise ConnectionError('stimulus source disconnected')
            reply = json.loads(line)
            if reply.get('error'):
                raise RuntimeError('stimulus source rejected request')
            return reply

    def start(self, run_number):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(os.environ['SESSION_SOURCE_SOCKET'])
        self.reader = self.sock.makefile('rb')
        c = self.config
        if c['readout_mode'] == 'continuous':
            drain = (c['window_guard_after'] + 1) * c['window_length_ns']
        else:
            drain = c['strobe_delay_ns'] + c['strobe_length_ns']
        reply = self._request('join', pixels=[c['columns'], c['rows']], form='crossing',
                              response={}, drain_ps=drain * 1000, run=run_number, sink_required=True)
        self.bounded_history = reply.get('continuous') is True

    def mark_sent(self, number):
        with self.lock:
            if number <= self.last_sent:
                raise ValueError('non-increasing sent trigger')
            self.last_sent = number
            self.sent.append(number)

    def poll(self):
        while not self.finished:
            r = self._request('next', processed_ps=self.processed_ns * 1000)
            if r['kind'] == 'event':
                number = r['trigger']
                for item in r['crossings']:
                    self.pending.append(Crossing(number, item['time_ps'] // 1000,
                                                 item['col'], item['row']))
                self.pending.append(Trigger(number, r['time_ps'] // 1000))
            elif r['kind'] == 'crossing_tail':
                for item in r['crossings']:
                    self.pending.append(Crossing(r['crossing_id'], item['time_ps'] // 1000,
                                                 item['col'], item['row']))
            elif r['kind'] == 'wait':
                self.watermark = max(self.watermark, r.get('watermark_ps', 0) // 1000)
                break
            elif r['kind'] == 'finished':
                self.finished = True
                if r.get('source_finished'):
                    self.end = (r['time_ps'] + 999) // 1000
                    self.limit_reason = r.get('limit_reason')
                self.watermark = max(self.watermark, (r['time_ps'] + 999) // 1000)
                break
            else:
                raise RuntimeError('invalid stimulus response')

    def watermark_ns(self):
        self.poll()
        return self.watermark

    def end_ns(self):
        return self.end

    def take_until(self, t_ns):
        ready = [v for v in self.pending if v.time_ns < t_ns]
        self.pending = [v for v in self.pending if v.time_ns >= t_ns]
        return sorted(ready, key=lambda v: (v.time_ns, 0 if isinstance(v, Crossing) else 1,
                                            v.crossing_id if isinstance(v, Crossing) else v.trigger_number))

    def mark_processed(self, time_ns):
        self.processed_ns = time_ns

    def stop(self):
        reply = self._request('stop')
        self.end = (reply['time_ps'] + 999) // 1000
        self.watermark = max(self.watermark, self.end)

    def wait_barrier(self):
        while not self.finished:
            self.poll()
            if not self.finished: time.sleep(.01)
        self.reader.close()
        self.sock.close()
