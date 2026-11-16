# SPDX-License-Identifier: LGPL-3.0-or-later
"""Validate an entire configuration before the device applies any changes."""
import hashlib
import json
import math
from pathlib import Path

DEFAULTS = dict(regions=1, clock_mode='virtual', sim_step_ns=1000000, pace=1.0,
                source='synthetic', deterministic_ids=0, window_guard_before=1,
                window_guard_after=1, beam_sigma_px=0.0)
REQUIRED = {'plane', 'columns', 'rows', 'seed', 'readout_mode', 'threshold_dac'}
ALLOWED = set(DEFAULTS) | REQUIRED | {
    'stream_dir', 'response_file', 'strobe_delay_ns', 'strobe_length_ns',
    'window_length_ns', 'trigger_rate_hz', 'trigger_period_ns', 'trigger_first_ns',
    'trigger_times_ns', 'beam_center_col', 'beam_center_row', 'sim_end_ns',
    'EUDAQ_DC', 'EUDAQ_ID', 'tiles_x', 'tiles_y', 'tiles_off'}


def validate(settings):
    supplied = dict(settings)
    unknown = set(supplied) - ALLOWED
    if unknown or REQUIRED - set(supplied):
        raise ValueError(f'unknown={sorted(unknown)} missing={sorted(REQUIRED-set(supplied))}')
    c = DEFAULTS | supplied

    def integer(key, lo, hi):
        x = c[key]
        if isinstance(x, bool) or not isinstance(x, int) or not lo <= x <= hi:
            raise ValueError(key)

    def real(key, minimum, strict=False):
        x = c[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or (x <= minimum if strict else x < minimum):
            raise ValueError(key)

    for k, a, b in [('plane', 0, 2**32-1), ('columns', 1, 4096), ('rows', 1, 4096),
                    ('regions', 1, 64), ('seed', 0, 2**63-1), ('sim_step_ns', 1, 2**63-1),
                    ('deterministic_ids', 0, 1)]:
        integer(k, a, b)
    if c['columns'] % c['regions']:
        raise ValueError('regions')
    # A tile grid divides the matrix; each tile has its own threshold and can be
    # switched off. With more than one tile, tiles replace column regions.
    c.setdefault('tiles_x', 1); c.setdefault('tiles_y', 1); c.setdefault('tiles_off', [])
    integer('tiles_x', 1, 64); integer('tiles_y', 1, 64)
    tiles = c['tiles_x'] * c['tiles_y']
    if c['columns'] % c['tiles_x'] or c['rows'] % c['tiles_y']:
        raise ValueError('tiles must divide columns and rows')
    if tiles > 1 and c['regions'] != 1:
        raise ValueError('regions must be 1 when tiles are used')
    off = c['tiles_off']
    if not isinstance(off, list) or any(isinstance(t, bool) or not isinstance(t, int) or not 0 <= t < tiles for t in off) or len(set(off)) != len(off):
        raise ValueError('tiles_off')
    zones = tiles if tiles > 1 else c['regions']
    if c['clock_mode'] not in ('virtual', 'paced') or c['source'] not in ('synthetic', 'session'):
        raise ValueError('clock_mode or source')
    real('pace', 0, True)
    real('beam_sigma_px', 0)
    for k in ('beam_center_col', 'beam_center_row'):
        if k not in c:
            c[k] = c['columns' if k.endswith('col') else 'rows'] / 2
        real(k, -float('inf'))
    mode = c['readout_mode']
    if mode not in ('triggered', 'continuous'):
        raise ValueError('readout_mode')
    ts = c['threshold_dac']
    ts = [ts] * zones if isinstance(ts, int) and not isinstance(ts, bool) else ts
    if not isinstance(ts, list) or len(ts) != zones or any(isinstance(t, bool) or not isinstance(t, int) or not 0 <= t <= 255 for t in ts):
        raise ValueError('threshold_dac')
    c['threshold_dac'] = ts
    if mode == 'triggered':
        if any(k in supplied for k in ('window_length_ns', 'window_guard_before', 'window_guard_after', 'stream_dir')):
            raise ValueError('continuous setting in triggered mode')
        for k, a, b in [('strobe_delay_ns', 0, 1000000), ('strobe_length_ns', 1, 1000000)]:
            if k not in c:
                raise ValueError(k)
            integer(k, a, b)
        c.pop('window_guard_before'); c.pop('window_guard_after')
    else:
        if any(k in supplied for k in ('strobe_delay_ns', 'strobe_length_ns')) or not isinstance(c.get('stream_dir'), str) or not c.get('stream_dir') or 'window_length_ns' not in c:
            raise ValueError('continuous settings')
        for k, a, b in [('window_length_ns', 1000, 10000000), ('window_guard_before', 0, 8), ('window_guard_after', 0, 8)]:
            integer(k, a, b)
    modes = sum(k in supplied for k in ('trigger_rate_hz', 'trigger_period_ns', 'trigger_times_ns'))
    if c['source'] == 'synthetic':
        if modes != 1 or ('trigger_first_ns' in supplied) != ('trigger_period_ns' in supplied):
            raise ValueError('trigger source')
    elif c['clock_mode'] != 'virtual' or modes or 'trigger_first_ns' in supplied or 'sim_end_ns' in supplied:
        raise ValueError('session source requires session-controlled virtual time')
    if 'trigger_rate_hz' in c:
        real('trigger_rate_hz', 0)
    if 'trigger_period_ns' in c:
        integer('trigger_period_ns', 1, 2**63-1); integer('trigger_first_ns', 1, 2**63-1)
    if 'trigger_times_ns' in c:
        t = c['trigger_times_ns']
        if not isinstance(t, list) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in t) or t != sorted(t):
            raise ValueError('trigger_times_ns')
    if 'sim_end_ns' in c:
        integer('sim_end_ns', 0, 2**63-1)
    response = Path(c.get('response_file', Path(__file__).with_name('response_default.json')))
    with response.open(encoding='utf-8') as f:
        params = json.load(f)
    names = ('e_max', 't_half', 'w_e', 'r0', 't_n', 'w_n', 'fake_rate_max', 'reference_duration_ns', 'response_delay_ns', 'jitter_ns')
    if any(k not in params or isinstance(params[k], bool) or not isinstance(params[k], (int, float)) or not math.isfinite(params[k]) for k in names):
        raise ValueError('response_file')
    if not (0 <= params['e_max'] <= 1 and params['w_e'] > 0 and params['w_n'] > 0 and params['r0'] >= 0 and params['fake_rate_max'] >= 0 and params['reference_duration_ns'] > 0 and params['response_delay_ns'] >= 0 and params['jitter_ns'] >= 0):
        raise ValueError('response values')
    detector_keys = ('plane', 'columns', 'rows', 'regions', 'readout_mode', 'threshold_dac', 'strobe_delay_ns', 'strobe_length_ns', 'window_length_ns', 'window_guard_before', 'window_guard_after')
    detector = {k: c[k] for k in detector_keys if k in c}
    if tiles > 1 or off:
        detector.update(tiles_x=c['tiles_x'], tiles_y=c['tiles_y'], tiles_off=sorted(off))
    detector['response'] = {k: params[k] for k in names}
    canonical = json.dumps(detector, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return c, params, detector, hashlib.sha256(canonical).hexdigest()
