# SPDX-License-Identifier: LGPL-3.0-or-later
"""Trigger board stream, a record stream in the msraw container.

The container is that of rawstream.py: a 64-byte MSRAWV01 header with canonical
JSON metadata, then records of one-byte kind, u32 body length, body and CRC32
over kind, length and body, so the nestDAQ input devices, FileSink and
nestdaq_mode.extract handle it like an msensor stream. The metadata format is
msraw-board-metadata-1 and the plane is the device id of the board.

Kinds (little endian): 1 PERIODIC (<QQ: periodic trigger number, board clock),
2 PHYSICS (<QQQQI: physics trigger number, board clock, number of the last
periodic trigger before it or 2**64-1, clock cycles since that trigger or since
the start of the run, scintillator pattern with bit n for channel n), 3 TICK
(<QQQQII: frame, end time ns, 0, records so far, records in the frame, short
final frame), 5 END (<8Q32s: periodic, physics, ticks, 0, 0, records, frames,
end time ns, SHA-256 of every byte before it). The board clock counts cycles of
100.5 MHz from the start of the run. A TICK closes a frame of frame_ns.
"""
import hashlib
import json
import os
import struct
import sys
import time
import zlib

from .rawstream import HEADER, MAGIC, PREFIX, _canonical, _record

PERIODIC = struct.Struct('<QQ')
PHYSICS = struct.Struct('<QQQQI')
TICK = struct.Struct('<QQQQII')
END = struct.Struct('<8Q32s')
NONE = (1 << 64) - 1
KEYS = {'format', 'device', 'plane', 'run_number', 'frame_ns', 'period_clk', 'clock_hz', 'software'}


class BoardWriter:
    def __init__(self, out, metadata, deterministic=False):
        if set(metadata) != KEYS or metadata['format'] != 'msraw-board-metadata-1':
            raise ValueError('metadata keys')
        self.metadata = dict(metadata)
        encoded = _canonical(self.metadata)
        ident = f"{metadata['run_number']}:{metadata['device']}:{metadata['period_clk']}".encode()
        uid = hashlib.sha256(ident).digest()[:16] if deterministic else os.urandom(16)
        created = 0 if deterministic else time.time_ns()
        self.out = out
        self.digest = hashlib.sha256()
        self.frame = 0
        self.frames = {}
        self.last_time = -1
        self.counts = dict(periodic=0, physics=0, records=0)
        self.finished = False
        self._write(HEADER.pack(MAGIC, 64, 1, 2, len(encoded), zlib.crc32(encoded), created, uid, bytes(16)) + encoded)

    def _write(self, data):
        written = self.out.write(data)
        if written is not None and written != len(data):
            raise OSError('short write')
        self.digest.update(data)

    def _add(self, time_ns, rank, order, kind, body):
        index = time_ns // self.metadata['frame_ns']
        if index < self.frame:
            raise ValueError('record after its frame was closed')
        self.frames.setdefault(index, []).append(((time_ns, rank, order), kind, body))

    def periodic(self, number, time_ns, clock):
        self._add(time_ns, 0, number, 1, PERIODIC.pack(number, clock))
        self.counts['periodic'] += 1

    def physics(self, number, time_ns, clock, last_periodic, since, pattern):
        self._add(time_ns, 1, number, 2, PHYSICS.pack(number, clock, NONE if last_periodic is None else last_periodic, since, pattern))
        self.counts['physics'] += 1

    def _frame(self, end, short):
        records = sorted(self.frames.pop(self.frame, []), key=lambda x: x[0])
        for _, kind, body in records:
            self._write(_record(kind, body))
        self.counts['records'] += len(records)
        self._write(_record(3, TICK.pack(self.frame, end, 0, self.counts['records'], len(records), int(short))))
        self.out.flush()
        self.frame += 1

    def advance(self, time_ns):
        """Close every frame that ends at or before time_ns; no record may come before it later."""
        step = self.metadata['frame_ns']
        n = 0
        while (self.frame + 1) * step <= time_ns:
            self._frame((self.frame + 1) * step, False)
            n += 1
        return n

    def finish(self, end_ns):
        if self.finished:
            raise ValueError('writer already finished')
        step = self.metadata['frame_ns']
        if end_ns < self.frame * step:
            raise ValueError('end before written data')
        while self.frame * step < end_ns:
            boundary = (self.frame + 1) * step
            self._frame(min(boundary, end_ns), end_ns < boundary)
        if self.frames:
            raise ValueError('records after end')
        c = self.counts
        body = END.pack(c['periodic'], c['physics'], self.frame, 0, 0, c['records'], self.frame, end_ns, self.digest.digest())
        self._write(_record(5, body))
        self.out.flush()
        self.finished = True
        return dict(c, frames_total=self.frame, end_ns=end_ns)


def read(path_or_bytes):
    """Parse and check a board stream; returns a dict with metadata, records and status."""
    data = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, 'rb').read()
    if len(data) < 64 or data[:8] != MAGIC:
        raise ValueError('bad header')
    size = struct.unpack_from('<I', data, 16)[0]
    if len(data) < 64 + size or zlib.crc32(data[64:64 + size]) != struct.unpack_from('<I', data, 20)[0]:
        raise ValueError('bad metadata')
    meta = json.loads(data[64:64 + size])
    if meta.get('format') != 'msraw-board-metadata-1':
        raise ValueError('not a board stream')
    pos = 64 + size
    records, frame_records, ticks, end = [], [], 0, None
    while pos < len(data):
        if end is not None:
            raise ValueError('bytes after END')
        if len(data) - pos < 9:
            raise ValueError('truncated record')
        kind, length = struct.unpack_from('<BI', data, pos)
        stop = pos + 9 + length
        if kind not in (1, 2, 3, 5) or stop > len(data):
            raise ValueError('bad record')
        if zlib.crc32(data[pos:stop - 4]) != struct.unpack_from('<I', data, stop - 4)[0]:
            raise ValueError('CRC mismatch')
        body = data[pos + 5:stop - 4]
        if kind == 1:
            n, clock = PERIODIC.unpack(body)
            frame_records.append({'kind': 'periodic', 'number': n, 'clock': clock})
        elif kind == 2:
            n, clock, last, since, pattern = PHYSICS.unpack(body)
            frame_records.append({'kind': 'physics', 'number': n, 'clock': clock,
                                  'last_periodic': None if last == NONE else last, 'since': since, 'pattern': pattern})
        elif kind == 3:
            frame, end_ns, _, total, count, short = TICK.unpack(body)
            if frame != ticks or count != len(frame_records):
                raise ValueError('bad TICK')
            for r in frame_records:
                r['frame'] = frame
            records.extend(frame_records)
            frame_records = []
            ticks += 1
        else:
            c = END.unpack(body)
            if frame_records or c[6] != ticks:
                raise ValueError('bad END')
            if hashlib.sha256(data[:pos]).digest() != c[8]:
                raise ValueError('END digest mismatch')
            end = dict(periodic=c[0], physics=c[1], frames=c[6], end_ns=c[7])
        pos = stop
    return {'metadata': meta, 'records': records, 'frames': ticks, 'end': end, 'complete': end is not None}


def main():
    if len(sys.argv) != 3 or sys.argv[1] != 'verify':
        print('usage: python -m msensor.boardstream verify FILE', file=sys.stderr)
        return 2
    try:
        r = read(sys.argv[2])
    except (OSError, ValueError, KeyError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps({'frames': r['frames'], 'records': len(r['records']), 'end': r['end']}))
    return 0 if r['complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
