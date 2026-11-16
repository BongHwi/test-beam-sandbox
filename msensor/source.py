# SPDX-License-Identifier: LGPL-3.0-or-later
"""Deterministic synthetic stimulus source (R2, R3)."""
import hashlib
import math
from dataclasses import dataclass

@dataclass(frozen=True)
class Crossing:
    crossing_id: int
    time_ns: int
    col: float
    row: float

@dataclass(frozen=True)
class Trigger:
    trigger_number: int
    time_ns: int


def uniform(seed, purpose, *ids):
    """Domain-separated counter values avoid global RNG state and call-order dependence."""
    key = ':'.join(str(x) for x in (seed, purpose, *ids)).encode('ascii')
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], 'big') >> 12
    return (value + 0.5) / 2**52


def normal(seed, purpose, index, axis):
    u = uniform(seed, purpose, index, axis, 0)
    v = uniform(seed, purpose, index, axis, 1)
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)


class SyntheticSource:
    def __init__(self, config):
        self.config = dict(config)
        self.start(0)

    def start(self, run_number):
        self._next = 0
        self._last = 0
        self._rate_time = 0
        self._pending = []

    def watermark_ns(self):
        return None

    def end_ns(self):
        return self.config.get('sim_end_ns')

    def _next_time(self):
        c = self.config
        if 'trigger_times_ns' in c:
            times = c['trigger_times_ns']
            return times[self._next] if self._next < len(times) else None
        if 'trigger_period_ns' in c:
            return c['trigger_first_ns'] + self._next * c['trigger_period_ns']
        rate = c['trigger_rate_hz']
        if not rate:
            return None
        if not self._pending:
            interval = max(1, int(-math.log(uniform(c['seed'], 'interval', self._next)) * 1e9 / rate))
            self._pending.append(self._rate_time + interval)
        return self._pending[0]

    def take_until(self, t_ns):
        if not isinstance(t_ns, int) or t_ns < self._last:
            raise ValueError('source time must be monotonic integer ns')
        end = self.end_ns()
        if end is not None and t_ns > end:
            raise ValueError('past source end')
        result = []
        while True:
            t = self._next_time()
            if t is None or t >= t_ns:
                break
            i = self._next
            c = self.config
            sigma = c['beam_sigma_px']
            result.append(Crossing(i, t, c['beam_center_col'] + sigma * normal(c['seed'], 'spot', i, 0),
                                   c['beam_center_row'] + sigma * normal(c['seed'], 'spot', i, 1)))
            result.append(Trigger(i, t))
            self._next += 1
            if self._pending:
                self._rate_time = self._pending.pop()
        self._last = t_ns
        return sorted(result, key=lambda x: (x.time_ns, 0 if isinstance(x, Crossing) else 1, x.crossing_id if isinstance(x, Crossing) else x.trigger_number))
