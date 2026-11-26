# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the session core, the msensor runners and the trigger board runner without nestDAQ.

The driver plays the part of the nestDAQ input devices: it starts and drains every
runner through its control socket, then checks the files they wrote. It needs
only Python 3 with numpy, no Docker.
Usage: python3 tests/e2e/nestdaq_board_check.py SETUP.ini WORKDIR [SECONDS]
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


CONNECTIONS = {}


def control(path, request, timeout=40):
    # A runner aborts its run when its control connection closes, so keep it open like the input device does.
    if path not in CONNECTIONS:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        CONNECTIONS[path] = (s, s.makefile('rb'))
    s, reader = CONNECTIONS[path]
    s.settimeout(timeout)
    s.sendall(json.dumps(request).encode() + b'\n')
    return json.loads(reader.readline())


def verify(n, setup, period_clk):
    """Check the periodic triggers of every msensor and the board stream against each other."""
    from msensor import boardstream, rawstream
    from msensor.clock import Clock
    from msensor.periodic import board_cycle, periodic_time_ns
    board = boardstream.read(n / 'TRIGGERBOARD_0_run1.msraw')
    assert board['complete'], 'board stream incomplete'
    periodic = [r for r in board['records'] if r['kind'] == 'periodic']
    physics = [r for r in board['records'] if r['kind'] == 'physics']
    assert [r['number'] for r in periodic] == list(range(len(periodic))), 'periodic numbers'
    assert all(r['clock'] == (r['number'] + 1) * period_clk for r in periodic), 'periodic clock'
    assert periodic, 'no periodic trigger'
    assert [r['number'] for r in physics] == list(range(len(physics))), 'physics numbers'
    last = -1
    for r in physics:
        k = r['clock'] // period_clk - 1
        assert r['clock'] >= last, 'clock order'
        last = r['clock']
        assert r['last_periodic'] == (k if k >= 0 else None), 'last periodic'
        assert r['since'] == r['clock'] - (k + 1) * period_clk if k >= 0 else r['since'] == r['clock'], 'since'
        assert 0 <= r['pattern'] < 16, 'pattern'
    channels = {p['channel'] for p in setup.get('scintillators', [])}
    if channels:
        assert all(r['pattern'] & ~sum(1 << c for c in channels) == 0 for r in physics), 'pattern outside channels'
    out = {'periodic': len(periodic), 'physics': len(physics), 'sensors': {}}
    for dev in setup['devices']:
        if dev['kind'] != 'msensor': continue
        name = 'MSENSOR_' + str(dev['number'])
        raw = rawstream.read(n / (name + '_run1.msraw'))
        assert raw['status'] == 'closed_clean', name + ' ' + str(raw['status'])
        triggers = [r for r in raw['records'] if r['kind'] == 'TRIGGER']
        clock = Clock(dev.get('clock_offset_ns', 0), round(dev.get('clock_drift_ppm', 0) * 1000))
        missed = set(dev.get('missed_triggers', []))
        expected = [(k, clock.local(periodic_time_ns(k, period_clk))) for k in range(len(periodic)) if k not in missed]
        # the msensor may have received one more trigger than the board wrote at the end of the run
        got = [(r['trigger_number'], r['time_ns']) for r in triggers]
        assert got[:len(expected)] == [(i, t) for i, (k, t) in enumerate(expected)][:len(got)], name + ' trigger times'
        assert abs(len(got) - len(expected)) <= 1, f'{name} has {len(got)} triggers, expected {len(expected)}'
        out['sensors'][name] = len(got)
    out['frames'] = build_frames(n, setup, periodic, physics, period_clk)
    return out


def build_frames(n, setup, periodic, physics, period_clk):
    """Build the time frames offline and compare them with the streams."""
    from msensor import rawstream
    from nestdaq_mode import framebuilder
    frames_file = n / 'frames.jsonl'
    if frames_file.exists(): frames_file.unlink()
    board, sensors = framebuilder.classify(str(p) for p in n.glob('*.msraw'))
    summary = framebuilder.build(board, sensors, str(frames_file), hits=True)
    assert summary['complete'], summary
    frames = [json.loads(line) for line in frames_file.read_text().splitlines()]
    assert [f['frame'] for f in frames] == list(range(len(periodic))), 'frame numbers'
    assert sum(len(f['physics']) for f in frames) + summary['physics']['after_last_periodic'] == len(physics), 'physics in frames'
    assert summary['physics']['inconsistent'] == 0, 'physics frame vs last periodic'
    for dev in setup['devices']:
        if dev['kind'] != 'msensor': continue
        name = 'MSENSOR_' + str(dev['number'])
        raw = rawstream.read(n / (name + '_run1.msraw'))
        tiles = sum(1 for r in raw['records'] if r['kind'] == 'TILE_DATA')
        s = summary['sensors'][name]
        assert sum(f['sensors'][name]['tiles'] for f in frames) + s['windows_after_last_periodic'] == tiles, name + ' tile data lost'
        assert sum(f['sensors'][name]['hits'] for f in frames) > 0, name + ' no hits'
        missed = sorted(dev.get('missed_triggers', []))
        # a missed trigger is a border that the builder has to estimate; it marks the frame it closes and the next one
        want = sorted({k for m in missed for k in (m, m + 1) if k < len(frames)})
        got = [f['frame'] for f in frames if f['sensors'][name]['estimated_border']]
        assert all(k in got for k in want), f'{name} estimated frames {got}, missed {missed}'
        assert s['estimated_borders'] >= len([m for m in missed if m < len(frames)]) , name + ' estimated borders'
        assert s['worst_residual_ns'] < 1e6, name + ' border residual'
    return {'frames': len(frames), 'physics_in_frames': summary['physics']['in_frames'], 'sensors': summary['sensors']}


def main():
    setup_file, work = sys.argv[1], Path(sys.argv[2]).resolve()
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 6
    work.mkdir(parents=True, exist_ok=True)
    layout = work / 'layout'
    subprocess.run([sys.executable, '-m', 'session_setup.setupfile', 'prepare', setup_file, str(layout)],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    from nestdaq_mode.launch import runner_configs
    from session_setup.setupfile import beam_covariance
    setup = json.loads((layout / 'setup.json').read_text())
    geometry = json.loads((layout / 'geometry.json').read_text())
    geometry['participants'] = [p for p in geometry['participants'] if p['serial'].startswith('MSENSOR_')]
    geometry['continuous'] = True
    geometry['trigger_board'] = bool(setup['nestdaq'].get('trigger_board') == 'on')
    geometry.pop('end_ns', None)
    (layout / 'active-geometry.json').write_text(json.dumps(geometry))
    n = layout / 'nestdaq'
    for name in ('', 'logs', 'data'):
        (n / name).mkdir(exist_ok=True, parents=True)
    runners, all_runners, frame_ns, input_mode, period_clk = runner_configs(layout, n, setup)
    beam = setup['beam']
    private = {'seed': 12345, 'device_seed': 54321,
               'beam': {'position_mm': [beam['position_mm'][0], beam['position_mm'][1], float(beam['position_mm'][2])],
                        'direction': [0.0001, 0, 1],
                        'phase_space_covariance': beam_covariance(0.003, 0.004, None)},
               'rate_hz': beam['rate_hz']}
    sock = str(layout / 'source.sock')
    env = dict(os.environ, SESSION_SOURCE_CONFIG_JSON=json.dumps(private),
               MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed': 777}), SESSION_SOURCE_SOCKET=sock,
               VIRTUAL_ALPIDE_SOURCE_SOCKET=sock, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'tracksim' / 'src'))
    procs = []
    def spawn(args, log):
        procs.append(subprocess.Popen(args, env=env, cwd=ROOT, stdout=open(n / 'logs' / log, 'w'), stderr=subprocess.STDOUT))
    try:
        spawn([sys.executable, '-m', 'session_core.server', '--geometry', str(layout / 'active-geometry.json'),
               '--socket', sock, '--beam-on'], 'core.log')
        for _ in range(100):
            if os.path.exists(sock): break
            time.sleep(.1)
        for r in all_runners:
            module = 'nestdaq_mode.board_runner' if r['readout_mode'] == 'board' else 'msensor.runner'
            spawn([sys.executable, '-m', module, '--config', r['config']], r['name'] + '.log')
        for r in all_runners:
            for _ in range(100):
                try:
                    if json.loads(Path(r['status']).read_text())['state'] == 'idle': break
                except (OSError, ValueError): pass
                time.sleep(.1)
            else: raise SystemExit(r['name'] + ' did not become idle')
        results = {}
        def do(r, request):
            results[r['name']] = control(r['control'], request)
        def all_do(request):
            threads = [threading.Thread(target=do, args=(r, request)) for r in all_runners]
            for t in threads: t.start()
            for t in threads: t.join()
            return dict(results)
        started = all_do({'op': 'start', 'run': 1})
        print('start', started)
        time.sleep(seconds)
        drained = all_do({'op': 'drain'})
        print('drain', drained)
        if not all(v.get('ok') for v in started.values()) or not all(v.get('ok') for v in drained.values()):
            raise SystemExit('start or drain failed; see ' + str(n / 'logs'))
    finally:
        for p in procs: p.terminate()
        for p in procs:
            try: p.wait(10)
            except subprocess.TimeoutExpired: p.kill()
    print(json.dumps({'dir': str(n), 'period_clk': period_clk, 'runners': [r['name'] for r in all_runners]}))
    if period_clk:
        print('verified', json.dumps(verify(n, setup, period_clk)))


if __name__ == '__main__':
    main()
