# SPDX-License-Identifier: LGPL-3.0-or-later
"""Align the analysis geometry with the public Corryvreckan alignment modules.

Runs Prealignment, then Tracking4D with AlignmentTrackChi2 (reference planes), then
DUTAssociation with AlignmentDUTResidual (DUTs), on a complete-event snapshot of a run.
The result replaces /tmp/session-run/alignment only when every step succeeded.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from session_setup import aligned
from session_setup.setupfile import geometry_fingerprint

root = Path('/tmp/session-run')
WAIT_EVENTS = 3000
WAIT_SECONDS = 300
MIN_TRACKS = 100
LIBRARIES = '/tmp/libmsensor_status.so:/opt/eudaq/lib/libeudaq_module_ITS3.so:/opt/eudaq/lib/libeudaq_module_msensor.so'


def fail(message, code=2):
    print('Alignment not done: ' + message, file=sys.stderr)
    raise SystemExit(code)


def collector_events():
    try:
        status = json.loads((root / 'status.json').read_text())
    except (OSError, ValueError):
        return None, None
    dc = next((c for c in status.get('connections', []) if c.get('role') == 'collector'), None)
    return status.get('rc_state'), (dc or {}).get('data_events')


def choose_raw(run):
    raws = list(root.glob('run*.raw'))
    if run is not None:
        if not run.isdecimal():
            fail('run number must contain digits only')
        raws = [p for p in raws if re.fullmatch(r'run0*' + re.escape(str(int(run))) + r'\.raw', p.name)]
    if not raws:
        fail('no matching raw file was found; turn the beam on and acquire data first')
    return max(raws, key=lambda p: p.stat().st_mtime_ns)


def snapshot(raw, work):
    capture, out = work / 'capture.raw', work / 'snapshot.raw'
    with raw.open('rb') as source, capture.open('xb') as output:
        remaining = os.fstat(source.fileno()).st_size
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                fail('the raw file changed while it was copied')
            output.write(block)
            remaining -= len(block)
    p = subprocess.run(['python', '-m', 'session_setup.snapshot', str(capture), str(out)], text=True, capture_output=True)
    capture.unlink()
    if p.returncode:
        fail('EUDAQ could not make a complete-event snapshot: ' + (p.stdout + p.stderr)[-1000:])
    return out, json.loads(p.stdout.splitlines()[-1])['data_events']


def corry(work, name, geometry, updated, raw, names, body):
    text = (f'[Corryvreckan]\nlog_level = "INFO"\ndetectors_file = "{geometry}"\n'
            + (f'detectors_file_updated = "{updated}"\n' if updated else '')
            + f'histogram_file = "{name}.root"\nnumber_of_events = -1\nlibrary_directories = "/tmp"\n'
            f'[EventDefinitionRawTrigger]\nfile_name = "{raw}"\n')
    for n in names:
        text += f'[EventLoaderEUDAQ2]\nname = "{n}"\nidentifier = "{n}"\nfile_name = "{raw}"\n'
    text += '[ClusteringSpatial]\nuse_trigger_timestamp = true\n' + body
    config = work / f'{name}.conf'
    config.write_text(text)
    p = subprocess.run(['corry', '-c', str(config)], cwd=work, text=True, capture_output=True,
                       env=dict(os.environ, LD_PRELOAD=LIBRARIES))
    (work / f'{name}.log').write_text(p.stdout + p.stderr)
    if p.returncode or (updated and not Path(updated).is_file()):
        fail(f'the {name} step of Corryvreckan failed; see {work / (name + ".log")}')


def histograms(path, references, duts):
    p = subprocess.run(['python', '-m', 'session_setup.histograms', str(path), ','.join(references), ','.join(duts)],
                       text=True, capture_output=True)
    if p.returncode:
        fail('cannot read ' + str(path) + ': ' + p.stderr[-500:])
    return json.loads(p.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', help='EUDAQ run number to use; defaults to the current or latest run')
    args = parser.parse_args()
    if shutil.which('corry') is None:
        fail('Corryvreckan is not installed in this acquisition-only image')
    nominal_path = root / 'analysis-geometry.conf'
    if not nominal_path.is_file():
        fail('the analysis geometry is missing')
    nominal = nominal_path.read_text()
    sections = aligned.sections(nominal)
    duts = [n for n, lines in sections if (aligned.value(lines, 'role') or '').strip('"').lower() == 'dut']
    references = [n for n, _ in sections if n not in duts]
    if len(references) < 3:
        fail(f'{len(references)} reference plane(s); alignment needs tracks from at least 3')
    min_hits = max(3, min(5, len(references) - 1))
    state, events = collector_events()
    if args.run is None and state not in ('TERMINATED', 'STOPPED') and (state != 'RUNNING' or (events or 0) < WAIT_EVENTS):
        # A session that is starting or running: wait for enough data in its current run.
        print(f'Waiting for {WAIT_EVENTS} data events in the current run (now {events or 0}) ...', flush=True)
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end and (state != 'RUNNING' or (events or 0) < WAIT_EVENTS):
            time.sleep(1)
            state, events = collector_events()
        if state == 'RUNNING' and (events or 0) < WAIT_EVENTS or state not in ('RUNNING', 'TERMINATED', 'STOPPED'):
            fail(f'the current run has {events or 0} data events after {WAIT_SECONDS} s; '
                 'turn the beam on or wait, then align again')
    raw = choose_raw(args.run)
    started = time.monotonic()
    ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6]
    work = root / 'alignment-work' / ident
    work.mkdir(parents=True)
    for line in nominal.splitlines():
        match = re.match(r'\s*mask_file\s*=\s*"([^"]+)"', line)
        if match:
            shutil.copyfile(root / match.group(1), work / match.group(1))
    shutil.copyfile(nominal_path, work / 'nominal.conf')
    subprocess.run(['bash', '/opt/public-work/integration/build_modules.sh', '/opt/public-work/integration', '/tmp'],
                   check=True, capture_output=True)
    snap, data_events = snapshot(raw, work)
    print(f'Aligning with run {raw.name}: {data_events} complete data events.', flush=True)
    names = duts + references
    tracking = (f'[Tracking4D]\ntime_cut_abs = 1us\nspatial_cut_abs = {{cut}}, {{cut}}\nmin_hits_on_track = {min_hits}\n'
                'exclude_dut = true\n')
    corry(work, 'prealign', work / 'nominal.conf', work / 'step1.conf', snap, names,
          '[Prealignment]\nrange_abs = 20mm\nmethod = "maximum"\n')
    corry(work, 'telescope', work / 'step1.conf', work / 'step2.conf', snap, names,
          tracking.format(cut='500um') + '[AlignmentTrackChi2]\niterations = 4\nalign_orientation = false\n'
          'max_track_chi2ndof = 20\n')
    telescope = histograms(work / 'output' / 'telescope.root', references, [])
    if telescope['tracks'] < MIN_TRACKS:
        fail(f'only {telescope["tracks"]} tracks were found for the reference-plane alignment; at least '
             f'{MIN_TRACKS} are needed. Acquire more data with the beam on, then align again.')
    final = work / 'step2.conf'
    if duts:
        corry(work, 'dut', final, work / 'step3.conf', snap, names,
              tracking.format(cut='200um') + '[DUTAssociation]\nspatial_cut_abs = 300um, 300um\ntime_cut_abs = 1us\n'
              '[AlignmentDUTResidual]\niterations = 4\nalign_orientation = false\nalign_position_axes = "xy"\n'
              'max_track_chi2ndof = 20\n')
        final = work / 'step3.conf'
    found = aligned.positions(final.read_text())
    result_geometry = aligned.compose(nominal, found)
    (work / 'aligned.conf').write_text(result_geometry)
    checks = {}
    for label, geometry in (('before', work / 'nominal.conf'), ('after', work / 'aligned.conf')):
        body = tracking.format(cut='200um')
        if duts:
            body += ('[DUTAssociation]\nspatial_cut_abs = 100um, 100um\ntime_cut_abs = 1us\n'
                     '[AnalysisDUT]\ntime_cut_frameedge = 0ns\nchi2ndof_cut = 100\nn_time_bins = 2000\ntime_binning = 1ns\n')
        corry(work, 'check-' + label, geometry, None, snap, names, body)
        checks[label] = histograms(work / 'output' / f'check-{label}.root', references, duts)
    if checks['after']['tracks'] < MIN_TRACKS:
        fail(f'the aligned geometry gives only {checks["after"]["tracks"]} tracks; at least {MIN_TRACKS} are needed. '
             'Acquire more data with the beam on, then align again.')
    corrections = {}
    for name, lines in sections:
        before = aligned.millimetres(aligned.value(lines, 'position'))
        after = aligned.millimetres(found[name]['position'])
        corrections[name] = [round((after[i] - before[i]) * 1000, 1) for i in (0, 1)]
    seconds = round(time.monotonic() - started, 1)
    result = {'format': 'session-alignment/1', 'id': ident, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'run': raw.name, 'data_events': data_events, 'fingerprint': geometry_fingerprint(nominal),
              'positions': found, 'reference_planes': references, 'duts': duts,
              'corrections_um': corrections, 'tracks_per_event_before': checks['before']['tracks_per_event'],
              'tracks_per_event_after': checks['after']['tracks_per_event'], 'seconds': seconds,
              'modules': ['Prealignment (range_abs 20mm, method maximum)',
                          'Tracking4D (spatial cut 500um) + AlignmentTrackChi2 (4 iterations, positions only)',
                          'Tracking4D (spatial cut 200um) + DUTAssociation (300um) + AlignmentDUTResidual '
                          '(4 iterations, x and y only)'],
              'geometry': result_geometry}
    staged = root / ('.alignment-' + ident)
    staged.mkdir()
    (staged / 'alignment.json').write_text(json.dumps(result, indent=2) + '\n')
    (staged / 'aligned-geometry.conf').write_text(result_geometry)
    old = root / 'alignment'
    # The work folder is removed below; its Corryvreckan configuration files stay with the result, with their
    # paths pointing to the copies.
    (staged / 'corryvreckan').mkdir()
    for conf in sorted(work.glob('*.conf')):
        (staged / 'corryvreckan' / conf.name).write_text(conf.read_text().replace(str(work), str(old / 'corryvreckan')))
    if old.exists():
        shutil.rmtree(old)
    staged.rename(old)
    print(f'Alignment {ident} from {raw.name} ({data_events} data events, {seconds} s).')
    print('Corrections found, relative to the setup positions (um); the reference plane stays fixed:')
    print(f'  {"Detector":<12} {"x":>9} {"y":>9}   residual rms x, y after (um)')
    for name in names:
        rms = checks['after']['residuals'].get(name, {})
        width = ', '.join(f'{rms[a]["rms_um"]:.1f}' if rms.get(a) else '-' for a in ('x', 'y'))
        role = ' (reference)' if (aligned.value(dict(sections)[name], 'role') or '').strip('"') == 'reference' else ''
        print(f'  {name:<12} {corrections[name][0]:>9.1f} {corrections[name][1]:>9.1f}   {width}{role}')
    print(f'Tracks per event: {checks["before"]["tracks_per_event"]:.3f} with the setup positions, '
          f'{checks["after"]["tracks_per_event"]:.3f} after alignment.')
    print(f'Corryvreckan configuration: {old / "corryvreckan"}/ (in the container)')
    print('QA now uses this alignment. Save it for later sessions with --save FILE.')
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
