# SPDX-License-Identifier: LGPL-3.0-or-later
"""Canonical public event payload encoding, independent of any existing format."""
import json

MAGIC = b'MSENSOR\x01'


def encode(record):
    body = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return MAGIC + len(body).to_bytes(4, 'big') + body


def decode(payload):
    if len(payload) < 12 or payload[:8] != MAGIC:
        raise ValueError('invalid event identifier')
    length = int.from_bytes(payload[8:12], 'big')
    if length != len(payload) - 12:
        raise ValueError('invalid event length')
    return json.loads(payload[12:])
