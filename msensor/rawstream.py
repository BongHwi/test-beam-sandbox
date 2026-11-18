# SPDX-License-Identifier: LGPL-3.0-or-later
"""Read and write msensor raw stream version 1.

All integers are little endian. A 64-byte MSRAWV01 header (size, version,
flags, metadata length and CRC32, creation time, UUID, and zero reservation)
precedes canonical UTF-8 JSON metadata. Each record consists of a one-byte
kind, a u32 body length, the body, and CRC32 over kind, length and body.
Kinds 1..5 are TILE_DATA (<HHHHQI plus u16 column/row pairs), TRIGGER
(<QQQI), TICK (<QQQQII), TILE_STATUS (<HHQI), and END (<8Q32s).
Coordinates in TILE_DATA are tile-local and ordered by row then column.
Frame records are ordered by reference time, kind rank and tile/trigger;
TICK closes a frame, with bit 0 marking only a short final frame. END contains
counts and SHA-256 of every byte before its own record. No bytes follow END.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import zlib

MAGIC = b'MSRAWV01'
MAX_LENGTH = 16 * 1024 * 1024
HEADER = struct.Struct('<8sIHHIIQ16s16s')
PREFIX = struct.Struct('<BI')
TILE = struct.Struct('<HHHHQI')
TRIGGER = struct.Struct('<QQQI')
TICK = struct.Struct('<QQQQII')
STATUS = struct.Struct('<HHQI')
END = struct.Struct('<8Q32s')
NAMES = {1: 'TILE_DATA', 2: 'TRIGGER', 3: 'TICK', 4: 'TILE_STATUS', 5: 'END'}
KEYS = set('format device plane run_number readout_mode columns rows tiles_x tiles_y tile_columns tile_rows tiles_off window_length_ns strobe_delay_ns strobe_length_ns frame_ns anchor_windows config_hash software'.split())
U64_MAX = (1 << 64) - 1


def _integer(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _metadata(m):
    if not isinstance(m, dict) or set(m) != KEYS:
        raise ValueError('metadata keys')
    if m['format'] != 'msraw-metadata-1' or m['software'] != 'msensor.rawstream 1':
        raise ValueError('metadata format')
    if not isinstance(m['device'], str) or not isinstance(m['config_hash'], str):
        raise ValueError('metadata strings')
    if not _integer(m['plane']) or not _integer(m['run_number']) or m['run_number'] < 0:
        raise ValueError('metadata identifiers')
    for key in ('columns', 'rows', 'tiles_x', 'tiles_y', 'tile_columns', 'tile_rows'):
        if not _integer(m[key]) or m[key] < 1:
            raise ValueError('metadata geometry')
    if (m['columns'] != m['tiles_x'] * m['tile_columns'] or
        m['rows'] != m['tiles_y'] * m['tile_rows'] or
        m['tile_columns'] > 65536 or m['tile_rows'] > 65536 or
        m['tiles_x'] * m['tiles_y'] > 65536):
        raise ValueError('metadata geometry')
    off = m['tiles_off']
    if (not isinstance(off, list) or any(not _integer(x) or x < 0 or x >= m['tiles_x'] * m['tiles_y'] for x in off)
        or off != sorted(set(off))):
        raise ValueError('metadata tiles_off')
    f = m['frame_ns']
    if not _integer(f) or not 10_000_000 <= f <= 1_000_000_000:
        raise ValueError('metadata frame')
    if m['readout_mode'] == 'continuous':
        w, a = m['window_length_ns'], m['anchor_windows']
        if (not _integer(w) or not 1000 <= w <= 10_000_000 or f % w or f // w > 65536
            or not _integer(a) or not 16 <= a <= 32768
            or m['strobe_delay_ns'] is not None or m['strobe_length_ns'] is not None):
            raise ValueError('metadata continuous')
    elif m['readout_mode'] == 'triggered':
        if (m['window_length_ns'] is not None or m['anchor_windows'] is not None
            or not _integer(m['strobe_delay_ns']) or not _integer(m['strobe_length_ns'])):
            raise ValueError('metadata triggered')
    else:
        raise ValueError('metadata mode')
    return m


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')


def _u64(value, name):
    if not _integer(value) or not 0 <= value <= U64_MAX:
        raise ValueError(name)


def _record(kind, body):
    if len(body) > MAX_LENGTH:
        raise ValueError('record too large')
    data = PREFIX.pack(kind, len(body)) + body
    return data + struct.pack('<I', zlib.crc32(data))


class RawWriter:
    def __init__(self, out, metadata, deterministic=False):
        self.metadata = _metadata(dict(metadata))
        m = self.metadata
        encoded = _canonical(m)
        if len(encoded) > 0xffffffff:
            raise ValueError('metadata too large')
        ident = f"{m['run_number']}:{m['device']}:{m['config_hash']}".encode()
        uid = hashlib.sha256(ident).digest()[:16] if deterministic else os.urandom(16)
        created = 0 if deterministic else time.time_ns()
        self.out = out
        self.digest = hashlib.sha256()
        self.frames = {}
        self.frame = 0
        self.windows = 0
        self.last_window_end = 0
        self.last_window_truncated = False
        self.last_trigger_number = -1
        self.last_trigger_time = -1
        self.last_gate_number = -1
        self.max_reference = -1
        self.finished = False
        self.counts = dict(tile_data=0, triggers=0, ticks=0, tile_status=0,
                           hits_total=0, triggers_total=0, frames_total=0)
        off = set(m['tiles_off'])
        self.enabled = [t for t in range(m['tiles_x'] * m['tiles_y']) if t not in off]
        self.since = {t: 0 for t in self.enabled}
        header = HEADER.pack(MAGIC, 64, 1, int(m['readout_mode'] == 'triggered'),
                             len(encoded), zlib.crc32(encoded), created, uid, bytes(16))
        self._write(header + encoded)

    def _write(self, data):
        written = self.out.write(data)
        if written is not None and written != len(data):
            raise OSError('short write')
        self.digest.update(data)

    def _check_open(self):
        if self.finished:
            raise ValueError('writer already finished')

    def _add(self, reference, rank, order, kind, body):
        _u64(reference, 'reference time')
        frame = reference // self.metadata['frame_ns']
        if frame < self.frame:
            raise ValueError('record belongs to closed frame')
        self.max_reference = max(self.max_reference, reference)
        self.frames.setdefault(frame, []).append(((reference, rank, order), kind, body))

    def _hits(self, hits):
        m = self.metadata
        groups = {}
        seen = set()
        for hit in hits:
            if not isinstance(hit, (list, tuple)) or len(hit) != 2:
                raise ValueError('hit')
            col, row = hit
            if not _integer(col) or not _integer(row) or not (0 <= col < m['columns'] and 0 <= row < m['rows']):
                raise ValueError('hit coordinates')
            if (col, row) in seen:
                raise ValueError('duplicate hit')
            seen.add((col, row))
            tile = (row // m['tile_rows']) * m['tiles_x'] + col // m['tile_columns']
            if tile in m['tiles_off']:
                raise ValueError('disabled tile hit')
            groups.setdefault(tile, []).append((col % m['tile_columns'], row % m['tile_rows']))
        for points in groups.values():
            points.sort(key=lambda p: (p[1], p[0]))
        return groups

    def window(self, index, t_begin_ns, t_end_ns, truncated, hits):
        self._check_open()
        m = self.metadata
        if m['readout_mode'] != 'continuous' or not _integer(index) or index != self.windows or self.last_window_truncated:
            raise ValueError('window sequence')
        w = m['window_length_ns']
        if (not _integer(t_begin_ns) or not _integer(t_end_ns) or t_begin_ns != index * w
            or not t_begin_ns < t_end_ns <= t_begin_ns + w or not isinstance(truncated, bool)
            or truncated != (t_end_ns < t_begin_ns + w)):
            raise ValueError('window bounds')
        _u64(t_end_ns, 'window end')
        if t_begin_ns // m['frame_ns'] < self.frame:
            raise ValueError('window in closed frame')
        groups = self._hits(hits)
        for tile, points in groups.items():
            body = TILE.pack(tile, int(truncated), index & 0xffff, 0, 0, len(points))
            body += b''.join(struct.pack('<HH', *p) for p in points)
            self._add(t_begin_ns, 0, tile, 1, body)
            self.since[tile] += len(points)
        if index % m['anchor_windows'] == 0:
            for tile in self.enabled:
                self._add(t_begin_ns, 1, tile, 4, STATUS.pack(tile, 0, index, self.since[tile]))
                self.since[tile] = 0
        self.max_reference = max(self.max_reference, t_begin_ns)
        self.windows += 1
        self.last_window_end = t_end_ns
        self.last_window_truncated = truncated

    def window_array(self, index, t_begin_ns, t_end_ns, truncated, hits):
        import numpy as np
        self._check_open()
        m = self.metadata
        if m['readout_mode'] != 'continuous' or not _integer(index) or index != self.windows or self.last_window_truncated:
            raise ValueError('window sequence')
        w = m['window_length_ns']
        if (not _integer(t_begin_ns) or not _integer(t_end_ns) or t_begin_ns != index * w
            or not t_begin_ns < t_end_ns <= t_begin_ns + w or not isinstance(truncated, bool)
            or truncated != (t_end_ns < t_begin_ns + w)):
            raise ValueError('window bounds')
        _u64(t_end_ns, 'window end')
        if t_begin_ns // m['frame_ns'] < self.frame:
            raise ValueError('window in closed frame')
        a = np.asarray(hits)
        if a.ndim != 2 or a.shape[1] != 2:
            raise ValueError('hit')
        if a.dtype.kind not in 'iu':
            raise ValueError('hit coordinates')
        a = a.astype(np.int64)
        col, row = a[:, 0], a[:, 1]
        if np.any(col < 0) or np.any(col >= m['columns']) or np.any(row < 0) or np.any(row >= m['rows']):
            raise ValueError('hit coordinates')
        keys = row * m['columns'] + col
        if np.any(keys[1:] <= keys[:-1]):
            raise ValueError('duplicate hit')
        tiles = (row // m['tile_rows']) * m['tiles_x'] + col // m['tile_columns']
        if np.isin(tiles, m['tiles_off']).any():
            raise ValueError('disabled tile hit')
        order = np.argsort(tiles, kind='stable')
        tiles = tiles[order]; a = a[order]
        for tile in np.unique(tiles):
            points = a[tiles == tile].copy()
            points[:, 0] %= m['tile_columns']; points[:, 1] %= m['tile_rows']
            count = len(points); tile = int(tile)
            body = TILE.pack(tile, int(truncated), index & 0xffff, 0, 0, count) + points.astype('<u2').tobytes()
            self._add(t_begin_ns, 0, tile, 1, body)
            self.since[tile] += count
        if index % m['anchor_windows'] == 0:
            for tile in self.enabled:
                self._add(t_begin_ns, 1, tile, 4, STATUS.pack(tile, 0, index, self.since[tile]))
                self.since[tile] = 0
        self.max_reference = max(self.max_reference, t_begin_ns)
        self.windows += 1
        self.last_window_end = t_end_ns
        self.last_window_truncated = truncated

    def window_tiles(self, index, t_begin_ns, t_end_ns, truncated, tiles):
        self._check_open()
        m = self.metadata
        if m['readout_mode'] != 'continuous' or not _integer(index) or index != self.windows or self.last_window_truncated:
            raise ValueError('window sequence')
        w = m['window_length_ns']
        if (not _integer(t_begin_ns) or not _integer(t_end_ns) or t_begin_ns != index * w
            or not t_begin_ns < t_end_ns <= t_begin_ns + w or not isinstance(truncated, bool)
            or truncated != (t_end_ns < t_begin_ns + w)):
            raise ValueError('window bounds')
        _u64(t_end_ns, 'window end')
        if t_begin_ns // m['frame_ns'] < self.frame:
            raise ValueError('window in closed frame')
        for tile, count, points_bytes in tiles:
            self._add(t_begin_ns, 0, tile, 1, TILE.pack(tile, int(truncated), index & 0xffff, 0, 0, count) + points_bytes)
            self.since[tile] += count
        if index % m['anchor_windows'] == 0:
            for tile in self.enabled:
                self._add(t_begin_ns, 1, tile, 4, STATUS.pack(tile, 0, index, self.since[tile]))
                self.since[tile] = 0
        self.max_reference = max(self.max_reference, t_begin_ns)
        self.windows += 1
        self.last_window_end = t_end_ns
        self.last_window_truncated = truncated

    def gate(self, trigger_number, trigger_time_ns, t_begin_ns, t_end_ns, truncated, hits):
        self._check_open()
        if self.metadata['readout_mode'] != 'triggered':
            raise ValueError('gate in continuous mode')
        for value, name in ((trigger_number, 'trigger number'), (trigger_time_ns, 'trigger time'),
                            (t_begin_ns, 'gate begin'), (t_end_ns, 'gate end')):
            _u64(value, name)
        if t_end_ns < t_begin_ns or not isinstance(truncated, bool) or trigger_number <= self.last_gate_number:
            raise ValueError('gate bounds or sequence')
        if trigger_time_ns // self.metadata['frame_ns'] < self.frame:
            raise ValueError('gate in closed frame')
        groups = self._hits(hits)
        for tile, points in groups.items():
            body = TILE.pack(tile, 2 | int(truncated), 0, 0, trigger_number, len(points))
            body += b''.join(struct.pack('<HH', *p) for p in points)
            self._add(trigger_time_ns, 0, tile, 1, body)
        self.last_gate_number = trigger_number
        self.max_reference = max(self.max_reference, trigger_time_ns)

    def trigger(self, trigger_number, time_ns):
        self._check_open()
        _u64(trigger_number, 'trigger number')
        _u64(time_ns, 'trigger time')
        if trigger_number <= self.last_trigger_number or time_ns <= self.last_trigger_time:
            raise ValueError('trigger order')
        m = self.metadata
        if m['readout_mode'] == 'continuous':
            index, offset = divmod(time_ns, m['window_length_ns'])
        else:
            index, offset = U64_MAX, 0
        self._add(time_ns, 2, trigger_number, 2, TRIGGER.pack(trigger_number, time_ns, index, offset))
        self.last_trigger_number, self.last_trigger_time = trigger_number, time_ns

    def _frame(self, end, short):
        records = sorted(self.frames.get(self.frame, []), key=lambda x: x[0])
        if self.metadata['readout_mode'] == 'triggered':
            triggers = {TRIGGER.unpack(body)[0]: TRIGGER.unpack(body)[1]
                        for _, kind, body in records if kind == 2}
            for key, kind, body in records:
                if kind == 1 and triggers.get(TILE.unpack_from(body)[4]) != key[0]:
                    raise ValueError('gate does not match trigger in frame')
        self.frames.pop(self.frame, None)
        for _, kind, body in records:
            self._write(_record(kind, body))
            if kind == 1:
                self.counts['tile_data'] += 1
                self.counts['hits_total'] += TILE.unpack_from(body)[5]
            elif kind == 2:
                self.counts['triggers'] += 1
                self.counts['triggers_total'] += 1
            else:
                self.counts['tile_status'] += 1
        m = self.metadata
        index = end // m['window_length_ns'] if m['readout_mode'] == 'continuous' else U64_MAX
        body = TICK.pack(self.frame, end, index, self.counts['triggers_total'], len(records), int(short))
        self._write(_record(3, body))
        self.out.flush()
        self.frame += 1
        self.counts['ticks'] += 1
        self.counts['frames_total'] += 1

    def advance(self, time_ns):
        self._check_open()
        _u64(time_ns, 'advance time')
        m = self.metadata
        delay = 0 if m['readout_mode'] == 'continuous' else m['strobe_delay_ns'] + m['strobe_length_ns']
        target = max(0, (time_ns - delay) // m['frame_ns'])
        if m['readout_mode'] == 'continuous' and target > self.frame:
            required = target * (m['frame_ns'] // m['window_length_ns'])
            if self.windows < required or (self.last_window_truncated and self.windows - 1 < required):
                raise ValueError('frame windows not complete')
        before = self.frame
        while self.frame < target:
            self._frame((self.frame + 1) * m['frame_ns'], False)
        return self.frame - before

    def finish(self, end_ns):
        self._check_open()
        _u64(end_ns, 'end time')
        m = self.metadata
        if end_ns < self.frame * m['frame_ns'] or self.max_reference >= end_ns:
            raise ValueError('end before written or received data')
        if m['readout_mode'] == 'continuous':
            if self.last_window_end != end_ns and end_ns != 0:
                raise ValueError('windows do not reach end')
        while self.frame * m['frame_ns'] < end_ns:
            boundary = (self.frame + 1) * m['frame_ns']
            self._frame(min(boundary, end_ns), end_ns < boundary)
        if self.frames:
            raise ValueError('records after end')
        counts = self.counts
        body = END.pack(*(counts[k] for k in ('tile_data', 'triggers', 'ticks', 'tile_status',
                                              'hits_total', 'triggers_total', 'frames_total')),
                        end_ns, self.digest.digest())
        self._write(_record(5, body))
        self.out.flush()
        self.finished = True
        return dict(counts, end_ns=end_ns)


def read(path_or_bytes):
    """Read a path or bytes and return records, metadata, counts and recovery status."""
    data = bytes(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray, memoryview)) else Path(path_or_bytes).read_bytes()
    result = {'status': 'truncated', 'reason': 'missing header', 'offset': 0,
              'header': None, 'metadata': None, 'records': [], 'counts': {}}
    def stop(status, reason, offset):
        result.update(status=status, reason=reason, offset=offset)
        return result
    if len(data) < 8:
        return stop('truncated' if MAGIC.startswith(data) else 'corrupt', 'incomplete or invalid magic', 0)
    if data[:8] != MAGIC:
        return stop('corrupt', 'bad magic', 0)
    if len(data) < HEADER.size:
        return stop('truncated', 'incomplete header', 0)
    magic, size, version, flags, length, crc, created, uid, reserved = HEADER.unpack_from(data)
    if size != 64 or version != 1 or flags & ~1 or reserved != bytes(16):
        return stop('corrupt', 'invalid header', 0)
    result['header'] = {'magic': magic.decode(), 'header_size': size, 'format_version': version,
                        'flags': flags, 'metadata_length': length, 'metadata_crc32': crc,
                        'created_ns': created, 'stream_uuid': uid.hex(), 'reserved': reserved.hex()}
    pos = HEADER.size
    if len(data) - pos < length:
        return stop('truncated', 'incomplete metadata', pos)
    raw_meta = data[pos:pos + length]
    if zlib.crc32(raw_meta) != crc:
        return stop('corrupt', 'metadata checksum', pos)
    try:
        m = json.loads(raw_meta.decode('utf-8'))
        _metadata(m)
        if _canonical(m) != raw_meta or flags != int(m['readout_mode'] == 'triggered'):
            raise ValueError('noncanonical metadata or flags')
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return stop('corrupt', 'invalid metadata', pos)
    result['metadata'] = m
    pos += length
    result.update(reason='missing END', offset=pos)
    counts = dict(tile_data=0, triggers=0, ticks=0, tile_status=0,
                  hits_total=0, triggers_total=0, frames_total=0)
    result['counts'] = counts
    pending = []
    frame = 0
    previous_tick_time = 0
    previous_window = 0
    short_seen = False
    last_trigger_number = -1
    last_trigger_time = -1
    off = set(m['tiles_off'])
    enabled = set(range(m['tiles_x'] * m['tiles_y'])) - off
    since = {tile: 0 for tile in enabled}
    truncated_data = []
    while pos < len(data):
        start = pos
        if len(data) - pos < PREFIX.size:
            return stop('truncated', 'incomplete record prefix', pos)
        kind, length = PREFIX.unpack_from(data, pos)
        if kind not in NAMES or length > MAX_LENGTH:
            return stop('corrupt', 'invalid record type or length', pos)
        fixed = {2: TRIGGER.size, 3: TICK.size, 4: STATUS.size, 5: END.size}
        if (kind in fixed and length != fixed[kind]) or (kind == 1 and (length < TILE.size or (length - TILE.size) % 4)):
            return stop('corrupt', 'invalid record length', pos)
        end = pos + PREFIX.size + length + 4
        if end > len(data):
            return stop('truncated', 'incomplete record', pos)
        if zlib.crc32(data[pos:end - 4]) != struct.unpack_from('<I', data, end - 4)[0]:
            return stop('corrupt', 'record checksum', pos)
        body = data[pos + PREFIX.size:end - 4]
        rec = {'kind': NAMES[kind], 'offset': pos, 'frame': frame}
        try:
            if short_seen and kind != 5:
                raise ValueError('record after short tick')
            if kind == 1:
                tile, rflags, counter, reserved, number, hit_count = TILE.unpack_from(body)
                if reserved or tile not in enabled or hit_count == 0 or TILE.size + 4 * hit_count != length:
                    raise ValueError('invalid tile data')
                if (m['readout_mode'] == 'continuous' and (rflags & ~1 or number != 0)) or (m['readout_mode'] == 'triggered' and (rflags & ~3 or not rflags & 2 or counter != 0)):
                    raise ValueError('tile flags')
                hits = [list(v) for v in struct.iter_unpack('<HH', body[TILE.size:])]
                if (any(c >= m['tile_columns'] or r >= m['tile_rows'] for c, r in hits)
                    or hits != sorted(hits, key=lambda p: (p[1], p[0]))
                    or len({tuple(p) for p in hits}) != len(hits)):
                    raise ValueError('invalid tile hits')
                window_index = (previous_window + ((counter - previous_window) & 0xffff)
                                if m['readout_mode'] == 'continuous' else U64_MAX)
                rec.update(tile=tile, flags=rflags, window_counter=counter, window_index=window_index,
                           trigger_number=number, hit_count=hit_count, hits=hits)
                pending.append(rec)
            elif kind == 2:
                number, ts, index, offset = TRIGGER.unpack(body)
                if number <= last_trigger_number or ts <= last_trigger_time:
                    raise ValueError('trigger order')
                if m['readout_mode'] == 'continuous':
                    if (index, offset) != divmod(ts, m['window_length_ns']):
                        raise ValueError('trigger window')
                elif index != U64_MAX or offset:
                    raise ValueError('trigger window')
                rec.update(trigger_number=number, time_ns=ts, window_index=index, offset_ns=offset)
                last_trigger_number, last_trigger_time = number, ts
                pending.append(rec)
            elif kind == 4:
                tile, reserved, index, hits = STATUS.unpack(body)
                if m['readout_mode'] != 'continuous' or reserved or tile not in enabled or index % m['anchor_windows']:
                    raise ValueError('invalid tile status')
                rec.update(tile=tile, window_index=index, hits_since_last=hits)
                pending.append(rec)
            elif kind == 3:
                k, ts, index, triggers_total, record_count, rflags = TICK.unpack(body)
                boundary = (frame + 1) * m['frame_ns']
                if k != frame or rflags & ~1 or (not rflags and ts != boundary) or (rflags and not frame * m['frame_ns'] < ts < boundary):
                    raise ValueError('invalid tick frame, flags or time')
                if index != (ts // m['window_length_ns'] if m['readout_mode'] == 'continuous' else U64_MAX):
                    raise ValueError('tick window')
                if record_count != len(pending):
                    raise ValueError('frame record count')
                trigger_times = {r['trigger_number']: r['time_ns'] for r in pending if r['kind'] == 'TRIGGER'}
                last_key = None
                status_keys = set()
                tile_keys = set()
                for item in pending:
                    name = item['kind']
                    if name == 'TILE_DATA':
                        if m['readout_mode'] == 'continuous':
                            wi = previous_window + ((item['window_counter'] - previous_window) & 0xffff)
                            item['window_index'] = wi
                            reference = wi * m['window_length_ns']
                            identity = (wi, item['tile'])
                        else:
                            number = item['trigger_number']
                            if number not in trigger_times:
                                raise ValueError('tile data without trigger in frame')
                            reference = trigger_times[number]
                            item['window_index'] = U64_MAX
                            identity = (number, item['tile'])
                        if identity in tile_keys:
                            raise ValueError('duplicate tile data')
                        tile_keys.add(identity)
                        key = (reference, 0, item['tile'])
                        counts['tile_data'] += 1
                        counts['hits_total'] += item['hit_count']
                        since[item['tile']] += item['hit_count']
                        if item['flags'] & 1:
                            truncated_data.append((reference, item['window_index']))
                    elif name == 'TILE_STATUS':
                        reference = item['window_index'] * m['window_length_ns']
                        key = (reference, 1, item['tile'])
                        identity = (item['window_index'], item['tile'])
                        if identity in status_keys or item['hits_since_last'] != since[item['tile']]:
                            raise ValueError('tile status count')
                        status_keys.add(identity)
                        since[item['tile']] = 0
                        counts['tile_status'] += 1
                    else:
                        reference = item['time_ns']
                        key = (reference, 2, item['trigger_number'])
                        counts['triggers'] += 1
                        counts['triggers_total'] += 1
                    if not frame * m['frame_ns'] <= reference < ts or (last_key is not None and key < last_key):
                        raise ValueError('frame reference time or order')
                    last_key = key
                if m['readout_mode'] == 'continuous':
                    w = m['window_length_ns']
                    first = frame * m['frame_ns'] // w
                    last = (ts - 1) // w
                    anchor = m['anchor_windows']
                    first_anchor = ((first + anchor - 1) // anchor) * anchor
                    expected_status_count = len(range(first_anchor, last + 1, anchor)) * len(enabled)
                    if len(status_keys) != expected_status_count:
                        raise ValueError('missing tile status')
                if triggers_total != counts['triggers_total']:
                    raise ValueError('tick trigger count')
                rec.update(frame_index=k, time_ns=ts, window_index=index, triggers_total=triggers_total,
                           records_in_frame=record_count, flags=rflags)
                counts['ticks'] += 1
                counts['frames_total'] += 1
                previous_tick_time = ts
                previous_window = index if m['readout_mode'] == 'continuous' else 0
                short_seen = bool(rflags)
                frame += 1
                pending.clear()
            else:
                values = END.unpack(body)
                names = ('tile_data', 'triggers', 'ticks', 'tile_status', 'hits_total', 'triggers_total', 'frames_total')
                end_ns, digest = values[7:]
                if pending or any(values[i] != counts[name] for i, name in enumerate(names)):
                    raise ValueError('END counts')
                if end_ns != previous_tick_time or (not frame and end_ns != 0):
                    raise ValueError('END time')
                if digest != hashlib.sha256(data[:start]).digest():
                    raise ValueError('END digest')
                if end != len(data):
                    raise ValueError('trailing bytes')
                if m['readout_mode'] == 'continuous' and any(index != (end_ns - 1) // m['window_length_ns'] or end_ns % m['window_length_ns'] == 0 for _, index in truncated_data):
                    raise ValueError('truncated window')
                rec.update({name: values[i] for i, name in enumerate(names)})
                rec.update(end_ns=end_ns, body_sha256=digest.hex())
                result['records'].append(rec)
                return stop('closed_clean', '', end)
            result['records'].append(rec)
            pos = end
            result.update(reason='missing END', offset=pos)
        except (ValueError, OverflowError, struct.error) as exc:
            return stop('corrupt', str(exc), start)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['verify', 'dump'])
    parser.add_argument('file')
    parser.add_argument('--out')
    args = parser.parse_args()
    try:
        result = read(args.file)
    except OSError as exc:
        result = {'status': 'corrupt', 'reason': str(exc), 'offset': 0,
                  'header': None, 'metadata': None, 'records': [], 'counts': {}}
    if args.command == 'verify':
        print(json.dumps({k: v for k, v in result.items() if k != 'records'}, sort_keys=True))
    else:
        output = open(args.out, 'w', encoding='utf-8') if args.out else sys.stdout
        try:
            print(json.dumps({'header': result['header'], 'metadata': result['metadata']}, sort_keys=True), file=output)
            for record in result['records']:
                print(json.dumps(record, sort_keys=True), file=output)
        finally:
            if args.out:
                output.close()
    return {'closed_clean': 0, 'truncated': 2, 'corrupt': 3}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
