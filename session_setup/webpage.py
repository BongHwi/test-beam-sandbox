# SPDX-License-Identifier: LGPL-3.0-or-later
"""Serve the QA result page on 127.0.0.1 (Python standard library only; runs on the host).

The server reads the session the same way as the terminal commands (docker exec ... cat, the
beam status JSON, the QA output files, tmux capture-pane for the DAQ screen). Operations from
the page run the existing commands (scripts/session_control.sh, scripts/align_session.sh,
scripts/analyze_session.sh, scripts/start_session.sh, tmux send-keys for the RunControl keys,
docker cp and docker rm) and are accepted only as POST with the token the server made when it
started; the token is printed with the page address and kept in a state file in a folder only
the user can open. Setup files are checked with the code of scripts/setup_session.sh check.
"""
import argparse
from datetime import datetime, timezone
import errno
import hashlib
import hmac
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import stat
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).resolve().parent / 'web'
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/view.js': ('view.js', 'text/javascript; charset=utf-8'),
          '/control.js': ('control.js', 'text/javascript; charset=utf-8'),
          '/session.js': ('session.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'),
          **{f'/text/{lang}.json': (f'text/{lang}.json', 'application/json; charset=utf-8') for lang in ('en', 'ko', 'ja')}}
TYPES = {'.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.gif': 'image/gif',
         '.htm': 'text/html; charset=utf-8', '.html': 'text/html; charset=utf-8', '.woff2': 'font/woff2'}
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
HISTOGRAMS = re.compile(r'^(?:qa/[A-Za-z0-9._-]+/analysis|analysis)/output/histograms\.root$')
DEFAULT_PORT = 8765
RUN = re.compile(r'^[0-9]{1,12}$')
# The only operations of the page: each runs an existing command of the repository.
ACTIONS = {'beam_on': ('scripts/session_control.sh', 'on'), 'beam_off': ('scripts/session_control.sh', 'off'),
           'stop': ('scripts/session_control.sh', 'stop'), 'repeat': ('scripts/session_control.sh', 'repeat'),
           'terminate': ('scripts/session_control.sh', 'terminate'), 'set': ('scripts/session_control.sh', 'set'), 'align': ('scripts/align_session.sh',),
           'qa': ('scripts/analyze_session.sh',), 'start_session': None, 'key': None, 'copy': None, 'remove': None,
           'save_setup': None}
# Background operations; one of them at a time per session.
LONG = ('align', 'qa', 'start_session', 'copy')
# The page sends Q to the screen after termination.
KEYS = {'Q': 'Q'}
PAGE_IDS = {'beam_on': 'page-on', 'beam_off': 'page-off', 'stop': 'page-stop',
            'repeat': 'page-repeat', 'terminate': 'page-terminate', 'set': 'page-set'}
# The windows of the operator screen the page may read (perf runs htop and is not shown).
WINDOWS = ('rc', 'rp', 'dp', 'sp', 'dc', 'log')
OPERATIONS_LOG = '/tmp/session-run/page-operations.jsonl'
IMAGE = 'local/public-session:native'
# The folders the page writes: setup files, and copies of session output.
SETUPS = ROOT / 'configs/setups'
ALIGNMENTS = ROOT / 'configs/alignments'
OUTPUTS = ROOT / 'outputs'
# The setup files of the repository; the page saves edits of them under another name.
PROTECTED = ('default.ini', 'small-msensor.ini', 'msensor-telescope.ini')
SETUP_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.ini$')
ALIGNMENT_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.json$')


class Refused(Exception):
    def __init__(self, code, body):
        super().__init__(body.get('error'))
        self.code, self.body = code, body


def state_directory():
    """The folder of the server's state and log files: only the user can open it (the state holds the token)."""
    folder = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'test-beam-sandbox'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = folder.stat()
    if info.st_uid != os.getuid():
        raise SystemExit(f'{folder} belongs to another user; the page server keeps its token there and does not start.')
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(folder, 0o700)
    return folder


def state_file(port):
    return state_directory() / f'page-{port}.json'


def docker(*args, timeout=30):
    try:
        return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, '', str(exc))


def session_file(name, path):
    p = docker('exec', name, 'cat', '/tmp/session-run/' + path)
    return p.stdout if p.returncode == 0 else None


def read_state(name):
    """Everything the page shows, read fresh on every request."""
    out = {'session': name, 'read_utc': datetime.now(timezone.utc).isoformat()}
    inspect = docker('inspect', '--format', '{{.State.Running}}', name)
    out['exists'] = inspect.returncode == 0
    out['running'] = inspect.stdout.strip() == 'true'
    if not out['running']:
        return out
    status = session_file(name, 'status.json')
    try:
        status = json.loads(status) if status else {}
    except ValueError:
        status = {}
    out['rc_state'] = status.get('rc_state')
    # RunControl's status file carries no run number; the newest raw file is the current or latest run.
    listed = docker('exec', name, 'sh', '-c', 'ls -t /tmp/session-run/run*.raw 2>/dev/null').stdout.split()
    out['runs'] = [int(m.group(1)) for m in (re.search(r'run0*(\d+)\.raw$', x) for x in listed) if m]
    out['run'] = out['runs'][0] if out['runs'] else None
    beam = subprocess.run([str(ROOT / 'scripts/session_control.sh'), name, 'beam-status', '--json'], text=True,
                          capture_output=True, timeout=30)
    try:
        out['beam_on'] = json.loads(beam.stdout)['status']['beam_on']
    except (ValueError, KeyError, TypeError):
        out['beam_on'] = None
    latest = session_file(name, 'qa/latest.json')
    try:
        out['qa'] = json.loads(latest) if latest else None
    except ValueError:
        out['qa'] = None
    history = session_file(name, 'qa/history.jsonl') or ''
    out['history'] = [json.loads(x) for x in history.splitlines() if x.strip().startswith('{')]
    geometry = session_file(name, 'analysis-geometry.conf') or ''
    out['detectors'] = detectors(geometry)
    out['plots'] = plot_plan(out['detectors'])
    setup = session_file(name, 'setup.json')
    try:
        out['offset_max_mm'] = json.loads(setup).get('positions', {}).get('offset_max_mm', 0) if setup else 0
    except ValueError:
        out['offset_max_mm'] = 0
    return out


RATES = {}
RATES_LOCK = threading.Lock()


def event_rate(name, run, events):
    """Collector events per second from two readings of the same run at least 1.5 s apart."""
    now = time.monotonic()
    with RATES_LOCK:
        last = RATES.get(name)
        if not last or last['run'] != run or events is None or last['events'] is None or events < last['events']:
            RATES[name] = {'run': run, 'events': events, 'time': now, 'rate': None}
        elif now - last['time'] >= 1.5:
            RATES[name] = {'run': run, 'events': events, 'time': now,
                           'rate': round((events - last['events']) / (now - last['time']), 1)}
        return RATES[name]['rate']


def live(name):
    """What the experiment view polls: RunControl state, run, collector events and the beam, in one exec.

    Read RunControl's status, the beam source's state, the raw file list and whether the
    RunControl process is still present in the session container.
    """
    out = {'session': name, 'read_utc': datetime.now(timezone.utc).isoformat()}
    p = docker('exec', name, 'sh', '-c', 'cd /tmp/session-run && cat status.json; echo; echo @@; '
               'cat source.sock.beam.json; echo; echo @@; ls -t run*.raw 2>/dev/null | head -1; '
               "echo @@; if ps -eo args | grep -q '[I]TS3RunControl.py'; then echo yes; else echo no; fi", timeout=10)
    if p.returncode:
        inspect = docker('inspect', '--format', '{{.State.Running}}', name)
        out.update(exists=inspect.returncode == 0, running=inspect.stdout.strip() == 'true')
        return out
    out.update(exists=True, running=True)
    parts = (p.stdout.split('@@') + ['', '', '', ''])[:4]
    try:
        status = json.loads(parts[0])
    except ValueError:
        status = {}
    try:
        beam = json.loads(parts[1])
    except ValueError:
        beam = {}
    out['rc_state'] = status.get('rc_state')
    out['rc_alive'] = True if parts[3].strip() == 'yes' else False if parts[3].strip() == 'no' else None
    out['events'] = next((c.get('data_events') for c in status.get('connections', []) if c.get('role') == 'collector'),
                         None)
    match = re.search(r'run0*(\d+)\.raw$', parts[2].strip())
    out['run'] = int(match.group(1)) if match else None
    out['beam_on'] = beam.get('beam_on')
    out['beam_changed_utc'] = beam.get('last_changed_utc')
    out['events_per_s'] = event_rate(name, out['run'], out['events'])
    return out


def numbers(value):
    match = re.findall(r'([-+0-9.eE]+)\s*(mm|um|cm|m|deg)?', value)
    return [float(x) * {'um': 1e-3, 'cm': 10, 'm': 1000}.get(unit, 1) for x, unit in match]


def view(name):
    """The experiment view's layout: devices at the surveyed positions of the session's setup.

    Read: the setup copy (setup.json), the producer to analysis name map and the alignment
    result. The hidden offsets are never computed and the offset draw number is not sent; the
    actual positions exist only inside the session core.
    """
    out = {'session': name, 'read_utc': datetime.now(timezone.utc).isoformat()}
    p = docker('exec', name, 'sh', '-c', 'cd /tmp/session-run && cat setup.json 2>/dev/null; echo; echo @@; '
               'cat mapping.json; echo; echo @@; cat analysis-geometry.conf; echo @@; '
               'cat alignment/alignment.json 2>/dev/null; echo; echo @@; cat geometry.json 2>/dev/null', timeout=10)
    if p.returncode:
        inspect = docker('inspect', '--format', '{{.State.Running}}', name)
        out.update(exists=inspect.returncode == 0, running=inspect.stdout.strip() == 'true')
        return out
    out.update(exists=True, running=True)
    parts = (p.stdout.split('@@') + [''] * 5)[:5]
    loads = []
    for i in (0, 1, 3, 4):
        try:
            loads.append(json.loads(parts[i]) if parts[i].strip() else None)
        except ValueError:
            loads.append(None)
    setup, mapping, alignment, geometry = loads
    analysis = {m.get('producer'): m.get('analysis_name') for m in mapping or []}
    roles = {r['name']: r['role'] for r in detectors(parts[2])}
    devices = []
    if setup:
        out.update(setup_layout(setup, analysis))
        devices = out.pop('devices')
    else:
        # A session started from a layout directory: the analysis geometry's surveyed positions.
        out['source'] = 'layout'
        beam = (geometry or {}).get('settings', {}).get('beam', {})
        out['beam'] = {'particle': beam.get('particle'), 'energy_gev': beam.get('kinetic_energy_gev'),
                       'position_mm': None, 'spread_mm2': None, 'rate_hz': None}
        out['run_events'] = out['offset_max_mm'] = None
        name_ = None
        for line in parts[2].splitlines():
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                name_ = line[1:-1]
                devices.append({'label': name_, 'name': name_, 'kind': '', 'settings': {},
                                'role': 'dut' if roles.get(name_) == 'dut' else 'reference'})
            elif name_ and '=' in line and not line.startswith('#'):
                key, value = (x.strip() for x in line.split('=', 1))
                if key == 'type':
                    devices[-1]['kind'] = value.strip('"')
                elif key == 'number_of_pixels':
                    devices[-1]['pixels'] = [int(x) for x in numbers(value)]
                elif key == 'pixel_pitch':
                    devices[-1]['pitch_um'] = [x * 1000 for x in numbers(value)]
                elif key == 'position':
                    devices[-1]['position_mm'] = numbers(value)
    finish_layout(out, devices)
    response = docker('exec', name, 'python', '-m', 'session_setup.control', 'settings', '--json', timeout=10)
    try:
        out['settings'] = json.loads(response.stdout) if response.returncode == 0 else None
    except ValueError:
        out['settings'] = None
    if alignment and alignment.get('corrections_um'):
        out['alignment'] = {'id': alignment.get('id'), 'run': alignment.get('run'),
                            'corrections_um': alignment['corrections_um']}
    return out


def setup_layout(setup, analysis=None):
    """The view of a setup (session copy or a setup being edited): surveyed positions and the setup's beam only.

    The hidden offsets are never computed and the offset draw number is not sent.
    """
    analysis = analysis or {}
    b = setup.get('beam', {})
    out = {'source': 'setup', 'beam': {k: b.get(k) for k in ('particle', 'energy_gev', 'position_mm', 'spread_mm2', 'rate_hz')},
           'run_events': setup.get('run', {}).get('events'),
           'offset_max_mm': setup.get('positions', {}).get('offset_max_mm', 0), 'devices': [],
           'scintillators': setup.get('scintillators', []), 'trigger': setup.get('trigger')}
    for d in setup.get('devices', []):
        label = ('ALPIDE_plane_' if d['kind'] == 'alpide' else 'MSENSOR_') + str(d['number'])
        row = {'label': label, 'name': analysis.get(label, label), 'kind': d['kind'], 'number': d['number'],
               'plane': d['plane'], 'position_mm': d['position_mm'], 'pixels': d['pixels'], 'pitch_um': d['pitch_um']}
        if d['kind'] == 'msensor':
            row['settings_all'] = {k: d.get(k) for k in ('threshold_dac', 'readout', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns')}
            row.update(tiles=d.get('tiles'), tile_pixels=d.get('tile_pixels'), tiles_off=d.get('tiles_off', []),
                       settings={k: d.get(k) for k in ('threshold_dac', 'readout', 'window_length_ns',
                                                       'strobe_delay_ns', 'strobe_length_ns')})
            keep = ('threshold_dac', 'readout', 'window_length_ns') if d.get('readout') == 'continuous' else (
                'threshold_dac', 'readout', 'strobe_delay_ns', 'strobe_length_ns')
            row['settings'] = {k: row['settings'][k] for k in keep}
        else:
            row['settings'] = {k: d.get(k) for k in ('ithr', 'strobe_length')}
        row['role'] = d.get('role', 'dut') if d['kind'] == 'msensor' else 'reference'
        out['devices'].append(row)
    return out


def finish_layout(out, devices):
    devices = [d for d in devices if all(k in d for k in ('position_mm', 'pixels', 'pitch_um'))]
    for d in devices:
        d['size_mm'] = [d['pixels'][i] * d['pitch_um'][i] / 1000 for i in (0, 1)]
    out['devices'] = sorted(devices, key=lambda d: (d['position_mm'][2], d['name']))
    return out


def preview(setup):
    out = setup_layout(setup)
    return finish_layout(out, out.pop('devices'))


def sessions():
    """The session containers of this repository: made by scripts/start_session.sh from the session image."""
    p = docker('ps', '-a', '--no-trunc', '--filter', f'ancestor={IMAGE}', '--format', '{{.Names}}\t{{.State}}\t{{.Command}}')
    rows = []
    for line in p.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[2].strip('"').split()[-2:] == ['sleep', 'infinity'] and NAME.match(parts[0]):
            rows.append({'name': parts[0], 'state': parts[1]})
    return sorted(rows, key=lambda r: r['name'])


def image_present():
    return docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).returncode == 0


def setup_label(name):
    return f'configs/setups/{name if isinstance(name, str) and SETUP_NAME.match(name) else "setup.ini"}'


def setup_list():
    files = sorted(p.name for p in SETUPS.glob('*.ini') if SETUP_NAME.match(p.name) and p.is_file())
    alignments = sorted(p.name for p in ALIGNMENTS.glob('*.json') if ALIGNMENT_NAME.match(p.name) and p.is_file()) \
        if ALIGNMENTS.is_dir() else []
    from session_setup import setupfile
    return {'folder': 'configs/setups', 'alignment_folder': 'configs/alignments', 'output_folder': 'outputs',
            'files': [{'name': f, 'protected': f in PROTECTED} for f in files], 'alignments': alignments,
            'items': setupfile.item_table(), 'new': setupfile.written_sections(setupfile.build()),
            'protected': list(PROTECTED), 'image': image_present()}


def checked(name, sections):
    """The setup text of edited sections and what `check` and `show` say about it, with the view of a valid setup."""
    from session_setup import setupfile
    if not isinstance(sections, list) or len(sections) > 200 or not all(
            isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in x[1].items()) for x in sections):
        raise Refused(400, {'error': 'invalid sections'})
    try:
        text = setupfile.sections_text([(a, b) for a, b in sections])
    except ValueError as exc:
        raise Refused(400, {'error': 'a value holds a character a setup file cannot hold in a value '
                                     '(line break, #, ;, [, ], =)', 'item': str(exc)})
    code, check, show, setup = setupfile.report(setup_label(name), text)
    view_ = preview(setupfile.normalized(setup)) if setup else None
    return text, code, check, show, setup, view_


def setup_file(name):
    """A setup file of the folder, as written, with what `check` and `show` say about it."""
    from session_setup import setupfile
    if not isinstance(name, str) or not SETUP_NAME.match(name) or not (SETUPS / name).is_file():
        raise Refused(404, {'error': 'no such setup file in configs/setups'})
    text = (SETUPS / name).read_text(encoding='utf-8')
    try:
        sections = setupfile.raw_sections(text)
    except Exception:
        sections = None
    for section, values in sections or []:
        # A single-tile msensor given by its pixels is edited as one tile.
        if section.startswith('msensor') and 'pixels' in values and 'tiles' not in values and 'tile_pixels' not in values:
            values['tiles'], values['tile_pixels'] = '1, 1', values.pop('pixels')
    code, check, show, setup = setupfile.report(setup_label(name), text)
    view_ = preview(setupfile.normalized(setup)) if setup else None
    return {'name': name, 'protected': name in PROTECTED, 'sections': sections, 'code': code, 'check': check,
            'show': show, 'view': view_}


def save_setup(request):
    """Save edited sections as a setup file of the folder: checked as `check` does, written as `new` writes."""
    from session_setup import setupfile
    name, overwrite = request.get('file'), request.get('overwrite') is True
    if not isinstance(name, str) or not SETUP_NAME.match(name):
        raise Refused(400, {'error': 'file name: letters, digits, - and _ ending in .ini, saved in configs/setups'})
    if name in PROTECTED:
        raise Refused(400, {'error': f'{name} is a setup of the repository; save under another name'})
    text, code, check, show, setup, _ = checked(name, request.get('sections'))
    if code:
        raise Refused(422, {'error': 'invalid setup', 'lines': check})
    target = SETUPS / name
    if target.exists() and not overwrite:
        raise Refused(409, {'error': 'exists', 'file': name})
    final = setupfile.render(setup)
    again, _, _, _ = setupfile.report(setup_label(name), final)
    if again:
        raise Refused(500, {'error': 'the written file does not pass the check'})
    descriptor, part = tempfile.mkstemp(prefix='.' + name + '.', suffix='.part', dir=SETUPS)
    os.close(descriptor)
    part = Path(part)
    try:
        part.write_text(final, encoding='utf-8')
        os.chmod(part, 0o644)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    command = ['scripts/setup_session.sh', 'check', f'configs/setups/{name}']
    p = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    return {'saved': f'configs/setups/{name}', 'job': {'command': shlex.join(command), 'output': p.stdout,
                                                       'exit_code': p.returncode, 'running': False}}


def screen(name, window):
    """The text of one window of the session's operator screen, as tmux shows it; read only."""
    p = docker('exec', name, 'tmux', 'capture-pane', '-p', '-t', f'ITS3:{window}', timeout=10)
    return {'session': name, 'window': window, 'read_utc': datetime.now(timezone.utc).isoformat(),
            'ok': p.returncode == 0, 'text': p.stdout if p.returncode == 0 else '', 'error': '' if p.returncode == 0 else p.stderr.strip()}


def detectors(geometry):
    """Detectors in beam order with their roles, from the session's analysis geometry."""
    rows, name = [], None
    for line in geometry.splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1]
            rows.append({'name': name, 'role': '', 'z_mm': 0.0})
        elif name and '=' in line and not line.startswith('#'):
            key, value = (x.strip() for x in line.split('=', 1))
            if key == 'role':
                rows[-1]['role'] = value.strip('"').lower()
            elif key == 'position':
                match = re.findall(r'([-+0-9.eE]+)\s*(mm|um|cm|m)?', value)
                if len(match) == 3:
                    rows[-1]['z_mm'] = float(match[2][0]) * {'um': 1e-3, 'cm': 10, 'm': 1000}.get(match[2][1], 1)
    return sorted(rows, key=lambda r: r['z_mm'])


def plot_plan(rows):
    """Histograms of the QA tab, in beam order; the page draws exactly this list."""
    plan = [{'group': 'hitmaps', 'detector': r['name'], 'path': f'EventLoaderEUDAQ2/{r["name"]}/hitmap', 'option': 'colz'}
            for r in rows]
    for r in rows:
        if r['role'] != 'reference':
            plan += [{'group': 'correlations', 'detector': r['name'], 'axis': axis, 'option': 'colz',
                      'path': f'Correlations/{r["name"]}/correlation{axis.upper()}_2D'} for axis in ('x', 'y')]
    plan.append({'group': 'tracks', 'detector': '', 'path': 'Tracking4D/trackChi2ndof', 'option': ''})
    for r in rows:
        if r['role'] == 'dut':
            d = r['name']
            plan += [{'group': 'dut', 'detector': d, 'kind': 'efficiency_map', 'option': 'colz',
                      'path': f'AnalysisEfficiency/{d}/chipEfficiencyMap_trackPos_TProfile'},
                     {'group': 'dut', 'detector': d, 'kind': 'inpixel', 'option': 'colz',
                      'path': f'AnalysisEfficiency/{d}/pixelEfficiencyMap_trackPos_TProfile'},
                     {'group': 'dut', 'detector': d, 'kind': 'residual_x', 'option': '',
                      'path': f'AnalysisDUT/{d}/global_residuals/residualsX'},
                     {'group': 'dut', 'detector': d, 'kind': 'residual_y', 'option': '',
                      'path': f'AnalysisDUT/{d}/global_residuals/residualsY'},
                     {'group': 'dut', 'detector': d, 'kind': 'efficiency_time', 'option': '',
                      'path': f'AnalysisEfficiency/{d}/efficiencyVsTime'}]
    plan.append({'group': 'history', 'detector': '', 'path': '', 'option': ''})
    return plan


def jsroot_directory(cache):
    """Copy JSROOT from the session image once (it ships with the image's ROOT); nothing comes from the network."""
    image = docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).stdout.strip()
    if not image:
        return None
    target = cache / ('jsroot-' + image.split(':')[-1][:16])
    if (target / 'modules' / 'core.mjs').is_file():
        return target
    staging = Path(tempfile.mkdtemp(prefix='jsroot-', dir=cache))
    cid = docker('create', IMAGE, 'true').stdout.strip()
    try:
        if not cid or docker('cp', cid + ':/opt/conda/js/.', str(staging), timeout=300).returncode:
            shutil.rmtree(staging, ignore_errors=True)
            return None
    finally:
        if cid:
            docker('rm', '-f', cid)
    staging.rename(target)
    return target


def command_for(action, name, run):
    """The command line of a session command, as a list; nothing from the request becomes shell text."""
    script, *rest = ACTIONS[action]
    command = [script, name, *rest]
    if action in PAGE_IDS:
        command += ['--id', PAGE_IDS[action]]
    if action in ('align', 'qa') and run:
        command += ['--run', run]
    return command


def plan(request):
    """The command of an operation request, as a list: (session, action, command). Refused raises the answer."""
    name, action = request.get('session'), request.get('action')
    run = request.get('run') or ''
    if not isinstance(name, str) or not NAME.match(name) or action not in ACTIONS or action == 'save_setup' or \
            not isinstance(run, str) or (run and (action not in ('align', 'qa') or not RUN.match(run))):
        raise Refused(400, {'error': 'invalid session, action or run'})
    if action == 'set':
        section, item, value = request.get('section'), request.get('item'), request.get('value')
        items = {'trigger': ('trg', 'veto', 'count'), 'run': ('events',), 'alpide': ('ithr', 'strobe_length'),
                 'msensor': ('threshold_dac', 'readout', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns')}
        match = re.fullmatch(r'(alpide|msensor) [0-9]{1,2}', section) if isinstance(section, str) else None
        kind = section if section in ('run','trigger') else match.group(1) if match else None
        if kind not in items or item not in items[kind] or not isinstance(value, str) or not re.fullmatch(r'[0-9A-Za-z ,._&|!()<>+=-]{0,400}' if kind == 'trigger' else r'[0-9A-Za-z ,._-]{1,400}', value):
            raise Refused(400, {'error': 'invalid setting'})
        return name, action, ['scripts/session_control.sh', name, 'set', *section.split(), item, value, '--id', PAGE_IDS['set']]
    if ACTIONS[action]:
        return name, action, command_for(action, name, run)
    if action == 'key':
        key = request.get('key')
        if not isinstance(key, str) or key not in KEYS:
            raise Refused(400, {'error': 'only the RunControl key Q can be sent'})
        return name, action, ['docker', 'exec', name, 'tmux', 'send-keys', '-t', 'ITS3:rc', KEYS[key]]
    if action == 'start_session':
        setup, alignment = request.get('setup'), request.get('alignment') or ''
        if not isinstance(setup, str) or not SETUP_NAME.match(setup) or not (SETUPS / setup).is_file():
            raise Refused(400, {'error': 'no such setup file in configs/setups'})
        if not isinstance(alignment, str) or (alignment and (not ALIGNMENT_NAME.match(alignment)
                                                            or not (ALIGNMENTS / alignment).is_file())):
            raise Refused(400, {'error': 'no such alignment file in configs/alignments'})
        if not image_present():
            raise Refused(409, {'error': 'image', 'image': IMAGE, 'build': 'scripts/build_session.sh'})
        command = ['scripts/start_session.sh', '--setup', f'configs/setups/{setup}', '--name', name, '--no-attach']
        if alignment:
            command[3:3] = ['--alignment', f'configs/alignments/{alignment}']
        return name, action, command
    if action == 'copy':
        OUTPUTS.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target, n = f'{name}-{stamp}', 1
        while (OUTPUTS / target).exists():
            n += 1
            target = f'{name}-{stamp}-{n}'
        return name, action, ['docker', 'cp', f'{name}:/tmp/session-run', f'outputs/{target}']
    # remove: only a session container of this repository.
    if name not in [r['name'] for r in sessions()]:
        raise Refused(404, {'error': 'not a session container of this repository'})
    return name, action, ['docker', 'rm', '-f', name]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def record(name, row):
    """One line per page operation in the session's own directory (kept with the session output)."""
    line = json.dumps({k: row[k] for k in ('started_utc', 'finished_utc', 'action', 'command', 'exit_code')}) + '\n'
    try:
        subprocess.run(['docker', 'exec', '-i', name, 'sh', '-c', 'cat >> ' + OPERATIONS_LOG], input=line, text=True,
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_job(server, job, command):
    """Run one command; its output (stdout and stderr, as in a terminal) is kept for the page."""
    try:
        p = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL)
        for line in p.stdout:
            with server.jobs_lock:
                job['output'] += line
        code = p.wait()
    except OSError as exc:
        job['output'] += str(exc) + '\n'
        code = 127
    with server.jobs_lock:
        job.update(exit_code=code, finished_utc=utc_now(), running=False)
    # A copy of the output stays equal to the session directory, and a removed session has no directory:
    # these two are kept only in the server's list of operations.
    if job['action'] not in ('copy', 'remove'):
        record(job['session'], job)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'qa-page'

    def log_message(self, *args):
        pass

    def send_body(self, code, body, kind, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def refuse(self, code, text):
        self.send_body(code, (text + '\n').encode(), 'text/plain; charset=utf-8')

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/control':
            return self.control()
        if path == '/api/setup-check':
            return self.setup_check()
        self.refuse(405, 'Only page operations are accepted, at /api/control.')

    def body(self, limit=65536):
        length = int(self.headers.get('Content-Length', '0'))
        request = json.loads(self.rfile.read(length) if 0 < length <= limit else b'')
        if not isinstance(request, dict):
            raise ValueError
        return request

    def setup_check(self):
        """Check edited setup sections without saving (no token: nothing is written)."""
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.answer(415, {'error': 'JSON only'})
        try:
            request = self.body()
            _, code, check, show, _, view_ = checked(request.get('file'), request.get('sections'))
        except Refused as exc:
            return self.answer(exc.code, exc.body)
        except ValueError:
            return self.answer(400, {'error': 'invalid request'})
        return self.answer(200, {'code': code, 'check': check, 'show': show, 'view': view_})

    def do_PUT(self):
        self.refuse(405, 'Not accepted.')

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    def answer(self, code, body):
        self.send_body(code, json.dumps(body).encode(), 'application/json')

    def control(self):
        """An operation of the page: only with the server's token, only the fixed commands."""
        if self.server.token is None:
            return self.answer(403, {'error': 'This page server was started with --view-only.'})
        kind = self.headers.get('Content-Type', '').split(';')[0].strip().lower()
        given = self.headers.get('X-Page-Token', '')
        if kind != 'application/json' or not hmac.compare_digest(given.encode(), self.server.token.encode()):
            return self.answer(403, {'error': 'missing or wrong page token'})
        try:
            request = self.body()
            if request.get('action') == 'save_setup':
                return self.answer(200, save_setup(request))
            name, action, command = plan(request)
        except Refused as exc:
            return self.answer(exc.code, exc.body)
        except (ValueError, AttributeError):
            return self.answer(400, {'error': 'invalid request'})
        job = {'id': secrets.token_hex(6), 'session': name, 'action': action, 'command': shlex.join(command),
               'started_utc': utc_now(), 'finished_utc': None, 'exit_code': None, 'output': '', 'running': True}
        if action in LONG:
            with self.server.jobs_lock:
                busy = [j for j in self.server.jobs if j['session'] == name and j['running'] and j['action'] in LONG]
                if busy:
                    return self.answer(409, {'error': 'busy', 'job': dict(busy[0])})
                self.server.jobs.append(job)
            threading.Thread(target=run_job, args=(self.server, job, command), daemon=True).start()
            return self.answer(202, {'job': dict(job)})
        with self.server.jobs_lock:
            self.server.jobs.append(job)
        run_job(self.server, job, command)
        return self.answer(200, {'job': dict(job)})

    def jobs(self, name):
        with self.server.jobs_lock:
            mine = [dict(j) for j in self.server.jobs if j['session'] == name][-20:]
        log = session_file(name, 'page-operations.jsonl') or ''
        rows = []
        for line in log.splitlines()[-50:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return {'session': name, 'view_only': self.server.token is None, 'jobs': mine[::-1], 'log': rows[::-1]}

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = unquote(parts.path)
        name = (query.get('session') or [self.server.session or ''])[0]
        if path in STATIC:
            file, kind = STATIC[path]
            return self.send_body(200, (PAGE / file).read_bytes(), kind)
        if path == '/api/state':
            if not NAME.match(name):
                return self.refuse(400, 'invalid session name')
            state = read_state(name)
            state['refresh_seconds'] = self.server.refresh
            state['refresh_runs'] = self.server.refresh_runs
            state['refresh_started'] = self.server.refresh_started
            return self.send_body(200, json.dumps(state).encode(), 'application/json')
        if path in ('/api/live', '/api/view'):
            if not NAME.match(name):
                return self.refuse(400, 'invalid session name')
            body = live(name) if path == '/api/live' else view(name)
            return self.send_body(200, json.dumps(body).encode(), 'application/json')
        if path == '/api/jobs':
            if not NAME.match(name):
                return self.refuse(400, 'invalid session name')
            return self.answer(200, self.jobs(name))
        if path == '/api/sessions':
            return self.answer(200, {'sessions': sessions(), 'image': IMAGE, 'view_only': self.server.token is None})
        if path == '/api/setups':
            return self.answer(200, setup_list())
        if path == '/api/setup':
            try:
                return self.answer(200, setup_file((query.get('file') or [''])[0]))
            except Refused as exc:
                return self.answer(exc.code, exc.body)
        if path == '/api/screen':
            window = (query.get('window') or [''])[0]
            if not NAME.match(name) or window not in WINDOWS:
                return self.refuse(400, 'invalid session name or window')
            return self.answer(200, screen(name, window))
        if path == '/api/server':
            # Lets a later command tell this server from an unrelated process with the same number.
            return self.send_body(200, json.dumps({'server': 'qa-page', 'pid': os.getpid(), 'port': self.server.server_port,
                                                   'session': self.server.session}).encode(), 'application/json')
        if path == '/api/histograms.root':
            return self.histograms(name, *((query.get(k) or [''])[0] for k in ('file', 'qa', 'finished')))
        if path.startswith('/jsroot/'):
            return self.jsroot(path[len('/jsroot/'):])
        self.refuse(404, 'not found')

    def jsroot(self, relative):
        base = self.server.jsroot
        if base is None:
            return self.refuse(503, 'JSROOT could not be taken from the session image ' + IMAGE)
        target = (base / relative).resolve()
        if '..' in Path(relative).parts or base.resolve() not in target.parents or not target.is_file():
            return self.refuse(404, 'not found')
        self.send_body(200, target.read_bytes(), TYPES.get(target.suffix, 'application/octet-stream'))

    def histograms(self, name, relative, qa, finished):
        """The histogram file of exactly the QA result the page shows; one copy at a time."""
        if not NAME.match(name) or not HISTOGRAMS.match(relative):
            return self.refuse(404, 'not found')
        key = hashlib.sha256(f'{name}|{relative}|{qa}|{finished}'.encode()).hexdigest()[:24]
        copy = self.server.histograms / (key + '.root')
        with self.server.copy_lock:
            if not copy.is_file():
                # Check against the session now: its container and its latest QA result.
                latest = session_file(name, 'qa/latest.json')
                try:
                    latest = json.loads(latest) if latest else {}
                except ValueError:
                    latest = {}
                if (latest.get('histograms'), latest.get('id'), latest.get('finished_utc')) != (relative, qa, finished):
                    return self.refuse(409, 'The QA result changed; reload the page.')
                container = docker('inspect', '--format', '{{.Id}}', name).stdout.strip()
                for old in self.server.histograms.glob('*'):
                    old.unlink()
                part = self.server.histograms / (key + '.part')
                p = docker('cp', f'{container or name}:/tmp/session-run/{relative}', str(part), timeout=300)
                if p.returncode or not container:
                    part.unlink(missing_ok=True)
                    return self.refuse(404, 'not found')
                os.replace(part, copy)
            data = copy.read_bytes()
        header = self.headers.get('Range', '')
        ranges = re.findall(r'(\d+)-(\d*)', header) if header.startswith('bytes=') else []
        spans = [(int(a), min(int(b) if b else len(data) - 1, len(data) - 1)) for a, b in ranges]
        if len(spans) == 1:
            start, end = spans[0]
            return self.send_body(206, data[start:end + 1], 'application/octet-stream',
                                  {'Content-Range': f'bytes {start}-{end}/{len(data)}', 'Accept-Ranges': 'bytes'})
        if spans:
            # JSROOT reads several parts of the file in one request.
            boundary = 'qa-page-ranges'
            body = b''.join(f'--{boundary}\r\nContent-Type: application/octet-stream\r\nContent-Range: bytes {a}-{b}/{len(data)}'
                            f'\r\n\r\n'.encode() + data[a:b + 1] + b'\r\n' for a, b in spans) + f'--{boundary}--\r\n'.encode()
            return self.send_body(206, body, f'multipart/byteranges; boundary={boundary}', {'Accept-Ranges': 'bytes'})
        self.send_body(200, data, 'application/octet-stream', {'Accept-Ranges': 'bytes'})


def refresher(server):
    """Re-run QA of the running run every few seconds; never two at once."""
    while server.session and not server.stopping.wait(server.refresh):
        state = read_state(server.session)
        if not state.get('running') or state.get('rc_state') != 'RUNNING':
            continue
        # The same QA as the command; marked as made by the page so only its newest results are kept.
        server.refresh_started += 1
        docker('exec', server.session, 'python', '-m', 'session_setup.analyze', '--from-page', timeout=3600)
        server.refresh_runs += 1


def url(port, name, tab=None, token=None):
    # The server command opens the experiment view first (the setup tab without a session); the QA
    # command opens the QA tab. The token follows '#': the browser does not send it to the server, and
    # the page removes it from the address.
    query = ([f'session={name}'] if name else []) + ([f'tab={tab if name else "setup"}'] if tab or not name else [])
    return f'http://127.0.0.1:{port}/?' + '&'.join(query) + (f'#token={token}' if token else '')


def private_write(path, text):
    """Write a file only the owner can read (it may hold the page token)."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(descriptor, 'w') as out:
        out.write(text)


def answering(port):
    """What a page server on the port says about itself (/api/server), or None when no page server answers."""
    try:
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        c.request('GET', '/api/server')
        r = c.getresponse()
        answer = json.loads(r.read())
        c.close()
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if r.status == 200 and isinstance(answer, dict) and answer.get('server') == 'qa-page' and isinstance(answer.get('pid'), int):
        return answer
    return None


def serves_port(pid, port):
    """Whether the process runs this page server for the port, judged by its command line."""
    p = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], text=True, capture_output=True)
    words = p.stdout.split()
    if p.returncode or 'session_setup.webpage' not in words or 'serve' not in words:
        return False
    if '--port' in words:
        index = words.index('--port')
        return index + 1 < len(words) and words[index + 1] == str(port)
    return port == DEFAULT_PORT


def earlier_state_files(port):
    """The state and log files of earlier versions, which kept them in the temporary directory."""
    old = Path(os.environ.get('TMPDIR', '/tmp')) / f'test-beam-sandbox-page-{port}.json'
    return [old, old.with_suffix('.log')]


def stop_command(port):
    return 'scripts/qa_page.sh --stop' + ('' if port == DEFAULT_PORT else f' --port {port}')


def running(port, clean=True):
    """The page server recorded for this port, if that server itself answers on the port.

    A process number alone is not trusted: after a crash or a restart it may belong to another
    program. A record that the server does not confirm is removed and no signal is sent.
    """
    path = state_file(port)
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        c.request('GET', '/api/server')
        r = c.getresponse()
        answer = json.loads(r.read())
        c.close()
        if r.status == 200 and answer.get('server') == 'qa-page' and answer.get('pid') == info.get('pid'):
            return info
    except (OSError, ValueError, http.client.HTTPException):
        pass
    if clean:
        path.unlink(missing_ok=True)
        path.with_suffix('.log').unlink(missing_ok=True)
        print(f'Removed a stale state file for port {port}: process {info.get("pid")} is not a result page server '
              'on that port; no process was signalled.')
    return None


def open_browser(address, allowed):
    if not allowed:
        return
    if not webbrowser.open(address):
        print('No browser could be opened; open the address yourself.')


def serve(args, browser=False):
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'test-beam-sandbox'
    cache.mkdir(parents=True, exist_ok=True)
    try:
        server = Server(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        reason = 'it is already in use' if exc.errno == errno.EADDRINUSE else exc.strerror
        print(f'Cannot use port {args.port} on 127.0.0.1: {reason}. Choose another with --port.', file=sys.stderr)
        return 2
    server.session, server.refresh, server.cache = args.name, args.refresh, cache
    server.jsroot = jsroot_directory(cache)
    server.stopping = threading.Event()
    server.refresh_runs = server.refresh_started = 0
    server.copy_lock = threading.Lock()
    # Copies of QA files live only as long as this server.
    (cache / 'histograms').mkdir(exist_ok=True)
    server.histograms = Path(tempfile.mkdtemp(prefix=f'server-{args.port}-', dir=cache / 'histograms'))
    # A fresh token for page operations; none for a view-only server.
    server.token = None if args.view_only else secrets.token_urlsafe(32)
    server.jobs, server.jobs_lock = [], threading.Lock()
    private_write(state_file(args.port), json.dumps({'pid': os.getpid(), 'port': args.port, 'session': args.name,
                                                     'token': server.token}))

    def stop(*_):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    if args.refresh:
        threading.Thread(target=refresher, args=(server,), daemon=True).start()
    print(f'Result page: {url(args.port, args.name, "view", server.token)}', flush=True)
    if browser:
        threading.Timer(1.0, open_browser, (url(args.port, args.name, 'view', server.token), True)).start()
    if server.token is None:
        print('View only: page operations are off.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
        shutil.rmtree(server.histograms, ignore_errors=True)
        try:
            if json.loads(state_file(args.port).read_text()).get('pid') == os.getpid():
                state_file(args.port).unlink()
                state_file(args.port).with_suffix('.log').unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    print('Result page stopped.')
    return 0


def start(args, quiet=False, tab='view'):
    """Start the server in the background unless it already runs; say how to stop it."""
    info = running(args.port)
    if info:
        if not quiet:
            print(f'Result page already running (process {info["pid"]}): {url(args.port, args.name, tab, info.get("token"))}')
        else:
            print(f'Result page: {url(args.port, args.name, tab, info.get("token"))}')
        return 0
    other = answering(args.port)
    if other:
        # A page server without a state file here, such as one an earlier version started.
        print(f'A result page server already runs on port {args.port} (process {other["pid"]}), but this command has no '
              f'record of it. Stop it with: {stop_command(args.port)}', file=sys.stderr)
        return 2
    log = state_file(args.port).with_suffix('.log')
    command = [sys.executable, '-m', 'session_setup.webpage', 'serve', *([args.name] if args.name else []),
               '--port', str(args.port)]
    if args.refresh:
        command += ['--refresh', str(args.refresh)]
    if args.view_only:
        command += ['--view-only']
    private_write(log, '')
    with log.open('w') as out:
        process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
    for _ in range(300):
        if process.poll() is not None:
            print(log.read_text().strip(), file=sys.stderr)
            log.unlink(missing_ok=True)
            return 2
        info = running(args.port, clean=False)
        if info:
            break
        time.sleep(0.1)
    print(f'Result page: {url(args.port, args.name, tab, (info or {}).get("token"))}')
    print(f'It runs in the background as process {process.pid}; stop it with: scripts/qa_page.sh --stop'
          + ('' if args.port == DEFAULT_PORT else f' --port {args.port}'))
    if args.refresh:
        print(f'QA of the running run is repeated every {args.refresh} s while the page server runs.')
    return 0


def stop_server(args):
    info = running(args.port)
    if not info:
        other = answering(args.port)
        if not other:
            print(f'No result page server runs on port {args.port}.')
            return 0
        # No state file, but the port answers as a page server: stop it only if that process runs this server.
        if not serves_port(other['pid'], args.port):
            print(f'Port {args.port} answers as a result page server, but process {other["pid"]} does not run one on that '
                  'port; no process was signalled.')
            return 1
        info = {'pid': other['pid']}
    try:
        os.kill(info['pid'], signal.SIGTERM)
    except OSError as exc:
        print(f'Cannot stop process {info["pid"]}: {exc.strerror}; no process was signalled.')
        return 1
    for _ in range(100):
        try:
            os.kill(info['pid'], 0)
        except OSError:
            break
        time.sleep(0.1)
    for path in earlier_state_files(args.port):
        path.unlink(missing_ok=True)
    print(f'Stopped the result page server (process {info["pid"]}).')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scripts/qa_page.sh', description=__doc__)
    parser.add_argument('command', nargs='?', default='start', choices=('start', 'serve', 'open', 'stop'))
    parser.add_argument('name', nargs='?', default=None, help='session container (default: none; the page opens at the setup tab)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'port on 127.0.0.1 (default {DEFAULT_PORT})')
    parser.add_argument('--refresh', type=int, default=0, help='repeat QA of the running run every SECONDS (default off)')
    parser.add_argument('--no-browser', action='store_true', help='do not open a browser')
    parser.add_argument('--view-only', action='store_true', help='refuse every page operation (no token is made)')
    args = parser.parse_args(argv)
    if args.name is not None and not NAME.match(args.name):
        parser.error('invalid container name')
    if args.command == 'open' and args.name is None:
        parser.error('open needs a container name')
    if args.refresh and args.refresh < 10:
        parser.error('--refresh needs at least 10 seconds')
    browser = sys.stdout.isatty() and not args.no_browser
    if args.command == 'serve':
        # In the foreground (Ctrl-C stops it); the browser opens once the server listens.
        return serve(args, browser)
    if args.command == 'stop':
        return stop_server(args)
    tab = 'qa' if args.command == 'open' else 'view'
    code = start(args, quiet=args.command == 'open', tab=tab)
    if code == 0:
        info = running(args.port, clean=False) or {}
        open_browser(url(args.port, args.name, tab, info.get('token')), browser)
    return code


if __name__ == '__main__':
    sys.exit(main())
