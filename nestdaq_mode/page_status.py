# SPDX-License-Identifier: LGPL-3.0-or-later
"""Read nestDAQ state for the page server."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from nestdaq_mode import control
from nestdaq_mode.redis_resp import Redis


def main():
    path = Path('/tmp/session-run/nestdaq/session.json')
    try:
        session = json.loads(path.read_text())
    except FileNotFoundError as exc:
        print('error: ' + str(exc).replace('\n', ' '), file=sys.stderr)
        return 2
    except Exception as exc:
        print('error: ' + str(exc).replace('\n', ' '), file=sys.stderr)
        return 1
    try:
        client = Redis(session['redis'])
        try:
            status = control.execute(client, argparse.Namespace(command='status'))[0]
        finally:
            client.file.close()
            client.sock.close()
        runners = []
        for runner in session['runners']:
            try:
                runners.append(json.loads(Path(runner['status']).read_text()))
            except (OSError, ValueError):
                runners.append({'name': runner['name'], 'error': 'unavailable'})
        files = []
        for file in sorted(Path(session['sink']['data_dir']).iterdir(), key=lambda p: p.name):
            if file.is_file():
                info = file.stat()
                files.append({'name': file.name, 'bytes': info.st_size,
                              'mtime_utc': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
        print(json.dumps({'read_utc': datetime.now(timezone.utc).isoformat(), 'input': session['input'],
                          'control': status, 'runners': runners, 'files': files,
                          'screens': {'webctl': session['webctl_port'], 'slowdash': session['slowdash_port'],
                                      'dqm': session['dqm']['http_port']}}))
        return 0
    except Exception as exc:
        print('error: ' + str(exc).replace('\n', ' '), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
