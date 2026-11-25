# SPDX-License-Identifier: LGPL-3.0-or-later
"""A timed stall of one msensor within the current run.

The operator command ``session_control.sh NAME stall PRODUCER SECONDS`` leaves a
request file; the producer reads it from its run loop. While stalled the
producer keeps answering the stimulus source, so it is not excluded from the
run, but it does not process or send events. The events of the stall are kept
and sent after it, in order, as after any slow readout.
"""
import json
import os
from pathlib import Path
import time

MAX_SECONDS = 120


def directory(root):
    return Path(root) / 'stall'


def _write(path, data):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(data, separators=(',', ':')) + '\n')
    os.replace(temporary, path)


class Stall:
    """Producer side: read a request, report the state, say whether to stall."""

    def __init__(self, root, name, clock=time.monotonic):
        self.dir, self.name, self.clock = directory(root), name, clock
        self.request = self.dir / (name + '.request.json')
        self.state = self.dir / (name + '.state.json')
        self.until = None
        self.info = {}

    def reset(self):
        """At the start of a run: forget stale requests and states."""
        self.until = None
        for path in (self.request, self.state):
            path.unlink(missing_ok=True)

    def _report(self, state, **extra):
        self.info = {**self.info, 'state': state, **extra}
        _write(self.state, self.info)

    def _end(self, reason, **extra):
        if self.until is not None:
            self.until = None
            self._report('ended', reason=reason, ended_monotonic_ns=time.monotonic_ns(), **extra)

    def active(self, run):
        """True while the producer must not process events."""
        if self.until is not None and self.clock() >= self.until:
            self._end('elapsed')
        if not self.request.exists():
            return self.until is not None
        try:
            request = json.loads(self.request.read_text())
            self.request.unlink(missing_ok=True)
            seconds = float(request['seconds'])
            ident = request['id']
            if not 0 <= seconds <= MAX_SECONDS or request.get('run') != run:
                raise ValueError('request does not apply to this run')
        except (OSError, ValueError, KeyError, TypeError):
            self.info = {'format': 1}
            self._report('rejected', reason='invalid request or other run')
            return self.until is not None
        if seconds == 0:
            if self.until is not None:
                self._end('cancelled', cancel_id=ident)
            else:
                self.info = {'format': 1, 'id': ident, 'run': run}
                self._report('rejected', reason='no stall in progress')
        elif self.until is not None:
            pass  # already stalled: the request is dropped and the state stays as it is
        else:
            self.until = self.clock() + seconds
            self.info = {'format': 1, 'id': ident, 'run': run, 'seconds': seconds,
                         'started_monotonic_ns': time.monotonic_ns()}
            self._report('active')
        return self.until is not None

    def finish(self):
        """At the end of a run."""
        self._end('run_ended')


def submit(root, name, seconds, run, ident, wait=5.0):
    """Operator side: send a request and wait for the producer's answer."""
    folder = directory(root)
    folder.mkdir(exist_ok=True)
    state = folder / (name + '.state.json')
    previous = state.stat().st_mtime_ns if state.exists() else None
    _write(folder / (name + '.request.json'),
           {'id': ident, 'seconds': seconds, 'run': run})
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            current = json.loads(state.read_text())
            if state.stat().st_mtime_ns != previous and ident in (current.get('id', ident), current.get('cancel_id')):
                return current
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    (folder / (name + '.request.json')).unlink(missing_ok=True)
    return None
