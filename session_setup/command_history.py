# SPDX-License-Identifier: LGPL-3.0-or-later
"""Append serialized session command history records."""
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import time


def append(root, kind, ident, **fields):
    root = Path(root)
    with (root / 'command-history.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'command-history.jsonl'
        with path.open('a+', encoding='utf-8') as stream:
            stream.seek(0)
            seq = sum(1 for _ in stream) + 1
            row = {'format': 1, 'seq': seq, 'utc': datetime.now(timezone.utc).isoformat(),
                   'monotonic_ns': time.monotonic_ns(), 'kind': kind, 'id': ident, **fields}
            stream.write(json.dumps(row, separators=(',', ':'), ensure_ascii=False) + '\n')
            stream.flush()
        fcntl.flock(lock, fcntl.LOCK_UN)
