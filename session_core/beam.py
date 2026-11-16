# SPDX-License-Identifier: LGPL-3.0-or-later
"""Independent beam state and atomic public operation records."""
import bisect
import datetime
import json
import os
import re
from pathlib import Path
import tempfile
import time


class Beam:
    def __init__(self, status_path, log_path, initial=False, schedule=None):
        self.status_path = Path(status_path)
        self.log_path = Path(log_path)
        self.schedule = self._validate_schedule(schedule)
        self.on = self.schedule[0][1] if self.schedule is not None else bool(initial)
        self.seq = 0
        self.run_active = False
        self.run_number = None
        self.transitions = []
        self.schedule_index = 0
        self.last = None
        self.last_control_origin = 'startup'
        self.last_changed_utc = None
        self.last_changed_monotonic_ns = None
        self._publish('startup', None, changed=False)

    @staticmethod
    def _validate_schedule(schedule):
        if schedule is None:
            return None
        if not isinstance(schedule, list) or not schedule:
            raise ValueError('beam schedule must be a nonempty list')
        rows = []
        for row in schedule:
            if not isinstance(row, dict) or set(row) != {'time_ps', 'on'}:
                raise ValueError('invalid beam schedule item')
            t, on = row['time_ps'], row['on']
            if isinstance(t, bool) or not isinstance(t, int) or t < 0 or not isinstance(on, bool):
                raise ValueError('invalid beam schedule item')
            if rows and t <= rows[-1][0]:
                raise ValueError('beam schedule times must increase')
            rows.append((t, on))
        if rows[0][0] != 0:
            raise ValueError('beam schedule must start at zero')
        return rows

    def _publish(self, origin, applied_ps, *, changed, ident=None):
        if changed:
            self.seq += 1
        if origin in ('command', 'socket', 'schedule'):
            self.last_control_origin = origin
        actual_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        actual_monotonic_ns = time.monotonic_ns()
        if changed or self.last_changed_utc is None:
            self.last_changed_utc = actual_utc
            self.last_changed_monotonic_ns = actual_monotonic_ns
        record = {'beam_on': self.on, 'seq': self.seq, 'origin': origin,
                  'applied_run_ps': applied_ps, 'session_run': self.run_number,
                  'actual_utc': actual_utc, 'actual_monotonic_ns': actual_monotonic_ns,
                  'last_control_origin': self.last_control_origin,
                  'last_changed_utc': self.last_changed_utc,
                  'last_changed_monotonic_ns': self.last_changed_monotonic_ns,
                  'id': ident}
        self.last = record
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.beam-', dir=str(self.status_path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream, separators=(',', ':'))
                stream.write('\n')
            os.replace(temporary, self.status_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, separators=(',', ':')) + '\n')
        return dict(record)

    def begin(self, run_number):
        self.run_active = True
        self.run_number = run_number
        if self.schedule is not None:
            initial = self.schedule[0][1]
            changed = initial != self.on
            self.on = initial
            self.transitions = list(self.schedule)
            self.schedule_index = 1
            self._publish('schedule', 0, changed=changed)
        else:
            self.transitions = [(0, self.on)]
            self._publish('run_start', 0, changed=False)

    def end(self, freeze_ps):
        self.transitions = [row for row in self.transitions if row[0] < freeze_ps or (self.schedule is None and row[0] == freeze_ps)]
        self.run_active = False
        self.run_number = None

    def active_at(self, time_ps):
        index = bisect.bisect_right([row[0] for row in self.transitions], time_ps) - 1
        return index >= 0 and self.transitions[index][1]

    def compact(self, before_ps):
        """Keep the state at a committed time and all later transitions."""
        index = bisect.bisect_right([row[0] for row in self.transitions], before_ps) - 1
        if index > 0:self.transitions = self.transitions[index:]

    def advance(self, end_ps):
        if self.schedule is None or not self.run_active:
            return
        while self.schedule_index < len(self.schedule) and self.schedule[self.schedule_index][0] < end_ps:
            at, value = self.schedule[self.schedule_index]
            changed = value != self.on
            self.on = value
            self._publish('schedule', at, changed=changed)
            self.schedule_index += 1

    def request(self, action, origin, committed_ps, ident=None):
        if action == 'status':
            return {'accepted': True, 'status': dict(self.last)}
        if (action not in ('on', 'off') or origin not in ('command', 'socket')
                or (ident is not None and (not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', ident)))):
            return {'accepted': False, 'reason': 'invalid beam request'}
        if self.schedule is not None:
            return {'accepted': False, 'reason': 'scheduled mode'}
        desired = action == 'on'
        applied = committed_ps if self.run_active else None
        if self.run_active:
            self.transitions.append((applied, desired))
        changed = desired != self.on
        self.on = desired
        return {'accepted': True, 'status': self._publish(origin, applied, changed=changed, ident=ident)}
