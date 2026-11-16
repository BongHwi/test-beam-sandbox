# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only journal of the exact status snapshots atomically published by the RC."""
import os
import sys
import json
import time

if os.path.basename(sys.argv[0]) == 'ITS3RunControl.py' and os.environ.get('RUNCONTROL_TEST_JOURNAL'):
    target = os.path.abspath(os.environ['RUNCONTROL_TEST_STATUS_PATH'])
    journal = os.environ['RUNCONTROL_TEST_JOURNAL']
    real_replace = os.replace
    def observe_replace(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if os.path.abspath(destination) == target:
            with open(destination, 'rb') as published:
                snapshot = published.read().rstrip(b'\n')
            with open(journal, 'ab') as log:
                log.write(snapshot + b'\n')
        return result
    os.replace = observe_replace
    # The upstream ITS3 code obtains this value from Status.GetTag under its lock.
    # Record exactly what reached that public API, independently of the hook output.
    import pyeudaq
    original_get_tag = pyeudaq.Status.GetTag
    def observe_get_tag(status, key, *args):
        value = original_get_tag(status, key, *args)
        if key == 'DataEventN' and original_get_tag(status, 'TestReporter', '') == 'TEST_0':
            record={'monotonic_ns':time.monotonic_ns(),'value':int('0'+value)}
            with open(os.environ['RUNCONTROL_TEST_RECEIVED'], 'a') as log:
                log.write(json.dumps(record)+'\n')
        return value
    pyeudaq.Status.GetTag = observe_get_tag
