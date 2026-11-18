# SPDX-License-Identifier: LGPL-3.0-or-later
"""Wall-clock load generator for the nestDAQ data path."""
import argparse
import hashlib
import math
import os
import signal
import sys
import time

import numpy as np

from msensor.runner import Runner, RunnerDevice, configuration
from msensor.source import Trigger

PERIOD_NS = 4545454


class GeneratorSource:
    reader = None
    sock = None
    bounded_history = True
    finished = False

    def start(self, run_number):
        self.t0 = time.monotonic_ns()
        self.end = None
        self.finished = False
        self.number = 0
        self.next_time = PERIOD_NS

    def watermark_ns(self):
        return self.end if self.end is not None else time.monotonic_ns() - self.t0

    def end_ns(self):
        return self.end

    def take_until(self, x):
        result = []
        while self.next_time < x:
            result.append(Trigger(self.number, self.next_time))
            self.number += 1
            self.next_time += PERIOD_NS
        return result

    def mark_processed(self, x):
        pass

    def mark_sent(self, n):
        pass

    def stop(self):
        self.end = time.monotonic_ns() - self.t0

    def poll(self):
        if self.end is not None:
            self.finished = True


class GeneratorDevice(RunnerDevice):
    def arm(self, run_number, source, stream_path=None):
        self.__dict__.pop('_hits', None)
        self.__dict__.pop('_tiles', None)
        super().arm(run_number, source, stream_path)

    def _window(self, index, end, truncated):
        c = self.config
        if not hasattr(self, '_hits'):
            columns, rows = c['columns'], c['rows']
            tile_columns, tile_rows = columns // c['tiles_x'], rows // c['tiles_y']
            keys = np.arange(columns * rows, dtype=np.int64)
            tile = (keys // columns // tile_rows) * c['tiles_x'] + (keys % columns // tile_columns)
            keys = keys[~np.isin(tile, c['tiles_off'])]
            count = max(1, min(len(keys), round(self.mb_s * 250)))
            seed = int.from_bytes(hashlib.sha256(f"{c['seed']}:generator:{self.name}".encode('ascii')).digest()[:16], 'big')
            keys = np.sort(np.random.Generator(np.random.Philox(key=seed)).choice(keys, count, replace=False))
            self._hits = np.column_stack((keys % columns, keys // columns)).astype('<u2')
        hits = self._hits
        begin = index * c['window_length_ns']
        self.signals.pop(index, [])
        frame = {'window_index': index, 't_begin_ns': begin, 't_end_ns': end, 'truncated': truncated, 'hits': hits}
        self.frames[index] = frame
        self.windows_total += 1
        self.windows_nonempty += int(len(hits) > 0)
        self.hits_total += len(hits)
        self.last_truncated = truncated
        if not hasattr(self, '_tiles'):
            self.stream.window(frame)
            m = self.stream.raw.metadata
            a = hits.astype(np.int64)
            tile = (a[:, 1] // m['tile_rows']) * m['tiles_x'] + a[:, 0] // m['tile_columns']
            order = np.argsort(tile, kind='stable')
            tile, a = tile[order], a[order]
            self._tiles = []
            for t in np.unique(tile):
                points = a[tile == t].copy()
                points[:, 0] %= m['tile_columns']
                points[:, 1] %= m['tile_rows']
                self._tiles.append((int(t), len(points), points.astype('<u2').tobytes()))
        else:
            self.stream.raw.window_tiles(index, begin, end, truncated, self._tiles)


class GeneratorRunner(Runner):
    device_class = GeneratorDevice

    def __init__(self, c, device, mb_s):
        super().__init__(c, device)
        if self.device.config['readout_mode'] != 'continuous':
            raise ValueError('generator needs continuous readout')
        self.device.mb_s = mb_s

    def make_source(self):
        return GeneratorSource()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--mb-s', type=float, required=True)
    args = parser.parse_args()
    try:
        if not math.isfinite(args.mb_s) or not 0 < args.mb_s <= 500:
            raise ValueError('invalid generator rate')
        c, device = configuration(args.config)
        runner = GeneratorRunner(c, device, args.mb_s)
        if os.path.lexists(runner.ctl_path):
            raise ValueError('control socket exists')
    except Exception as exc:
        print(str(exc).splitlines()[0], file=sys.stderr)
        return 2
    def stop(signum, frame):
        runner.stop_event.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        runner.serve()
    except OSError:
        print('control socket error', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
