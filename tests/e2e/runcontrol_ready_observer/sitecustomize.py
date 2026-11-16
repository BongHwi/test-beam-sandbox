# SPDX-License-Identifier: LGPL-3.0-or-later
"""Observe when the unmodified RunControl enters its active run loop."""
import linecache
import os
import threading

path = os.environ.get('RUNCONTROL_READY_TRACE')
if path:
    def trace(frame, event, arg):
        if event == 'call':
            return trace if frame.f_code.co_name == 'userlogic_runner_' else None
        if event == 'line':
            text = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
            if 'self.running = True' in text:
                frame.f_trace_lines = True
                trace.armed = True
            elif 'while self.running:' in text and getattr(trace, 'armed', False):
                with open(path, 'a', encoding='utf-8') as output:
                    output.write('ready\n')
                trace.armed = False
        return trace
    trace.armed = False
    threading.settrace(trace)
