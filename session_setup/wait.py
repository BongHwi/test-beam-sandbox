# SPDX-License-Identifier: LGPL-3.0-or-later
"""Wait for the synthetic example run to finish producing or to close."""
import json
from pathlib import Path
import sys
import time
mode=sys.argv[1];path=Path('/tmp/session-run/status.json');deadline=time.monotonic()+180
while time.monotonic()<deadline:
    try:status=json.loads(path.read_text())
    except (OSError,ValueError):time.sleep(.05);continue
    state=status.get('rc_state')
    if state=='ERROR' or any(c.get('state')=='ERROR' for c in status.get('connections',[])):
        raise SystemExit('Session reported an error; inspect status and logs.')
    if mode=='closed' and state=='TERMINATED':
        print('Run closed.');raise SystemExit(0)
    if mode=='ready' and state=='RUNNING':
        if any(c['name'].startswith('MSENSOR_') and c['message']=='source_finished' for c in status.get('connections',[])):
            print('Example source finished; the run can be stopped.');raise SystemExit(0)
    time.sleep(.05)
raise SystemExit('Timed out waiting for session state.')
