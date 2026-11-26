# SPDX-License-Identifier: LGPL-3.0-or-later
"""Virtual trigger board stream for the nestDAQ mode.

The runner has the control socket and the status file of the msensor runners, so
MSensorFileSource reads its file unchanged. It reads the accepted physics
triggers of the session core, adds the periodic triggers that the msensors
also receive, and writes both as a board stream (msensor/boardstream.py).
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time

from msensor.boardstream import BoardWriter
from msensor.periodic import board_cycle, periodic_time_ns
from msensor.runner import Runner, integer

CONFIG_KEYS = {'format', 'name', 'plane', 'frame_ns', 'period_clk', 'output_dir'}


def configuration(path):
    try:
        c = json.loads(Path(path).read_text())
        if (not isinstance(c, dict) or set(c) != CONFIG_KEYS or c['format'] != 'board-runner-config-1'
                or not isinstance(c['name'], str) or not c['name'].startswith('TRIGGERBOARD_')
                or not integer(c['plane'], 0, 2**32 - 1) or not integer(c['frame_ns'], 10_000_000, 1_000_000_000)
                or not integer(c['period_clk'], 1000, 1_005_000_000)
                or not isinstance(c['output_dir'], str) or not os.path.isdir(c['output_dir'])):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise ValueError('invalid configuration') from None
    return c


class BoardRunner(Runner):
    def __init__(self, c):
        self.c = c
        self.state = 'idle'; self.run = None; self.last_error = None; self.last_run_result = None
        self.raw = None; self.file = None; self.path = None
        self.lock = threading.RLock(); self.stop_event = threading.Event(); self.client = None; self.last_status = 0
        self.ctl_path = Path(c['output_dir']) / (c['name'] + '.ctl')
        self.status_path = Path(c['output_dir']) / (c['name'] + '.status.json')
        self.core = None; self.reader = None
        self.cursor = 0; self.next_k = 0; self.watermark_ns = 0

    def status(self):
        counts = self.raw.counts if self.raw else {}
        return dict(name=self.c['name'], state=self.state, run=self.run, pid=os.getpid(), time_ns=self.watermark_ns,
                    frames_written=self.raw.frame if self.raw else 0,
                    records={'periodic': counts.get('periodic', 0), 'physics': counts.get('physics', 0)},
                    file=({'path': str(self.path), 'bytes': self.path.stat().st_size} if self.path and self.path.exists() else None),
                    socket=None, last_error=self.last_error, last_run_result=self.last_run_result, updated_ns=time.time_ns())

    def cleanup(self, remove=False):
        for item in (self.reader, self.core):
            if item is not None:
                try: item.close()
                except OSError: pass
        self.reader = self.core = None
        if self.file: self.file.close(); self.file = None
        if remove and self.path: self.path.unlink(missing_ok=True); self.path = None

    def request(self, **values):
        if self.core is None:
            self.core = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.core.settimeout(3)
            self.core.connect(os.environ['SESSION_SOURCE_SOCKET'])
            self.reader = self.core.makefile('rb')
        self.core.sendall((json.dumps({'op': 'board_next', **values}, separators=(',', ':')) + '\n').encode())
        line = self.reader.readline()
        if not line: raise ConnectionError('session core disconnected')
        reply = json.loads(line)
        if reply.get('error'): raise RuntimeError('session core rejected the request')
        return reply

    def start(self, n):
        if isinstance(n, bool) or not isinstance(n, int) or n < 0: return {'ok': False, 'error': 'invalid run'}
        if self.state not in ('idle', 'drained', 'aborted'): return {'ok': False, 'error': 'busy'}
        self.run = n; self.last_error = None; self.transition('starting')
        self.path = Path(self.c['output_dir']) / f"{self.c['name']}_run{n}.msraw"; created = False
        try:
            try: self.file = self.path.open('xb', buffering=0); created = True
            except FileExistsError: raise RuntimeError('output exists') from None
            c = self.c
            self.raw = BoardWriter(self.file, dict(format='msraw-board-metadata-1', device=c['name'], plane=c['plane'], run_number=n,
                                                   frame_ns=c['frame_ns'], period_clk=c['period_clk'], clock_hz=100_500_000,
                                                   software='msensor.boardstream 1'))
            self.cursor = 0; self.next_k = 0; self.watermark_ns = 0
            self.transition('running'); return {'ok': True}
        except Exception as exc:
            reason = 'output exists' if str(exc) == 'output exists' else 'start failed'
            self.cleanup(remove=created); self.last_error = reason
            self.transition('idle')
            return {'ok': False, 'error': reason}

    def take(self, reply, final=False):
        """Write the records of one core reply and every periodic trigger before its watermark."""
        period = self.c['period_clk']
        for number, time_ps, mask in reply['records']:
            cycle = board_cycle(time_ps)
            k = cycle // period - 1
            self.raw.physics(number, time_ps // 1000, cycle, k if k >= 0 else None,
                             cycle - (k + 1) * period if k >= 0 else cycle, mask)
        self.cursor = reply['next']
        mark = (reply['watermark_ps'] + (999 if final else 0)) // 1000
        while periodic_time_ns(self.next_k, period) < mark:
            self.raw.periodic(self.next_k, periodic_time_ns(self.next_k, period), (self.next_k + 1) * period)
            self.next_k += 1
        self.watermark_ns = max(self.watermark_ns, mark)
        return mark

    def step(self):
        reply = self.request(run=self.run, cursor=self.cursor)
        if reply['kind'] == 'board':
            self.raw.advance(self.take(reply))
        if time.monotonic() - self.last_status >= .5: self.publish()
        time.sleep(.005)

    def drain(self):
        if self.state != 'running': return {'ok': False, 'error': 'busy'}
        deadline = time.monotonic() + 15; self.transition('draining')
        try:
            while True:
                reply = self.request(run=self.run, cursor=self.cursor)
                if reply['kind'] == 'board':
                    done = reply['finished']
                    mark = self.take(reply, final=done)
                    if done: break
                if time.monotonic() >= deadline: raise TimeoutError()
                time.sleep(.005)
            end = self.raw.finish(mark)
            result = {'ok': True, 'end_bytes': self.file.tell(), 'frames': end['frames_total'], 'end_ns': end['end_ns'],
                      'dropped_frames': None, 'end_sent': None}
            self.cleanup(); self.last_error = None; self.last_run_result = 'drained'; self.transition('drained'); return result
        except Exception as exc:
            self.last_error = 'drain timeout' if isinstance(exc, TimeoutError) else 'drain failed'; self.abort(); return {'ok': False, 'error': self.last_error}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True); args = parser.parse_args()
    try:
        runner = BoardRunner(configuration(args.config))
        if os.path.lexists(runner.ctl_path): raise ValueError('control socket exists')
    except Exception as exc:
        print(str(exc).splitlines()[0], file=sys.stderr); return 2
    def stop(signum, frame): runner.stop_event.set()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try: runner.serve()
    except OSError: print('control socket error', file=sys.stderr); return 2
    return 0


if __name__ == '__main__': sys.exit(main())
