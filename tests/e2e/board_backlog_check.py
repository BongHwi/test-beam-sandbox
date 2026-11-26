# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check that a board reader behind by more than 512 physics triggers writes a complete stream.

No processes and no sockets: Session.board_next is called on a bare Session with a
prepared log, and its replies go through BoardRunner.take and BoardWriter like in a run.
Usage: python3 tests/e2e/board_backlog_check.py
"""
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tracksim' / 'src'))

from msensor import boardstream
from nestdaq_mode.board_runner import BoardRunner
from session_core.server import Session

PERIOD = 2010000
FRAME_NS = 100_000_000
COUNT = 1500


def main():
    session = object.__new__(Session)
    session.clients = {'MSENSOR_0': {'run': 1, 'released': True, 'lost': False}}
    session.transport = object()
    session.boundary = 0
    session._advance = lambda: None
    session.board_enabled = True
    # one physics trigger every 1.3 ms, so that periodic triggers fall between them; the run ends later
    times_ps = [(i + 1) * 1_300_000_000 for i in range(COUNT)]
    session.board_log = [(i, t, i % 16) for i, t in enumerate(times_ps)]
    session.board_base = 0
    session.clock_ps = times_ps[-1] + 5_000_000_000

    runner = BoardRunner.__new__(BoardRunner)
    runner.c = {'name': 'TRIGGERBOARD_0', 'plane': 1000, 'frame_ns': FRAME_NS, 'period_clk': PERIOD}
    out = io.BytesIO()
    runner.raw = boardstream.BoardWriter(out, dict(format='msraw-board-metadata-1', device='TRIGGERBOARD_0', plane=1000, run_number=1,
                                                  frame_ns=FRAME_NS, period_clk=PERIOD, clock_hz=100_500_000, software='test'),
                                         deterministic=True)
    runner.cursor = 0; runner.next_k = 0; runner.watermark_ns = 0
    sizes = []
    while True:
        reply = session.board_next({'run': 1, 'cursor': runner.cursor})
        assert reply['kind'] == 'board', reply
        sizes.append(len(reply['records']))
        done = reply['finished']
        mark = runner.take(reply, final=done)
        if done:
            break
        runner.raw.advance(mark)
        assert len(sizes) < 10, 'too many replies'
    assert sizes == [512, 512, 476], sizes
    runner.raw.finish(mark)
    result = boardstream.read(out.getvalue())
    assert result['complete'], result['end']
    physics = [r for r in result['records'] if r['kind'] == 'physics']
    assert [r['number'] for r in physics] == list(range(COUNT)), 'physics order or loss'
    assert [r['pattern'] for r in physics] == [i % 16 for i in range(COUNT)], 'patterns'
    periodic = [r for r in result['records'] if r['kind'] == 'periodic']
    assert [r['number'] for r in periodic] == list(range(len(periodic))) and periodic, 'periodic numbers'
    print('ok', {'replies': sizes, 'periodic': len(periodic), 'physics': len(physics), 'frames': result['frames']})


if __name__ == '__main__':
    main()
