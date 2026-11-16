# SPDX-License-Identifier: LGPL-3.0-or-later
"""Virtual surface used by the unchanged public TriggerProducer."""
import json
import math
import os
import re
import socket
from types import SimpleNamespace

import numpy as np
from integration.trigger_logic import parse


def find_trigger():
    return 'virtual'


class DacAddrCh:
    @staticmethod
    def keys():
        return [f'CH{n}_{kind}' for n in range(4) for kind in ('GAIN', 'THR')]

    def __init__(self, name):
        if name not in self.keys():
            raise ValueError(f'unknown DAC {name}')
        self.name = name
        self.channel = int(name[2])
        self.address = 0 if name.endswith('GAIN') else 1

    def __str__(self):
        return self.name


class DacVoltage:
    def __init__(self, text):
        try:
            self.value = float(text)
        except (TypeError, ValueError):
            raise ValueError(f'DAC voltage {text}: expected a nonnegative number') from None
        if not math.isfinite(self.value) or self.value < 0:
            raise ValueError(f'DAC voltage {text}: expected a nonnegative number')


class TriggerConditionParser:
    def get_cmd_sequence(self, trg, veto):
        if trg is None:
            raise ValueError('trg is required')
        for key, text in (('trg', trg), ('veto', veto or '')):
            try:
                parse(text, allow_empty=key == 'veto')
            except ValueError as exc:
                raise ValueError(f'[trigger] {key} = {text}: not accepted; expected a valid expression using existing channels ({exc})') from exc
        return {'trg': trg, 'veto': veto or ''}


class CounterCondition:
    def __init__(self, text):
        if not isinstance(text, str) or not re.fullmatch(r'[01xR]{4}', text):
            raise ValueError(f'count = {text}: expected four characters 0, 1, x or R')
        self.name = text

    def count(self, dc):
        def matches(index):
            current, previous = index & 15, index >> 4
            for channel, ch in enumerate(reversed(self.name)):
                now, before = bool(current & (1 << channel)), bool(previous & (1 << channel))
                if ch == '1' and not now or ch == '0' and now or ch == 'R' and (not now or before):
                    return False
            return True
        return sum(int(dc[index]) for index in range(256) if matches(index))


class TriggerBoard:
    def __init__(self, port):
        if port != 'virtual':
            raise RuntimeError(f'no trigger board at {port}; this sandbox has one virtual board, use port = auto')
        self.con = SimpleNamespace(port=port)
        self.dacs = {}

    def set_dac(self, val, channel, address):
        self.dacs[channel, address] = val

    def _request(self, request):
        path = os.environ.get('VIRTUAL_ALPIDE_SOURCE_SOCKET')
        if not path:
            raise RuntimeError('VIRTUAL_ALPIDE_SOURCE_SOCKET is not set')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(path)
            connection.sendall(json.dumps(request).encode() + b'\n')
            with connection.makefile('rb') as stream:
                reply = json.loads(stream.readline())
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        return reply

    def write_trg_logic(self, seq):
        self._request(dict(op='trigger_logic', **seq))

    def latch_and_read_cnts(self):
        return np.asarray(self._request({'op': 'trigger_counts'})['counts'], dtype=np.uint64)
