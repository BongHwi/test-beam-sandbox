# SPDX-License-Identifier: LGPL-3.0-or-later
"""Integer conversion between session and msensor clocks."""


class Clock:
    def __init__(self, offset_ns=0, drift_ppb=0):
        self.offset_ns = offset_ns
        self.drift_ppb = drift_ppb
        self.identity = offset_ns == 0 and drift_ppb == 0

    def local(self, t):
        return self.offset_ns + t + (t * self.drift_ppb) // 1_000_000_000

    def session_time(self, x):
        if x <= self.offset_ns:
            return 0
        t = max(0, (x - self.offset_ns) * 1_000_000_000 // (1_000_000_000 + self.drift_ppb))
        while self.local(t) < x:
            t += 1
        while t and self.local(t - 1) >= x:
            t -= 1
        return t

    def session_span(self, d):
        return -(-d * 10**9 // (10**9 - abs(self.drift_ppb))) + 1
