# SPDX-License-Identifier: LGPL-3.0-or-later
"""Extract msraw streams from a single-part nestDAQ FileSink run file."""
import argparse
import json
from pathlib import Path
import struct
import sys
import zlib

HEADER = struct.Struct('<QIHHIIIIQQ')


def extract(filename, outdir):
    data = Path(filename).read_bytes()
    if len(data) < 608 or data[:8] != b'FILESNK\0' or data[-304:-296] != b'FILETRL\0':
        raise ValueError('invalid FileSink header or trailer')
    run = struct.unpack_from('<Q', data, 24)[0]
    end = len(data) - 304
    pos = 304
    devices = {}
    while pos < end:
        if end - pos < HEADER.size: raise ValueError(f'incomplete message at {pos}')
        magic, length, hlen, typ, frame, femtype, fem, count, sec, usec = HEADER.unpack_from(data, pos)
        if magic != 0x00454d4954425553 or hlen != 48 or length < 48 or pos + length > end:
            raise ValueError(f'invalid message at {pos}')
        payload = data[pos + 48:pos + length]
        if fem not in devices:
            if typ != 1: raise ValueError(f'missing META at {pos}')
            if len(payload) < 64 or payload[:8] != b'MSRAWV01': raise ValueError(f'invalid META at {pos}')
            size = struct.unpack_from('<I', payload, 16)[0]
            if len(payload) != 64 + size: raise ValueError(f'invalid META length at {pos}')
            meta = json.loads(payload[64:].decode('utf-8'))
            if meta['plane'] != fem: raise ValueError(f'plane mismatch at {pos}')
            name = meta['device']; number = meta['run_number']
            if not isinstance(name, str) or not name or Path(name).name != name or not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise ValueError(f'invalid META identifiers at {pos}')
            devices[fem] = {'device': name, 'fem_id': fem, 'run_number': number,
                            'messages': 0, 'frames': 0, 'bytes': 0, 'complete': False, 'missing_frames': 0, 'last_tick': -1,
                            'chunks': [], 'path': str(Path(outdir) / f'{name}_run{number}.msraw')}
        elif typ == 1:
            raise ValueError(f'duplicate META at {pos}')
        elif typ != 0:
            raise ValueError(f'invalid message type at {pos}')
        item = devices[fem]
        if typ != 1:
            i = 0; kind = None
            while i < len(payload):
                if len(payload) - i < 9: raise ValueError(f'incomplete record at {pos + 48 + i}')
                kind, size = struct.unpack_from('<BI', payload, i)
                record_end = i + 9 + size
                if kind not in (1, 2, 3, 4, 5) or size > 16777216 or record_end > len(payload):
                    raise ValueError(f'invalid record at {pos + 48 + i}')
                if zlib.crc32(payload[i:record_end - 4]) != struct.unpack_from('<I', payload, record_end - 4)[0]:
                    raise ValueError(f'CRC mismatch at {pos + 48 + i}')
                i = record_end
            if kind is None: raise ValueError(f'empty payload at {pos}')
            if kind == 3:
                if size < 8: raise ValueError(f'invalid TICK at {pos}')
                tick = struct.unpack_from('<Q', payload, i - size - 4)[0]
                if tick <= item['last_tick']: raise ValueError(f'frame order at {pos}')
                item['missing_frames'] += tick - item['last_tick'] - 1
                item['last_tick'] = tick
            elif kind == 5:
                if size < 56: raise ValueError(f'invalid END at {pos}')
                total = struct.unpack_from('<Q', payload, i - size - 4 + 48)[0]
                if total < item['last_tick'] + 1: raise ValueError(f'frame count at {pos}')
                item['missing_frames'] += total - item['last_tick'] - 1
            item['frames'] += kind == 3
            item['complete'] = kind == 5
        item['messages'] += 1; item['bytes'] += len(payload); item['chunks'].append(payload)
        pos += length
    paths = [item['path'] for item in devices.values()]
    if len(paths) != len(set(paths)): raise ValueError('duplicate output path')
    for path in paths:
        if Path(path).exists(): raise ValueError(f'output exists: {path}')
    Path(outdir).mkdir(parents=True, exist_ok=True)
    result = []
    for fem in sorted(devices):
        item = devices[fem]; chunks = item.pop('chunks'); item.pop('last_tick')
        with open(item['path'], 'xb') as f:
            for chunk in chunks: f.write(chunk)
        result.append(item)
    return {'file': str(filename), 'run': run, 'devices': result}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('file'); parser.add_argument('outdir')
    args = parser.parse_args()
    try:
        result = extract(args.file, args.outdir)
        print(json.dumps(result, separators=(',', ':')))
        return 0 if all(x['complete'] and x['missing_frames'] == 0 for x in result['devices']) else 3
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        print(str(exc).replace('\n', ' '), file=sys.stderr)
        return 2


if __name__ == '__main__': sys.exit(main())
