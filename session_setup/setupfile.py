# SPDX-License-Identifier: LGPL-3.0-or-later
"""Create, show and check a session setup file, and prepare a session from it."""
import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import textwrap

from integration.layout import (ALPIDE_DEFAULTS, BEAM_DEFAULTS, MSENSOR_DEFAULTS, OPERATOR_EVENTS,
                                EditedFilesError, generate)

from integration.resolution import BUDGET_LIMIT, estimate, material_budget

KINDS = ('alpide', 'msensor')
# QA reconstructs tracks from the setup's reference planes.
QA_MIN_REFERENCE_PLANES = 3
# A wide beam (sigma 5 mm in x and y) reaches every tile of the default msensor.
BEAM_NOMINAL = {'position_mm': [1.1, -2.1, -20], 'spread_mm2': [25, 25], 'rate_hz': 220}
WIDTH = 80
EXAMPLE_NEEDS_MSENSOR = ('The finite example (--example) needs an msensor: its producer reports when the example '
                         'source has finished. Start this setup without --example.')


def qa_min_hits(reference_planes):
    """Hits a track needs: five as before, fewer only when fewer reference planes exist."""
    return max(QA_MIN_REFERENCE_PLANES, min(5, reference_planes - 1))


class Item:
    def __init__(self, kind, default, help, low=None, high=None, count=1, choices=None, required=False, render=True):
        # count: a number of values, 'list' (one or more) or 'optional list' (none, one or more)
        self.kind, self.default, self.help = kind, default, help
        self.low, self.high, self.count, self.choices, self.required = low, high, count, choices, required
        self.render = render

    def accepted(self):
        if self.kind == 'expr':
            return 'an expression of trg0 to trg3, bsy and comparisons of dt_trg, dt_veto or ntrg, joined with &, | and !'
        if self.kind == 'counter':
            return 'one to sixteen groups of four characters 0, 1, x or R, for inputs 3 to 0 from left to right'
        if self.choices:
            return 'one of: ' + ', '.join(self.choices)
        noun = 'integer' if self.kind == 'int' else 'number'
        if self.count == 'list':
            noun = 'one or more comma-separated ' + noun + 's'
        elif self.count == 'optional list':
            noun = 'empty, or comma-separated ' + noun + 's'
        elif self.count > 1:
            noun = str(self.count) + ' comma-separated ' + noun + 's'
        if self.low is not None and self.high is not None:
            return noun + ' from ' + _text(self.low) + ' to ' + _text(self.high)
        return noun

    def parse(self, raw):
        if self.kind in ('expr', 'counter'):
            return raw.strip()
        if self.choices:
            value = raw.strip()
            if value not in self.choices:
                raise ValueError
            return value
        if self.count == 'optional list' and raw.strip().lower() in ('', 'none'):
            return []
        parts = [x.strip() for x in raw.split(',')]
        if isinstance(self.count, int) and len(parts) != self.count:
            raise ValueError
        values = []
        for part in parts:
            if self.kind == 'int':
                if not re.fullmatch(r'[+-]?\d+', part):
                    raise ValueError
                value = int(part)
            else:
                value = int(part) if re.fullmatch(r'[+-]?\d+', part) else float(part)
                if not math.isfinite(value):
                    raise ValueError
            if (self.low is not None and value < self.low) or (self.high is not None and value > self.high):
                raise ValueError
            values.append(value)
        if self.count == 'list':
            return values[0] if len(values) == 1 else values
        return values[0] if self.count == 1 else values


def _text(value):
    if isinstance(value, list):
        return ', '.join(_text(x) for x in value)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return repr(value)
    return str(value)


RUN = {'events': Item('int', OPERATOR_EVENTS, 'events per run (NEVENTS) before RunControl starts the next run; '
                      'the finite example (--example) ends with its source instead', 1, 1000000000)}
DAQ = Item('choice', 'eudaq', 'DAQ framework of the session; nestdaq needs the nestDAQ image (scripts/build_session.sh --nestdaq)', choices=('eudaq', 'nestdaq'))
NESTDAQ = {
    'input': Item('choice', 'file', 'how the msensor data reach nestDAQ', choices=('file', 'socket')),
    'frame_ms': Item('int', 100, 'length of one msensor stream frame in ms', 10, 1000),
    'send_buffer_mb': Item('int', 64, 'TCP send queue limit of each msensor runner in MB (input = socket)', 1, 1024),
    'trigger_board': Item('choice', 'off', 'add a virtual trigger board: every msensor then gets a periodic trigger instead of one for each particle, a board stream records the periodic and the physics triggers, and scintillators and [trigger] become available; off keeps the msensor triggers of each particle', choices=('off', 'on')),
    'periodic_clk': Item('int', 1005000, 'period of the periodic trigger in cycles of the 100.5 MHz board clock (1005000 is 10 ms); only with trigger_board = on', 10000, 1005000000),
    'generator_mb_s': Item('number', 0, 'load test of the data path: above 0, every msensor runner is replaced by a generator that writes valid frames at about this many MB/s per msensor, without the beam simulation; 0 runs the simulation', 0, 500, render=False),
}
NESTDAQ_NOTE = 'nestDAQ mode: the msensor data go to nestDAQ; events is ignored, the operator starts and stops runs; QA, alignment and analysis are not available in this mode; scintillators and [trigger] need trigger_board = on.'
BEAM = {
    'particle': Item('choice', BEAM_DEFAULTS['particle'], 'beam particle; the session core simulates electrons only',
                     choices=('e-',)),
    'energy_gev': Item('number', BEAM_DEFAULTS['energy_gev'], 'kinetic energy in GeV', 1, 6),
    'profile': Item('choice', 'gaussian', 'beam angles: gaussian has none; kek-2gev and kek-4gev use the angle widths measured at a KEK test beam line at 2 and 4 GeV', choices=('gaussian', 'kek-2gev', 'kek-4gev')),
    'position_mm': Item('number', BEAM_NOMINAL['position_mm'], 'nominal beam centre x, y and source z in mm; '
                        'the session varies x and y privately by up to 0.1 mm', -10000, 10000, count=3),
    'spread_mm2': Item('number', BEAM_NOMINAL['spread_mm2'], 'nominal variance of the beam position in x and y '
                       '(sigma squared, mm2)', 0.000001, 10000, count=2),
    'rate_hz': Item('number', BEAM_NOMINAL['rate_hz'], 'nominal particle rate in Hz; the session adds up to 20 Hz '
                    'privately', 1, 1000),
}
POSITIONS = {
    'offset_max_mm': Item('number', 5, 'the actual device positions differ from the positions in this file by up '
                          'to this much in x and in y, as after mounting; the offsets stay hidden and are found by '
                          'alignment; 0 switches them off', 0, 10),
    'offset_draw': Item('int', 0, 'another number draws another set of offsets for the same devices', 0, 1000000),
}
GEOMETRY = {
    'plane': Item('int', None, 'EUDAQ plane number, unique across all devices', 0, 63, required=True),
    'position_mm': Item('number', None, 'x, y, z of the sensor centre in mm; z is along the beam', -10000, 10000,
                        count=3, required=True),
    'pitch_um': Item('number', [30, 30], 'pixel pitch in micrometres', 1, 1000, count=2),
}
DEVICE = {
    'alpide': dict(GEOMETRY, **{
        'pixels': Item('int', [1024, 512], 'columns, rows; the public ALPIDE producer requires 1024, 512',
                       1, 4096, count=2),
        'ithr': Item('int', ALPIDE_DEFAULTS['ithr'], 'ITHR threshold DAC; higher values lower the virtual '
                     'efficiency and cluster size', 0, 255),
        'strobe_length': Item('int', ALPIDE_DEFAULTS['strobe_length'], 'STROBE_LENGTH in 25 ns ticks (readout gate)',
                              1, 4000),
    }),
    'msensor': dict(GEOMETRY, **{
        'role': Item('choice', 'dut', 'analysis role; reference planes need triggered readout', choices=('dut', 'reference')),
        'pitch_um': Item('number', [23, 21], 'pixel pitch in micrometres (x, y)', 1, 1000, count=2),
        'tiles': Item('int', [6, 2], 'tile grid: tiles along x, tiles along y; tiles are numbered from 0 '
                      'starting at column 0, row 0, along x first', 1, 64, count=2),
        'tile_pixels': Item('int', [156, 444], 'pixels of one tile: columns, rows', 1, 4096, count=2),
        'tiles_off': Item('int', [], 'tile numbers that are switched off (no hits, no noise); '
                          'empty for none', 0, 4095, count='optional list'),
        'pixels': Item('int', None, 'columns, rows of a single-tile matrix; instead of tiles and tile_pixels',
                       1, 4096, count=2, render=False),
        'threshold_dac': Item('int', MSENSOR_DEFAULTS['threshold_dac'], 'threshold DAC, one value for all tiles or '
                              'one per tile in tile order; higher values lower the efficiency', 0, 255, count='list'),
        'readout': Item('choice', MSENSOR_DEFAULTS['readout'], 'readout mode', choices=('continuous', 'triggered')),
        'window_length_ns': Item('int', MSENSOR_DEFAULTS['window_length_ns'], 'continuous readout window in ns',
                                 1000, 10000000),
        'strobe_delay_ns': Item('int', MSENSOR_DEFAULTS['strobe_delay_ns'], 'triggered readout strobe delay in ns',
                                0, 1000000),
        'strobe_length_ns': Item('int', MSENSOR_DEFAULTS['strobe_length_ns'], 'triggered readout strobe length in ns',
                                 1, 1000000),
        'clock_offset_ns': Item('int', 0, 'reading of this msensor clock in ns when the session clock reads 0; only with daq = nestdaq', 0, 1000000000, render=False),
        'clock_drift_ppm': Item('number', 0, 'how much faster this msensor clock runs, in parts per million; only with daq = nestdaq', -1000, 1000, render=False),
        'missed_triggers': Item('int', [], 'session trigger numbers, counted from 0 in every run, that this msensor does not see; only with daq = nestdaq', 0, 1000000000, count='optional list', render=False),
        'noise': Item('choice', 'off', 'noise hits of this msensor: on uses the default msensor response, whose noise rate grows as threshold_dac is lowered; only with daq = nestdaq', choices=('off', 'on'), render=False),
    }),
}
SCINTILLATOR = {
    'channel': Item('int', None, 'trigger board input channel', 0, 3, required=True),
    'position_mm': Item('number', None, 'plate centre x, y, z in mm', -10000, 10000, count=3, required=True),
    'shape': Item('choice', 'rectangle', 'plate shape', choices=('rectangle', 'disc')),
    'size_mm': Item('number', [30, 20], 'plate width and height, or disc diameter in mm', 1, 200, count='list'),
    'hole_mm': Item('number', [0, 0], 'central hole width and height, or diameter in mm; 0 means none', 0, 200, count='list'),
}
TRIGGER = {
    'trg': Item('expr', 'trg0&trg3&dt_trg>10000&dt_veto>5000&!bsy', 'condition that makes a trigger'),
    'veto': Item('expr', 'ntrg>0', 'condition that blocks triggers; empty disables veto'),
    'count': Item('counter', '1xxR Rxx1 xxxR Rxxx', 'conditions the board counts'),
}

CONTINUOUS_ONLY = ('window_length_ns',)
TRIGGERED_ONLY = ('strobe_delay_ns', 'strobe_length_ns')
SETTING_NAMES = {'alpide': ('ithr', 'strobe_length'),
                 'msensor': ('threshold_dac', 'readout', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns',
                             'tiles', 'tiles_off')}


class SetupError(ValueError):
    def __init__(self, errors):
        super().__init__('\n'.join(errors))
        self.errors = errors


def _defaults(items):
    return {k: (list(v.default) if isinstance(v.default, list) else v.default) for k, v in items.items()}


def _read_items(section, values, items, errors, given=None):
    result = {}
    for key, raw in values.items():
        if key not in items:
            errors.append(f'[{section}] {key}: unknown item; accepted items: ' + ', '.join(items))
            continue
        try:
            result[key] = items[key].parse(raw)
        except ValueError:
            errors.append(f'[{section}] {key} = {raw.strip()}: not accepted; expected {items[key].accepted()}')
    for key, item in items.items():
        if item.required and key not in values:
            errors.append(f'[{section}] {key}: required item is missing; expected {item.accepted()}')
    if given is not None:
        given.update(result)
    return result


def load(path):
    """Read a setup file (INI) or a layout file (JSON); raise SetupError with every problem found."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise SetupError([f'{path}: cannot be read ({exc.strerror})'])
    return load_text(text, path)


def load_text(text, path):
    """Check the text of a setup file named path, as load() does for the file."""
    path = Path(path)
    if path.suffix == '.json':
        return from_layout(text, str(path))
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=('#', ';'),
                                       inline_comment_prefixes=('#', ';'), default_section='\0')
    try:
        parser.read_string(text, source=str(path))
    except configparser.Error as exc:
        raise SetupError([f'{path}: {exc.message if hasattr(exc, "message") else exc}'.replace('\n', ' ')])
    errors = []
    setup = {'run': _defaults(RUN), 'beam': _defaults(BEAM), 'positions': _defaults(POSITIONS), 'devices': [], 'scintillators': [], 'trigger': None}
    nestdaq_values = None
    for section in parser.sections():
        values = dict(parser.items(section))
        if section == 'run' and 'daq' in values:
            raw = values.pop('daq')
            try:
                daq = DAQ.parse(raw)
                if daq == 'nestdaq': setup['run']['daq'] = daq
            except ValueError:
                errors.append(f'[run] daq = {raw.strip()}: not accepted; expected {DAQ.accepted()}')
        if section == 'nestdaq':
            nestdaq_values = _defaults(NESTDAQ)
            nestdaq_values.update(_read_items(section, values, NESTDAQ, errors))
            continue
        if section in ('run', 'beam', 'positions'):
            setup[section].update(_read_items(section, values, {'run': RUN, 'beam': BEAM, 'positions': POSITIONS}[section], errors))
            continue
        if section == 'trigger':
            setup['trigger'] = _defaults(TRIGGER)
            setup['trigger'].update(_read_items(section, values, TRIGGER, errors))
            continue
        match = re.fullmatch(r'(\S+)\s+(\S+)', section)
        if not match:
            errors.append(f'[{section}]: unknown section; use [run], [beam], [positions], [alpide N], [msensor N], [scintillator N] or [trigger]')
            continue
        kind, number = match.groups()
        if kind == 'scintillator':
            if not re.fullmatch(r'\d+', number):
                errors.append(f'[{section}]: scintillator number must be a whole number from 0')
                continue
            plate = _defaults(SCINTILLATOR)
            plate.update(_read_items(section, values, SCINTILLATOR, errors))
            plate['number'] = int(number)
            for field in ('size_mm', 'hole_mm'):
                if not isinstance(plate[field], list): plate[field] = [plate[field]]
            if 'size_mm' not in values and plate['shape'] == 'disc': plate['size_mm'] = [20]
            if plate['hole_mm'] == [0] and plate['shape'] == 'rectangle': plate['hole_mm'] = [0, 0]
            if 'hole_mm' not in values and plate['shape'] == 'disc': plate['hole_mm'] = [0]
            setup['scintillators'].append(plate)
            continue
        if kind not in KINDS:
            errors.append(f'[{section}]: unknown device kind "{kind}"; accepted kinds: ' + ', '.join(KINDS + ('scintillator',)))
            continue
        if not re.fullmatch(r'\d+', number):
            errors.append(f'[{section}]: device number "{number}" must be a whole number from 0')
            continue
        given = {}
        device = _defaults(DEVICE[kind])
        device.update(_read_items(section, values, DEVICE[kind], errors, given))
        device.update(kind=kind, number=int(number), given=sorted(given))
        setup['devices'].append(device)
    if not parser.sections():
        errors.append(f'{path}: no sections; a setup needs [run], [beam] and at least one device section')
    if setup['run'].get('daq', 'eudaq') == 'nestdaq':
        setup['nestdaq'] = nestdaq_values if nestdaq_values is not None else _defaults(NESTDAQ)
        if setup['nestdaq']['input'] == 'file':
            if parser.has_option('nestdaq', 'send_buffer_mb'):
                errors.append('[nestdaq] send_buffer_mb: only for input = socket')
            setup['nestdaq'].pop('send_buffer_mb')
        if setup['nestdaq']['trigger_board'] == 'off':
            if parser.has_option('nestdaq', 'periodic_clk'):
                errors.append('[nestdaq] periodic_clk: only with trigger_board = on')
            setup['nestdaq'].pop('trigger_board')
            setup['nestdaq'].pop('periodic_clk')
        else:
            if setup['nestdaq']['generator_mb_s'] > 0:
                errors.append('[nestdaq] trigger_board = on: not together with generator_mb_s')
            if any(d['kind'] == 'msensor' and d['readout'] != 'continuous' for d in setup['devices']):
                errors.append('[nestdaq] trigger_board = on: needs continuous readout for every msensor')
            for d in setup['devices']:
                w = d.get('window_length_ns')
                if d['kind'] == 'msensor' and d['readout'] == 'continuous' and isinstance(w, int) and setup['nestdaq']['periodic_clk'] * 2000000 < w * 201 * 1000:
                    errors.append(f'[msensor {d["number"]}] window_length_ns = {w}: with trigger_board = on, periodic_clk must make a period of at least one window')
            for d in setup['devices']:
                offset = d.get('clock_offset_ns', 0)
                if d['kind'] == 'msensor' and isinstance(offset, int) and offset * 2 * 100500000 >= setup['nestdaq']['periodic_clk'] * 1000000000:
                    errors.append(f'[msensor {d["number"]}] clock_offset_ns = {offset}: with trigger_board = on, the offset must be less than half of the periodic trigger period, or the periodic triggers cannot be matched to the board')
        if setup['nestdaq']['generator_mb_s'] == 0:
            setup['nestdaq'].pop('generator_mb_s')
        elif any(d['kind'] == 'msensor' and d['readout'] != 'continuous' for d in setup['devices']):
            errors.append('[nestdaq] generator_mb_s: needs continuous readout for every msensor')
        if not any(d['kind'] == 'msensor' for d in setup['devices']):
            errors.append('[run] daq = nestdaq: needs at least one [msensor N] section')
        if (setup['scintillators'] or setup['trigger'] is not None) and 'trigger_board' not in setup['nestdaq']:
            errors.append('[run] daq = nestdaq: scintillators and [trigger] are only available with [nestdaq] trigger_board = on; otherwise remove those sections (scripts/setup_session.sh new --scintillators 0 writes a setup without them)')
        frame = setup['nestdaq']['frame_ms'] * 1000000
        for d in setup['devices']:
            if d['kind'] == 'msensor' and d['readout'] == 'continuous':
                w = d['window_length_ns']
                if frame % w or frame // w > 65536:
                    errors.append(f'[msensor {d["number"]}] window_length_ns = {w}: with daq = nestdaq, frame_ms x 1000000 must be a multiple of it, at most 65536 windows')
    elif nestdaq_values is not None:
        errors.append('[nestdaq]: only for daq = nestdaq; add daq = nestdaq to [run] or remove this section')
    validate(setup, errors)
    if errors:
        raise SetupError(errors)
    return setup


def from_layout(text, name):
    """Accept the layout file format; settings, run length and beam take their default values."""
    errors = []
    try:
        layout = json.loads(text)
    except ValueError as exc:
        raise SetupError([f'{name}: not valid JSON ({exc})'])
    devices = layout.get('devices') if isinstance(layout, dict) else None
    if not isinstance(layout, dict) or set(layout) != {'devices'} or not isinstance(devices, list) or not devices:
        raise SetupError([f'{name}: a layout file must contain only a nonempty "devices" list'])
    setup = {'run': _defaults(RUN), 'beam': _defaults(BEAM), 'positions': _defaults(POSITIONS), 'devices': [], 'scintillators': [], 'trigger': None}
    for index, d in enumerate(devices):
        where = f'{name} devices[{index}]'
        fields = {'kind', 'number', 'plane', 'position_mm', 'pixels', 'pitch_um'}
        if not isinstance(d, dict) or set(d) != fields:
            errors.append(f'{where}: needs exactly the fields ' + ', '.join(sorted(fields)))
            continue
        if d['kind'] not in KINDS:
            errors.append(f'{where} kind "{d["kind"]}": unknown device kind; accepted kinds: ' + ', '.join(KINDS))
            continue
        if isinstance(d['number'], bool) or not isinstance(d['number'], int) or d['number'] < 0:
            errors.append(f'{where} number: expected a whole number from 0')
            continue
        section = f'{d["kind"]} {d["number"]}'
        values = {k: _text(d[k]) if isinstance(d[k], (list, int, float)) and not isinstance(d[k], bool) else '?'
                  for k in ('plane', 'position_mm', 'pixels', 'pitch_um')}
        device = _defaults(DEVICE[d['kind']])
        device.update(_read_items(section, values, DEVICE[d['kind']], errors))
        device.update(kind=d['kind'], number=d['number'], given=[])
        setup['devices'].append(device)
    validate(setup, errors)
    if errors:
        raise SetupError(errors)
    return setup


def profile_values(name):
    if name == 'gaussian':
        return None
    if name not in ('kek-2gev', 'kek-4gev'):
        raise ValueError('unknown beam profile')
    path = Path(__file__).resolve().parents[1] / 'configs' / 'beam-profiles' / (name + '.json')
    values = json.loads(path.read_text())
    return {key: values[key] for key in ('slope_sigma_mrad', 'slope_per_position_mrad_per_mm')}


def beam_covariance(vx, vy, values):
    covariance = [[0] * 5 for _ in range(5)]
    covariance[0][0], covariance[1][1] = vx, vy
    if values is not None:
        for axis, variance in enumerate((vx, vy)):
            sigma = values['slope_sigma_mrad'][axis] / 1000
            slope = values['slope_per_position_mrad_per_mm'][axis] / 1000
            covariance[axis + 2][axis + 2] = sigma ** 2 + slope ** 2 * variance
            covariance[axis][axis + 2] = covariance[axis + 2][axis] = slope * variance
    return covariance


def validate(setup, errors):
    devices = setup['devices']
    beam = setup['beam']
    if beam['profile'] != 'gaussian':
        values = profile_values(beam['profile'])
        vx, vy = beam['spread_mm2']
        covariance = beam_covariance(vx, vy, values)
        for axis, index in (('x', 2), ('y', 3)):
            spread = math.sqrt(covariance[index][index])
            if spread > 0.0075:
                errors.append(f'[beam]: profile = {beam["profile"]} with spread_mm2 = {_text(vx)}, {_text(vy)} gives an angle spread of {spread * 1000:.2f} mrad in {axis}, above the simulation limit 7.50 mrad; use a smaller spread_mm2')
                break
    plates = setup.get('scintillators', [])
    nums = sorted(p['number'] for p in plates)
    if len(plates) > 4 or nums != list(range(len(plates))):
        errors.append('[scintillator N]: at most 4 scintillators, numbered consecutively from 0')
    channels = [p['channel'] for p in plates if p['channel'] is not None]
    if len(set(channels)) != len(channels):
        errors.append('[scintillator N] channel: channels must be unique, from 0 to 3')
    for p in plates:
        section = f'[scintillator {p["number"]}]'
        count = 1 if p['shape'] == 'disc' else 2
        if len(p['size_mm']) != count or len(p['hole_mm']) != count:
            errors.append(f'{section} size_mm, hole_mm: {p["shape"]} expects {count} number(s), size 1 to 200 and hole smaller than size')
        elif any(h >= w for h, w in zip(p['hole_mm'], p['size_mm'])):
            errors.append(f'{section} hole_mm = {_text(p["hole_mm"])}: each hole size must be smaller than the plate')
        if p.get('position_mm') is not None and p['position_mm'][2] <= setup['beam']['position_mm'][2]:
            errors.append(f'{section} position_mm: plate z must be downstream of the beam source')
    trigger = setup.get('trigger')
    if trigger is not None and not plates:
        errors.append('[trigger]: requires at least one scintillator')
    if plates:
        if trigger is None:
            trigger = setup['trigger'] = _defaults(TRIGGER)
        from integration.trigger_logic import parse
        for key in ('trg', 'veto'):
            try:
                expression = parse(trigger[key], allow_empty=key == 'veto')
                missing = expression.channels - set(channels)
                if missing: raise ValueError('channels not present: ' + ', '.join('trg'+str(n) for n in sorted(missing)))
            except ValueError as exc:
                errors.append(f'[trigger] {key} = {trigger[key]}: not accepted; expected a valid expression using existing channels ({exc})')
        conditions = trigger['count'].split()
        if not 1 <= len(conditions) <= 16 or any(not re.fullmatch(r'[01xR]{4}', word) for word in conditions):
            errors.append(f"[trigger] count = {trigger['count']}: not accepted; expected 1 to 16 four-character conditions using 0, 1, x and R")
    if not devices:
        errors.append('setup: at least one [alpide N] or [msensor N] section is required')
        return
    for kind in KINDS:
        numbers = sorted(d['number'] for d in devices if d['kind'] == kind)
        if len(set(numbers)) != len(numbers):
            errors.append(f'[{kind} N]: the same {kind} number is used twice')
        elif numbers != list(range(len(numbers))):
            missing = sorted(set(range(max(numbers) + 1)) - set(numbers))
            errors.append(f'[{kind} N]: {kind} numbers must be consecutive from 0; missing '
                          + ', '.join(f'[{kind} {n}]' for n in missing))
    planes = {}
    for d in devices:
        if d.get('plane') is None:
            continue
        planes.setdefault(d['plane'], []).append(f'[{d["kind"]} {d["number"]}]')
    for plane, owners in sorted(planes.items()):
        if len(owners) > 1:
            errors.append(f'{" and ".join(owners)} plane = {plane}: plane numbers must be unique')
    positions = {}
    for d in devices:
        if isinstance(d.get('position_mm'), list):
            positions.setdefault(d['position_mm'][2], []).append(f'[{d["kind"]} {d["number"]}]')
    for z, owners in sorted(positions.items()):
        if len(owners) > 1:
            errors.append(f'{" and ".join(owners)} position_mm: devices overlap at z = {_text(z)} mm')
    for d in devices:
        if d['kind'] == 'alpide' and d.get('pixels') != [1024, 512]:
            errors.append(f'[alpide {d["number"]}] pixels = {_text(d.get("pixels"))}: the public ALPIDE producer '
                          'requires 1024, 512')
        if d['kind'] != 'msensor':
            continue
        section = f'[msensor {d["number"]}]'
        if d['role'] == 'reference' and d['readout'] != 'triggered':
            errors.append(f'{section} readout = {d["readout"]}: not accepted; a reference plane needs triggered')
        given = d.get('given', [])
        if d.get('pixels') is not None and 'tiles' not in given and 'tile_pixels' not in given:
            # A plain pixel matrix is one tile.
            d['tiles'], d['tile_pixels'] = [1, 1], list(d['pixels'])
        total = [d['tiles'][0] * d['tile_pixels'][0], d['tiles'][1] * d['tile_pixels'][1]]
        if d.get('pixels') is not None and d['pixels'] != total:
            errors.append(f'{section} pixels = {_text(d["pixels"])}: must equal tiles x tile_pixels = {_text(total)}')
        elif max(total) > 4096:
            errors.append(f'{section} tiles, tile_pixels: the matrix {_text(total)} exceeds 4096 pixels per side')
        d['pixels'] = total
        count = d['tiles'][0] * d['tiles'][1]
        wrong = [t for t in d['tiles_off'] if t >= count]
        if wrong:
            errors.append(f'{section} tiles_off = {_text(d["tiles_off"])}: tile {_text(wrong)} does not exist; '
                          f'tiles are numbered 0 to {count - 1}')
        if len(set(d['tiles_off'])) != len(d['tiles_off']):
            errors.append(f'{section} tiles_off: a tile number is repeated')
        if isinstance(d['threshold_dac'], list) and len(d['threshold_dac']) != count:
            errors.append(f'{section} threshold_dac: {len(d["threshold_dac"])} values given; give one value or '
                          f'{count}, one per tile')
        unused = TRIGGERED_ONLY if d['readout'] == 'continuous' else CONTINUOUS_ONLY
        for key in unused:
            if key in d.get('given', []):
                errors.append(f'{section} {key}: applies only to '
                              + ('triggered' if d['readout'] == 'continuous' else 'continuous')
                              + f' readout, but readout = {d["readout"]}')
    if setup['run'].get('daq', 'eudaq') != 'nestdaq':
        for d in devices:
            if d['kind'] == 'msensor':
                for key in ('clock_offset_ns', 'clock_drift_ppm', 'missed_triggers', 'noise'):
                    if key in d.get('given', []):
                        errors.append(f'[msensor {d["number"]}] {key}: only for daq = nestdaq')
    for d in devices:
        if d['kind'] == 'msensor':
            missed = d.get('missed_triggers', [])
            if any(a >= b for a, b in zip(missed, missed[1:])):
                errors.append(f'[msensor {d["number"]}] missed_triggers: numbers must increase')
            if len(missed) > 64:
                errors.append(f'[msensor {d["number"]}] missed_triggers: at most 64 numbers')
    zs = [d['position_mm'][2] for d in devices if isinstance(d.get('position_mm'), list)]
    if len(zs) == len(devices) and zs and setup['beam']['position_mm'][2] < min(zs):
        total = material_budget(devices, setup['beam']['position_mm'][2])
        if total > BUDGET_LIMIT:
            errors.append(f'[devices]: the planes and the air add up to {total:.5f} X0, above the simulation limit {BUDGET_LIMIT:.5f} X0; use fewer planes or a shorter distance from the beam source')
    if zs and setup['beam']['position_mm'][2] >= min(zs):
        errors.append(f'[beam] position_mm: the beam source z = {_text(setup["beam"]["position_mm"][2])} mm must '
                      f'be upstream of every device (below z = {_text(min(zs))} mm)')


def qa_notes(setup):
    """Say before starting whether the analysis can reconstruct tracks and efficiencies."""
    references = sum(d['kind'] == 'alpide' or d.get('role') == 'reference' for d in setup['devices'])
    duts = sum(d['kind'] == 'msensor' and d.get('role', 'dut') == 'dut' for d in setup['devices'])
    if references < QA_MIN_REFERENCE_PLANES:
        return False, [f'QA not possible: {references} reference plane(s); tracks need at least '
                       f'{QA_MIN_REFERENCE_PLANES}. Acquisition works, but scripts/analyze_session.sh will stop '
                       'with an error.']
    notes = [f'QA: tracks need hits on {qa_min_hits(references)} of {references} reference planes.']
    notes.append('QA reports tracks only; there is no msensor DUT for an efficiency.' if not duts else
                 f'QA reports tracks and the efficiency of {duts} msensor DUT(s).')
    if not any(d['kind'] == 'msensor' for d in setup['devices']):
        notes.append(EXAMPLE_NEEDS_MSENSOR)
    return True, notes


def actual_offsets(setup):
    """Hidden x, y offsets in mm of each device (label -> [dx, dy]); uniform in [-max, max].

    They depend only on offset_draw and the device's own mounting (kind, number, plane, nominal
    position and matrix), so run length, thresholds, beam or switched-off tiles do not move them.
    """
    maximum = setup['positions']['offset_max_mm']
    result = {}
    for d in setup['devices']:
        key = json.dumps([setup['positions']['offset_draw'], d['kind'], d['number'], d['plane'], d['position_mm'],
                          d['pixels'], d['pitch_um'], d.get('tiles')], separators=(',', ':'))
        digest = hashlib.sha256(key.encode()).digest()
        u = [int.from_bytes(digest[i:i + 8], 'big') / 2 ** 64 for i in (0, 8)]
        result[label(d)] = [(2 * v - 1) * maximum for v in u]
    return result


def geometry_fingerprint(text):
    """Device composition of a nominal analysis geometry: names, kinds, matrices, positions and roles."""
    keep = ('type', 'number_of_pixels', 'pixel_pitch', 'position', 'role')
    sections, name = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1]
            sections[name] = {}
        elif name and '=' in line and not line.startswith('#'):
            key, value = (x.strip() for x in line.split('=', 1))
            if key in keep:
                sections[name][key] = value.replace(' ', '')
    return hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest()


def label(d):
    return ('ALPIDE_plane_' if d['kind'] == 'alpide' else 'MSENSOR_') + str(d['number'])


def layout_and_options(setup):
    layout = {'devices': [{'kind': d['kind'], 'number': d['number'], 'plane': d['plane'],
                           'position_mm': d['position_mm'], 'pixels': d['pixels'], 'pitch_um': d['pitch_um']}
                          for d in setup['devices']]}
    options = {'events': setup['run']['events'],
               'beam': {'particle': setup['beam']['particle'], 'energy_gev': setup['beam']['energy_gev']},
               'devices': {label(d): {k: d[k] for k in SETTING_NAMES[d['kind']]} for d in setup['devices']},
               'roles': {label(d): d.get('role', 'reference') for d in setup['devices']}}
    if setup.get('scintillators'):
        options['trigger'] = dict(setup['trigger'], scintillators=[{k:p[k] for k in ('channel','position_mm','shape','size_mm','hole_mm')} for p in setup['scintillators']])
    return layout, options


def normalized(setup):
    beam = dict(setup['beam'])
    if beam['profile'] != 'gaussian':
        beam['profile_values'] = profile_values(beam['profile'])
    return {**({'nestdaq': setup['nestdaq']} if setup.get('nestdaq') else {}), 'run': setup['run'], 'beam': beam, 'positions': setup['positions'],
            'devices': [{k: v for k, v in d.items() if k != 'given' and (k != 'noise' or (setup['run'].get('daq', 'eudaq') == 'nestdaq' and v == 'on')) and (k != 'missed_triggers' or (setup['run'].get('daq', 'eudaq') == 'nestdaq' and v)) and (k not in ('clock_offset_ns', 'clock_drift_ppm') or (setup['run'].get('daq', 'eudaq') == 'nestdaq' and (d.get('clock_offset_ns', 0) or d.get('clock_drift_ppm', 0))))} for d in setup['devices']],
            **({'scintillators': setup['scintillators'], 'trigger': setup['trigger']} if setup.get('scintillators') else {})}


def render(setup):
    """Write a setup file that documents every item and its accepted values."""
    lines = ['# Session setup for the test-beam sandbox.',
             '# Edit the values below, then run: scripts/setup_session.sh check FILE',
             '# Lines starting with # are comments. Items left out take the default shown in',
             '# docs/setup-file.md. Device sections are [alpide N] (reference planes) and',
             '# [msensor N] (reference or synthetic DUT); N counts from 0 for each kind without gaps.', '']

    def block(name, items, values, explain):
        lines.append(f'[{name}]')
        for key, item in items.items():
            if not item.render:
                continue
            if explain:
                lines.extend(textwrap.wrap(f'{item.help}; {item.accepted()}', 78, initial_indent='# ',
                                           subsequent_indent='#   '))
            lines.append(f'{key} = {_text(values[key])}'.rstrip())
        lines.append('')
    block('run', RUN, setup['run'], True)
    if setup['run'].get('daq', 'eudaq') == 'nestdaq':
        lines.pop()
        lines.extend(textwrap.wrap(f'{DAQ.help}; {DAQ.accepted()}', 78, initial_indent='# ', subsequent_indent='#   '))
        lines.extend(['daq = nestdaq', ''])
    block('beam', BEAM, setup['beam'], True)
    block('positions', POSITIONS, setup['positions'], True)
    if setup.get('nestdaq'): block('nestdaq', {k: v for k, v in NESTDAQ.items() if k in setup['nestdaq']}, setup['nestdaq'], True)
    if setup.get('nestdaq', {}).get('generator_mb_s', 0) > 0:
        lines.pop()
        item = NESTDAQ['generator_mb_s']
        lines.extend(textwrap.wrap(f'{item.help}; {item.accepted()}', 78, initial_indent='# ', subsequent_indent='#   '))
        lines.append(f"generator_mb_s = {_text(setup['nestdaq']['generator_mb_s'])}")
        lines.append('')
    explained = set()
    for d in sorted(setup['devices'], key=lambda d: (KINDS.index(d['kind']), d['number'])):
        items = dict(DEVICE[d['kind']])
        if d['kind'] == 'msensor':
            for key in (TRIGGERED_ONLY if d['readout'] == 'continuous' else CONTINUOUS_ONLY):
                items.pop(key)
        explain = (d['kind'], d.get('readout')) not in explained
        if not explain:
            lines.append(f'# Same items as the first [{d["kind"]} N] section above.')
        explained.add((d['kind'], d.get('readout')))
        block(f'{d["kind"]} {d["number"]}', items, d, explain)
        if d['kind'] == 'msensor' and setup['run'].get('daq', 'eudaq') == 'nestdaq' and (d.get('clock_offset_ns', 0) or d.get('clock_drift_ppm', 0)):
            lines.pop()
            for key in ('clock_offset_ns', 'clock_drift_ppm'):
                item = items[key]
                lines.extend(textwrap.wrap(f'{item.help}; {item.accepted()}', 78, initial_indent='# ', subsequent_indent='#   '))
                lines.append(f'{key} = {_text(d[key])}')
            lines.append('')
        if d['kind'] == 'msensor' and setup['run'].get('daq', 'eudaq') == 'nestdaq' and d.get('missed_triggers'):
            lines.pop()
            item = items['missed_triggers']
            lines.extend(textwrap.wrap(f'{item.help}; {item.accepted()}', 78, initial_indent='# ', subsequent_indent='#   '))
            lines.append(f"missed_triggers = {_text(d['missed_triggers'])}")
            lines.append('')
        if d['kind'] == 'msensor' and setup['run'].get('daq', 'eudaq') == 'nestdaq' and d.get('noise') == 'on':
            lines.pop()
            item = items['noise']
            lines.extend(textwrap.wrap(f'{item.help}; {item.accepted()}', 78, initial_indent='# ', subsequent_indent='#   '))
            lines.append(f"noise = {_text(d['noise'])}")
            lines.append('')
    for p in sorted(setup.get('scintillators', []), key=lambda p:p['number']):
        block(f'scintillator {p["number"]}', SCINTILLATOR, p, True)
    if setup.get('scintillators'):
        block('trigger', TRIGGER, setup['trigger'], True)
    return '\n'.join(lines)


def build(references=6, spacing=20, dut='msensor', dut_z=None, events=OPERATOR_EVENTS, energy=None, rate=None,
          ithr=None, threshold=None, readout=None, tiles_off=None, offset_max=None, scintillators=2):
    """Place reference planes along z with one gap for the DUT, as in the default setup."""
    upstream = references // 2
    zs = [i * spacing for i in range(references)]
    if dut == 'msensor':
        zs = [i * spacing for i in range(upstream)] + [(i + 1) * spacing for i in range(upstream, references)]
        if dut_z is None:
            dut_z = upstream * spacing
    setup = {'run': {'events': events}, 'beam': _defaults(BEAM), 'positions': _defaults(POSITIONS), 'devices': [], 'scintillators': [], 'trigger': None}
    if offset_max is not None:
        setup['positions']['offset_max_mm'] = offset_max
    if energy is not None:
        setup['beam']['energy_gev'] = energy
    if rate is not None:
        setup['beam']['rate_hz'] = rate
    for i, z in enumerate(zs):
        d = _defaults(DEVICE['alpide'])
        d.update(kind='alpide', number=i, plane=i, position_mm=[1, -2, z], given=[])
        if ithr is not None:
            d['ithr'] = ithr
        setup['devices'].append(d)
    if dut == 'msensor':
        d = _defaults(DEVICE['msensor'])
        d.update(kind='msensor', number=0, plane=references, position_mm=[1, -2, dut_z], given=[])
        if threshold is not None:
            d['threshold_dac'] = threshold
        if readout is not None:
            d['readout'] = readout
        if tiles_off:
            d['tiles_off'] = sorted(tiles_off)
        setup['devices'].append(d)
    if scintillators:
        setup['scintillators'] = [dict(_defaults(SCINTILLATOR), number=i, channel=c, position_mm=[1.1, -2.1, z]) for i,(c,z) in enumerate(((0,-10),(3,140)))]
        setup['trigger'] = _defaults(TRIGGER)
    return setup


def _ranges(numbers):
    """Write tile numbers compactly, such as 0-2, 5."""
    parts, numbers = [], sorted(numbers)
    for n in numbers:
        if parts and n == parts[-1][1] + 1:
            parts[-1][1] = n
        else:
            parts.append([n, n])
    return ', '.join(str(a) if a == b else f'{a}-{b}' for a, b in parts)


def side_view(setup):
    """Draw the devices along the beam axis within the terminal width."""
    devices = sorted(setup['devices'] + [dict(p, kind='scintillator') for p in setup.get('scintillators', [])], key=lambda d: (d['position_mm'][2], d['kind'], d['number']))
    beam_z = setup['beam']['position_mm'][2]
    lo, hi = beam_z, max(d['position_mm'][2] for d in devices)
    span = (hi - lo) or 1
    left, usable = 6, WIDTH - 12
    columns = []
    for d in devices:
        column = left + round((d['position_mm'][2] - lo) / span * usable)
        if columns and column <= columns[-1]:
            column = columns[-1] + 1
        columns.append(column)
    marks = [' '] * (max(columns + [left]) + 4)
    marks[left] = '>'
    for d, column in zip(devices, columns):
        marks[column] = '|' if d['kind'] == 'alpide' else '#' if d['kind'] == 'msensor' else 'S'
    rows = []
    for d, column in zip(devices, columns):
        name = ('A' if d['kind'] == 'alpide' else 'M' if d['kind'] == 'msensor' else 'S') + str(d['number'])
        start = max(0, column - len(name) // 2)
        for row in rows:
            if all(c == ' ' for c in row[max(0, start - 1):start + len(name) + 1]):
                break
        else:
            row = [' '] * WIDTH
            rows.append(row)
        row[start:start + len(name)] = list(name)
    legend = ('Side view (> beam, | ALPIDE, # msensor, S scintillator; z right):' if setup.get('scintillators')
              else 'Side view along the beam (> beam source, | ALPIDE, # msensor; z to the right):')
    out = [legend,
           ' beam ' + ''.join(marks[left:]).rstrip()]
    out += [''.join(r).rstrip() for r in rows]
    scale = f'z from {_text(lo)} mm (beam source) to {_text(hi)} mm'
    out.append(' ' * left + scale)
    return out


def show(setup):
    devices = sorted(setup['devices'], key=lambda d: (d['position_mm'][2], KINDS.index(d['kind']), d['number']))
    out = [f'Devices: {len(devices)} ({sum(d["kind"] == "alpide" for d in devices)} ALPIDE, '
           f'{sum(d["kind"] == "msensor" for d in devices)} msensor), in beam order:']
    rows = [('#', 'Device', 'Role', 'Plane', 'x, y, z mm', 'Pixels', 'Pitch um', 'Settings')]
    for index, d in enumerate(devices, 1):
        if d['kind'] == 'alpide':
            settings = f'ITHR {d["ithr"]}, strobe {d["strobe_length"]}'
        else:
            t = d['threshold_dac']
            t = t if not isinstance(t, list) else (t[0] if min(t) == max(t) else f'{min(t)}-{max(t)}')
            settings = f'thr {t}, {d["readout"]}'
        rows.append((str(index), f'{d["kind"]} {d["number"]}', d.get('role', 'reference').upper() if d.get('role') == 'dut' else 'reference', str(d['plane']), _text(d['position_mm']),
                     'x'.join(map(str, d['pixels'])), 'x'.join(_text(x) for x in d['pitch_um']), settings))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    plates = iter(sorted(setup.get('scintillators', []), key=lambda p: p['position_mm'][2]))
    plate = next(plates, None)
    def plate_line(p):
        return f'scintillator {p["number"]} (channel {p["channel"]}): {p["shape"]} ' + ' x '.join(map(_text, p['size_mm'])) + f' mm, z {_text(p["position_mm"][2])}'
    table_lines = set()
    for r, d in zip(rows, [None] + devices):
        while d is not None and plate is not None and plate['position_mm'][2] < d['position_mm'][2]:
            out.append(plate_line(plate))
            plate = next(plates, None)
        line = '  '.join(r[i].rjust(widths[i]) if i in (0, 3) else r[i].ljust(widths[i])
                         for i in range(len(r))).rstrip()
        table_lines.add(len(out))
        out.append(line)
        if d is not None and d['kind'] == 'msensor':
            # Tile grid and switched-off tiles of this msensor, inside the table.
            text = (f'tiles {d["tiles"][0]}x{d["tiles"][1]} of {d["tile_pixels"][0]}x{d["tile_pixels"][1]} pixels, '
                    f'off: {_ranges(d["tiles_off"]) or "none"}')
            out.extend(textwrap.wrap(text, WIDTH, initial_indent=' ' * (widths[0] + 2),
                                     subsequent_indent=' ' * (widths[0] + 4)))
    while plate is not None:
        out.append(plate_line(plate))
        plate = next(plates, None)
    if setup.get('trigger'):
        for key in ('trg','veto','count'):
            out.extend(textwrap.wrap(f'Trigger {key}: {setup["trigger"][key]}', WIDTH, subsequent_indent='  ', break_long_words=True, break_on_hyphens=False))
    b = setup['beam']
    if setup.get('nestdaq'):
        out.append(f'Run: nestDAQ mode, input {setup["nestdaq"]["input"]}, frame {setup["nestdaq"]["frame_ms"]} ms; the operator starts and stops runs')
        if setup['nestdaq'].get('trigger_board') == 'on':
            out.append(f'Trigger board: periodic trigger every {setup["nestdaq"]["periodic_clk"]} cycles (100.5 MHz)')
    else:
        out.append(f'Run: {setup["run"]["events"]} events per run')
    m = setup['positions']['offset_max_mm']
    out.append(f'Positions: actual x, y differ by up to {_text(m)} mm (hidden; align before QA)' if m
               else 'Positions: actual positions equal the setup (no offsets)')
    out.append(f'Beam: {b["particle"]} {_text(b["energy_gev"])} GeV, {_text(b["rate_hz"])} Hz, '
               f'centre {_text(b["position_mm"])} mm, variance {_text(b["spread_mm2"])} mm2')
    out.append('Track resolution at each DUT (estimate; see the setup file reference):')
    duts = [d for d in devices if d['kind'] == 'msensor' and d.get('role', 'dut') == 'dut']
    refs = [d for d in devices if d['kind'] == 'alpide' or d.get('role') == 'reference']
    if not duts:
        out.append('  no DUT in this setup')
    elif len(refs) < 2:
        out.append('  needs at least 2 reference planes')
    else:
        for r in estimate(devices, b['position_mm'][2], b['energy_gev']):
            x, y = r['track_um']; rx, ry = r['residual_um']
            out.extend(textwrap.wrap(f'  {r["label"]}, z {_text(r["z_mm"])} mm: track {x:.2f} x {y:.2f} um, residual {rx:.2f} x {ry:.2f} um', WIDTH, subsequent_indent='    '))
    out.append('')
    out += side_view(setup)
    long = [line for index, line in enumerate(out) if index not in table_lines and len(line) > WIDTH and not (setup.get('nestdaq') and line.startswith('Run: nestDAQ mode,'))]
    if long:
        raise RuntimeError('display line exceeds 80 columns: ' + long[0])
    return out


def ask(question, default, convert):
    while True:
        answer = input(f'{question} [{default}]: ').strip()
        if not answer:
            return default
        try:
            return convert(answer)
        except ValueError:
            print('  not accepted; try again')


def dut_kind(text):
    if text not in ('msensor', 'none'):
        raise ValueError(text)
    return text


def command_new(args):
    target = Path(args.file)
    if target.exists() and not args.force:
        print(f'{target} already exists; choose another name or add --force to replace it.', file=sys.stderr)
        return 2
    values = dict(references=args.reference_planes, spacing=args.spacing_mm, dut=args.dut, dut_z=args.dut_z,
                  events=args.events, energy=args.energy_gev, rate=args.rate_hz, ithr=args.alpide_ithr,
                  threshold=args.msensor_threshold, readout=args.msensor_readout,
                  tiles_off=[int(x) for x in args.msensor_tiles_off.split(',') if x.strip()] if args.msensor_tiles_off else None,
                  offset_max=args.offset_max_mm, scintillators=args.scintillators)
    if sys.stdin.isatty() and not args.no_input:
        number = lambda text: int(text) if re.fullmatch(r'[+-]?\d+', text) else float(text)
        values['references'] = ask('Number of ALPIDE reference planes', values['references'], int)
        values['spacing'] = ask('Spacing between reference planes in mm', values['spacing'], number)
        values['dut'] = ask('DUT kind (msensor or none)', values['dut'], dut_kind)
        if values['dut'] == 'msensor':
            default_z = values['dut_z'] if values['dut_z'] is not None else (values['references'] // 2) * values['spacing']
            values['dut_z'] = ask('DUT position along the beam (z) in mm', default_z, number)
        values['events'] = ask('Events per run', values['events'], int)
        values['energy'] = ask('Beam energy in GeV', values['energy'] if values['energy'] is not None
                               else BEAM_DEFAULTS['energy_gev'], number)
    setup = build(**values)
    text = render(setup)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.new')
    temporary.write_text(text)
    try:
        checked = load(temporary)
    except SetupError as exc:
        temporary.unlink()
        print('The answers give an invalid setup:', file=sys.stderr)
        for line in exc.errors:
            print('  ' + line.replace(str(temporary), str(target)), file=sys.stderr)
        return 2
    temporary.replace(target)
    print(f'Wrote {target}. Show it with: scripts/setup_session.sh show {target}')
    for line in qa_notes(checked)[1]:
        print(line)
    return 0


def report_errors(path, exc):
    print(f'{path}: {len(exc.errors)} problem(s) found:', file=sys.stderr)
    for line in exc.errors:
        print('  ' + line, file=sys.stderr)


def check_lines(name, setup):
    if setup.get('nestdaq'):
        return [f'{name}: valid; {len(setup["devices"])} device(s), nestDAQ mode (input {setup["nestdaq"]["input"]}, frame {setup["nestdaq"]["frame_ms"]} ms).', NESTDAQ_NOTE]
    return [f'{name}: valid; {len(setup["devices"])} device(s), {setup["run"]["events"]} events per run.'] + qa_notes(setup)[1]


def error_lines(name, exc):
    return [f'{name}: {len(exc.errors)} problem(s) found:'] + ['  ' + line for line in exc.errors]


def command_check(args):
    try:
        setup = load(args.file)
    except SetupError as exc:
        report_errors(args.file, exc)
        return 2
    print('\n'.join(check_lines(args.file, setup)))
    return 0


def command_show(args):
    try:
        setup = load(args.file)
    except SetupError as exc:
        report_errors(args.file, exc)
        return 2
    print('\n'.join(show(setup)))
    return 0


def report(name, text):
    """What `check` and `show` print for a file named name holding text: (exit code, check lines, show lines)."""
    try:
        setup = load_text(text, name)
    except SetupError as exc:
        return 2, error_lines(name, exc), error_lines(name, exc), None
    return 0, check_lines(name, setup), show(setup), setup


# Characters that would change the structure of a setup file if written into a value.
STRUCTURE = re.compile(r'[\r\n#;\[\]=]')


def raw_sections(text):
    """The sections of a setup file as written, [(section, {item: text})], without checking the values."""
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=('#', ';'),
                                       inline_comment_prefixes=('#', ';'), default_section='\0')
    parser.read_string(text)
    return [(section, dict(parser.items(section))) for section in parser.sections()]


def sections_text(sections):
    """A setup file from sections as written; a value with a structure character raises ValueError."""
    lines = []
    for section, values in sections:
        if STRUCTURE.search(section) or not section.strip():
            raise ValueError(section)
        lines.append(f'[{section}]')
        for key, value in values.items():
            if STRUCTURE.search(key) or re.search(r'[\r\n#;\[\]]', value) or not key.strip():
                raise ValueError(key)
            lines.append(f'{key} = {value}'.rstrip())
        lines.append('')
    return '\n'.join(lines)


def item_table():
    """Every item a setup file has, per section kind, with its help, accepted values and default as written."""
    def rows(items):
        return [{'item': k, 'help': v.help, 'accepted': v.accepted(), 'default': '' if v.default is None else _text(v.default),
                 'choices': list(v.choices or []), 'required': v.required} for k, v in items.items() if v.render]
    return {'run': rows(RUN), 'beam': rows(BEAM), 'positions': rows(POSITIONS),
            'alpide': rows(DEVICE['alpide']), 'msensor': rows(DEVICE['msensor']), 'scintillator': rows(SCINTILLATOR), 'trigger': rows(TRIGGER),
            'continuous_only': list(CONTINUOUS_ONLY), 'triggered_only': list(TRIGGERED_ONLY)}


def written_sections(setup):
    """The sections render() writes for a checked setup, as written values."""
    out = []
    for name, items, values in (('run', RUN, setup['run']), ('beam', BEAM, setup['beam']),
                                ('positions', POSITIONS, setup['positions'])):
        out.append((name, {k: _text(values[k]) for k, item in items.items() if item.render}))
    for d in sorted(setup['devices'], key=lambda d: (KINDS.index(d['kind']), d['number'])):
        items = dict(DEVICE[d['kind']])
        if d['kind'] == 'msensor':
            for key in (TRIGGERED_ONLY if d['readout'] == 'continuous' else CONTINUOUS_ONLY):
                items.pop(key)
        out.append((f'{d["kind"]} {d["number"]}', {k: _text(d[k]) for k, item in items.items() if item.render}))
    for p in sorted(setup.get('scintillators', []), key=lambda p:p['number']):
        out.append((f'scintillator {p["number"]}', {k:_text(p[k]) for k in SCINTILLATOR}))
    if setup.get('scintillators'):
        out.append(('trigger', {k:_text(setup['trigger'][k]) for k in TRIGGER}))
    return out


def command_prepare(args):
    """Check the setup and generate a session directory from it, keeping a copy of the setup."""
    try:
        setup = load(args.file)
    except SetupError as exc:
        report_errors(args.file, exc)
        return 2
    if args.example and setup.get('nestdaq'):
        print(f'{args.file}: the finite example (--example) is not available with daq = nestdaq', file=sys.stderr)
        return 2
    for line in ([NESTDAQ_NOTE] if setup.get('nestdaq') else qa_notes(setup)[1]):
        print(line)
    if args.example and not any(d['kind'] == 'msensor' for d in setup['devices']):
        print(f'{args.file}: cannot start the finite example. {EXAMPLE_NEEDS_MSENSOR}', file=sys.stderr)
        return 2
    layout, options = layout_and_options(setup)
    try:
        generate(layout, args.directory, analysis=True, mode='single' if args.example else 'operator',
                 options=options)
    except EditedFilesError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # The session keeps the setup as given and in normalized form for QA and later tools.
    directory = Path(args.directory)
    shutil.copyfile(args.file, directory / ('setup-source' + ('.json' if Path(args.file).suffix == '.json' else '.ini')))
    (directory / 'setup.json').write_text(json.dumps(normalized(setup), indent=2) + '\n')
    if setup.get('nestdaq'): (directory / 'daq-mode').write_text('nestdaq\n')
    if args.alignment:
        # A saved alignment is used only for the same devices; otherwise say why and start without it.
        from session_setup import aligned
        try:
            saved = json.loads(Path(args.alignment).read_text())
        except (OSError, ValueError) as exc:
            saved, reason = None, f'it cannot be read ({exc})'
        else:
            reason = aligned.matches(saved, (directory / 'analysis-geometry.conf').read_text())
        if reason:
            print(f'The saved alignment {args.alignment} is not used: {reason}.')
        else:
            (directory / 'alignment').mkdir(exist_ok=True)
            (directory / 'alignment' / 'alignment.json').write_text(json.dumps(saved, indent=2) + '\n')
            (directory / 'alignment' / 'aligned-geometry.conf').write_text(
                aligned.compose((directory / 'analysis-geometry.conf').read_text(), saved['positions']))
            print(f'Using the saved alignment {saved["id"]} (made from {saved["run"]}); QA needs no new alignment.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scripts/setup_session.sh', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new', help='create a setup file from questions or options')
    new.add_argument('file')
    new.add_argument('--reference-planes', type=int, default=6, help='number of ALPIDE planes (default 6)')
    new.add_argument('--spacing-mm', type=float, default=20, help='distance between reference planes (default 20)')
    new.add_argument('--dut', choices=('msensor', 'none'), default='msensor', help='DUT kind (default msensor)')
    new.add_argument('--dut-z', type=float, help='DUT z in mm (default: the gap in the middle)')
    new.add_argument('--events', type=int, default=OPERATOR_EVENTS, help='events per run (default 60000)')
    new.add_argument('--energy-gev', type=float, help='beam energy, 1 to 6 GeV (default 4)')
    new.add_argument('--rate-hz', type=float, help='nominal beam rate, 1 to 1000 Hz (default 220)')
    new.add_argument('--alpide-ithr', type=int, help='ITHR of every ALPIDE plane (default 0)')
    new.add_argument('--msensor-threshold', type=int, help='msensor threshold_dac (default 100)')
    new.add_argument('--msensor-readout', choices=('continuous', 'triggered'), help='msensor readout mode')
    new.add_argument('--msensor-tiles-off', help='comma-separated msensor tile numbers to switch off')
    new.add_argument('--offset-max-mm', type=float, help='largest hidden x, y offset of each device, 0 to 10 mm '
                     '(default 5; 0 switches the offsets off)')
    new.add_argument('--scintillators', type=int, choices=(0, 2), default=2)
    new.add_argument('--no-input', action='store_true', help='do not ask questions even on a terminal')
    new.add_argument('--force', action='store_true', help='replace an existing file')
    for name, text in (('show', 'show the devices, run length and beam of a setup'),
                       ('check', 'check a setup before starting and say whether QA is possible')):
        sub.add_parser(name, help=text).add_argument('file')
    prepare = sub.add_parser('prepare', help='used by scripts/start_session.sh --setup')
    prepare.add_argument('file')
    prepare.add_argument('directory')
    prepare.add_argument('--example', action='store_true')
    prepare.add_argument('--alignment', help='a saved alignment (scripts/align_session.sh --save) to reuse')
    args = parser.parse_args(argv)
    for name in ('spacing_mm', 'dut_z', 'energy_gev', 'rate_hz', 'offset_max_mm'):
        value = getattr(args, name, None)
        if isinstance(value, float) and value.is_integer():
            setattr(args, name, int(value))
    return {'new': command_new, 'show': command_show, 'check': command_check,
            'prepare': command_prepare}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
