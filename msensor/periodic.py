# SPDX-License-Identifier: LGPL-3.0-or-later
"""Periodic trigger of the nestDAQ trigger board mode.

The board counts a 100.5 MHz clock. Periodic trigger k (counted from 0 in every
run) comes when the board clock has counted (k + 1) * period_clk cycles since
the start of the run. All msensors of a session and the board stream derive the
trigger times from these functions, so they agree on every trigger.
"""
from .runner import RunnerSource
from .session_source import SessionSource
from .source import Crossing, Trigger

CLOCK_HZ = 100_500_000


def cycles_to_ps(cycles):
    """Time of the start of a board clock cycle, rounded up to ps (inverse of clock_cycle)."""
    return (cycles * 2_000_000 + 200) // 201


def periodic_time_ps(k, period_clk):
    return cycles_to_ps((k + 1) * period_clk)


def periodic_time_ns(k, period_clk):
    return periodic_time_ps(k, period_clk) // 1000


def board_cycle(time_ps):
    return time_ps * 201 // 2_000_000


class PeriodicSource(RunnerSource):
    """Sends the periodic triggers to the msensor instead of the physics triggers.

    Hits still come from every particle. Physics triggers are acknowledged to the
    session core as soon as they are seen, because the msensor never writes them.
    missed_triggers then counts periodic trigger numbers.
    """

    def __init__(self, config, serial, clock=None, missed_triggers=None, period_clk=None):
        super().__init__(config, serial, clock, None)
        self.period_clk = period_clk
        self.periodic_missed = set(missed_triggers or [])
        self.next_k = 0

    def start(self, run_number):
        super().start(run_number)
        self.next_k = 0

    def mark_sent(self, number):
        # Periodic trigger numbers are not known to the session core.
        pass

    def take_until(self, x):
        out = []
        for v in super().take_until(x):
            if isinstance(v, Trigger):
                SessionSource.mark_sent(self, v.trigger_number)
            else:
                out.append(v)
        while True:
            local = self.clock.local(periodic_time_ns(self.next_k, self.period_clk))
            if local >= x:
                break
            k = self.next_k
            self.next_k += 1
            if k in self.periodic_missed:
                self.dropped += 1
                continue
            out.append(Trigger(k - self.dropped, local))
        return sorted(out, key=lambda v: (v.time_ns, 0 if isinstance(v, Crossing) else 1,
                                          v.crossing_id if isinstance(v, Crossing) else v.trigger_number))
