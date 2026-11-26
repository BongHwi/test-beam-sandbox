# SPDX-License-Identifier: LGPL-3.0-or-later
"""Build time frames offline from msensor streams and a trigger board stream.

The periodic triggers of the trigger board are the frame borders: frame f covers
the board clock cycles [f*P, (f+1)*P) with P = period_clk, and periodic trigger
f closes it. Every msensor saw the same triggers on its own clock, so its TRIGGER
records give the borders in its local time. A window of a continuous sensor belongs
to the frame in which its start time (window index times window length) falls.
The physics triggers of the board are put in the frame of their clock.

A trigger that an msensor missed is matched by time, not by number: the border of
that sensor is then interpolated or extrapolated from its neighbours and the frame
is marked "estimated" for it. Matching needs the clock offset of a sensor to be
smaller than half a period. Data after the last periodic trigger is not in any frame
and is only counted.

Input is a directory of extracted *.msraw files or a FileSink run file. Output is
one JSON object per frame (JSON Lines) and a summary on standard output.
Usage: python -m nestdaq_mode.framebuilder (--dir DIR | --dat FILE --work DIR) --out FRAMES.jsonl [--hits]
Exit status: 0 complete, 2 unusable input, 3 an input stream is incomplete.
"""
import argparse
import bisect
import json
from pathlib import Path
import struct
import sys

from msensor import boardstream, rawstream
from msensor.periodic import CLOCK_HZ


def _format(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:8] != rawstream.MAGIC:
        raise ValueError(f'{path}: not an msraw stream')
    size = struct.unpack_from('<I', data, 16)[0]
    return json.loads(data[64:64 + size].decode('utf-8')).get('format', '')


def classify(paths):
    """Return (board path, sensor paths) of a set of msraw files."""
    board, sensors = None, []
    for path in sorted(paths):
        if _format(path) == 'msraw-board-metadata-1':
            if board is not None:
                raise ValueError('more than one board stream')
            board = path
        else:
            sensors.append(path)
    if board is None:
        raise ValueError('no board stream')
    if not sensors:
        raise ValueError('no msensor stream')
    return board, sensors


def match_borders(triggers, board_ns, period_ns):
    """Local time of every periodic trigger k for one sensor.

    triggers: [(time_ns)] of the sensor in order; board_ns: board time of every periodic trigger.
    Returns (local times, estimated flags, matched count, worst residual in ns).
    """
    n = len(board_ns)
    found = {}
    prev = None
    first = None
    worst = 0.0
    for t in triggers:
        if prev is None:
            k = round(t / period_ns) - 1
            slope = 1.0
        else:
            slope = (prev[1] - first[1]) / (board_ns[prev[0]] - board_ns[first[0]]) if prev[0] != first[0] else 1.0
            k = prev[0] + max(1, round((t - prev[1]) / (slope * period_ns)))
        if not 0 <= k < n:
            break  # trigger of the sensor after the last periodic trigger of the board
        expect = t if prev is None else prev[1] + slope * (board_ns[k] - board_ns[prev[0]])
        residual = abs(t - expect)
        if residual > period_ns / 4:
            raise ValueError(f'trigger at {t} ns does not fit the periodic triggers (off by {residual:.0f} ns)')
        worst = max(worst, residual)
        found[k] = t
        prev = (k, t)
        first = first or prev
    if len(found) < 2:
        raise ValueError('fewer than two sensor triggers match the periodic triggers')
    ks = sorted(found)
    local = []
    estimated = []
    for k in range(n):
        if k in found:
            local.append(float(found[k])); estimated.append(False); continue
        i = bisect.bisect_left(ks, k)
        lo, hi = ks[max(0, i - 1)], ks[min(len(ks) - 1, i)]
        if i == 0:
            lo, hi = ks[0], ks[1]
        elif i == len(ks):
            lo, hi = ks[-2], ks[-1]
        slope = (found[hi] - found[lo]) / (board_ns[hi] - board_ns[lo])
        local.append(found[lo] + slope * (board_ns[k] - board_ns[lo])); estimated.append(True)
    return local, estimated, len(found), worst


def build(board_path, sensor_paths, out, hits=False):
    board = boardstream.read(board_path)
    period = board['metadata']['period_clk']
    periodic = [r for r in board['records'] if r['kind'] == 'periodic']
    physics = [r for r in board['records'] if r['kind'] == 'physics']
    if [r['number'] for r in periodic] != list(range(len(periodic))) or any(r['clock'] != (r['number'] + 1) * period for r in periodic):
        raise ValueError('periodic triggers of the board are not regular')
    if len(periodic) < 2:
        raise ValueError('fewer than two periodic triggers')
    board_ns = [r['clock'] * 1e9 / CLOCK_HZ for r in periodic]
    period_ns = period * 1e9 / CLOCK_HZ
    n = len(periodic)
    frames = [{'frame': f, 'begin_clk': f * period, 'end_clk': (f + 1) * period, 'physics': [], 'sensors': {}} for f in range(n)]
    summary = {'frames': n, 'period_clk': period, 'physics': {'in_frames': 0, 'after_last_periodic': 0, 'inconsistent': 0},
               'sensors': {}, 'complete': board['complete']}
    for r in physics:
        f = r['clock'] // period
        expected = 0 if r['last_periodic'] is None else r['last_periodic'] + 1
        if f != expected:
            summary['physics']['inconsistent'] += 1
        if f >= n:
            summary['physics']['after_last_periodic'] += 1
            continue
        frames[f]['physics'].append({'number': r['number'], 'clock': r['clock'], 'since': r['since'], 'pattern': r['pattern']})
        summary['physics']['in_frames'] += 1
    for path in sensor_paths:
        raw = rawstream.read(path)
        m = raw['metadata']
        if m is None or m['readout_mode'] != 'continuous':
            raise ValueError(f'{path}: not a continuous msensor stream')
        name = m['device']
        triggers = [r['time_ns'] for r in raw['records'] if r['kind'] == 'TRIGGER']
        local, estimated, matched, worst = match_borders(triggers, board_ns, period_ns)
        width = m['window_length_ns']
        tail = 0
        for f in range(n):
            frames[f]['sensors'][name] = {'tiles': 0, 'hits': 0, 'estimated_border': estimated[f] or (f > 0 and estimated[f - 1]),
                                          'data': [] if hits else None}
        windows = [set() for _ in range(n)]
        for r in raw['records']:
            if r['kind'] != 'TILE_DATA':
                continue
            f = bisect.bisect_right(local, r['window_index'] * width)
            if f >= n:
                tail += 1
                continue
            entry = frames[f]['sensors'][name]
            entry['tiles'] += 1
            entry['hits'] += r['hit_count']
            windows[f].add(r['window_index'])
            if hits:
                entry['data'].append({'window': r['window_index'], 'tile': r['tile'], 'hits': r['hits']})
        for f in range(n):
            entry = frames[f]['sensors'][name]
            entry['windows'] = len(windows[f])
            if not hits:
                del entry['data']
        summary['sensors'][name] = {'triggers': len(triggers), 'matched': matched, 'estimated_borders': sum(estimated),
                                    'windows_after_last_periodic': tail, 'worst_residual_ns': round(worst, 1),
                                    'status': raw['status']}
        summary['complete'] = summary['complete'] and raw['status'] == 'closed_clean'
    with open(out, 'w') as f:
        for frame in frames:
            f.write(json.dumps(frame, separators=(',', ':')) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', help='directory with the board and msensor *.msraw files')
    parser.add_argument('--dat', help='FileSink run file; its streams are extracted into --work')
    parser.add_argument('--work', help='directory for the extracted streams of --dat')
    parser.add_argument('--out', required=True, help='frame file to write, JSON Lines')
    parser.add_argument('--hits', action='store_true', help='write the hits of every tile in the frames')
    args = parser.parse_args()
    try:
        if bool(args.dir) == bool(args.dat):
            raise ValueError('give exactly one of --dir and --dat')
        directory = args.dir
        if args.dat:
            if not args.work:
                raise ValueError('--dat needs --work')
            from .extract import extract
            extract(args.dat, args.work)
            directory = args.work
        board, sensors = classify(str(p) for p in Path(directory).glob('*.msraw'))
        if Path(args.out).exists():
            raise ValueError(f'output exists: {args.out}')
        summary = build(board, sensors, args.out, args.hits)
    except (OSError, ValueError, KeyError, struct.error) as exc:
        print(str(exc).replace('\n', ' '), file=sys.stderr)
        return 2
    print(json.dumps(summary, separators=(',', ':')))
    return 0 if summary['complete'] else 3


if __name__ == '__main__':
    sys.exit(main())
