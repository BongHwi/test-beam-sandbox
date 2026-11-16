# SPDX-License-Identifier: LGPL-3.0-or-later
"""Build deterministic acquisition files from a public detector layout."""
import argparse
import hashlib
import json
from pathlib import Path

# Generated files and their SHA-256 are recorded here so a later generation
# never silently replaces a file that was edited by hand.
MANIFEST = '.generated-files.json'
ALPIDE_DEFAULTS = {'ithr': 0, 'strobe_length': 40}
MSENSOR_DEFAULTS = {'threshold_dac': 100, 'readout': 'continuous', 'window_length_ns': 1000000,
                    'strobe_delay_ns': 0, 'strobe_length_ns': 2000, 'tiles': [1, 1], 'tiles_off': []}


def tile_ranges(device, tiles):
    """Column and row ranges of each tile, numbered from column 0, row 0 along x first."""
    w, h = device['pixels'][0] // tiles[0], device['pixels'][1] // tiles[1]
    return {i: ((i % tiles[0]) * w, w, (i // tiles[0]) * h, h) for i in range(tiles[0] * tiles[1])}
BEAM_DEFAULTS = {'particle': 'e-', 'energy_gev': 4}
OPERATOR_EVENTS = 60000


class EditedFilesError(RuntimeError):
    """Raised instead of replacing generated files that changed after generation."""


def default_layout():
    return {'devices': [
        *[{'kind': 'alpide', 'number': i, 'plane': i, 'position_mm': [1, -2, z],
           'pixels': [1024, 512], 'pitch_um': [30, 30]} for i, z in enumerate((0, 20, 40, 80, 100, 120))],
        {'kind': 'msensor', 'number': 0, 'plane': 6, 'position_mm': [1, -2, 60],
         'pixels': [128, 128], 'pitch_um': [30, 30]}]}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write_all(root, files):
    """Write generated files unless that would replace a file edited after generation."""
    record = root / MANIFEST
    try:
        previous = json.loads(record.read_text()) if record.is_file() else None
    except ValueError:
        previous = {}
    edited = []
    for name, data in files.items():
        path = root / name
        if not path.is_file():
            continue
        current = _digest(path.read_bytes())
        if previous is None:
            if current != _digest(data):
                edited.append(name)
        elif name in previous and previous[name] != current:
            edited.append(name)
    if edited:
        raise EditedFilesError('generated files in ' + str(root) + ' were changed after generation: '
                               + ', '.join(sorted(edited)) + '. Nothing was overwritten. Generate into a new '
                               'directory, or start this directory with --layout to use the edited files.')
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    merged = dict(previous or {})
    merged.update({name: _digest(data) for name, data in files.items()})
    record.write_text(json.dumps(merged, indent=2, sort_keys=True) + '\n')


def generate(layout, root, *, analysis=False, mode='single', options=None, extra=None):
    """Generate the acquisition files; options carries per-device settings, run length and beam."""
    options = options or {}
    root = Path(root).resolve()
    if set(layout) != {'devices'} or not isinstance(layout['devices'], list) or not layout['devices']:
        raise ValueError('layout must contain only a nonempty devices list')
    if mode not in ('single', 'two', 'off', 'manual', 'replay', 'operator'):
        raise ValueError('invalid run mode')
    devices = layout['devices']
    required = {'kind', 'number', 'plane', 'position_mm', 'pixels', 'pitch_um'}
    names, planes = set(), set()
    for d in devices:
        if not isinstance(d, dict) or set(d) != required or d['kind'] not in ('alpide', 'msensor'):
            raise ValueError('invalid device fields')
        if any(isinstance(d[k], bool) or not isinstance(d[k], int) or d[k] < 0 for k in ('number', 'plane')):
            raise ValueError('invalid number or plane')
        if len(d['position_mm']) != 3 or len(d['pixels']) != 2 or len(d['pitch_um']) != 2:
            raise ValueError('invalid geometry dimensions')
        if any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in d['position_mm']+d['pitch_um']):
            raise ValueError('invalid geometry values')
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 1 for x in d['pixels']) or any(x <= 0 for x in d['pitch_um']):
            raise ValueError('invalid pixel size')
        if d['kind'] == 'alpide' and d['pixels'] != [1024, 512]:
            raise ValueError('public reference producer requires 1024 by 512 pixels')
        name = ('ALPIDE_plane_' if d['kind'] == 'alpide' else 'MSENSOR_') + str(d['number'])
        if name in names or d['plane'] in planes:
            raise ValueError('duplicate producer or plane')
        names.add(name); planes.add(d['plane'])
    for kind in ('alpide', 'msensor'):
        nums = sorted(d['number'] for d in devices if d['kind'] == kind)
        if nums != list(range(len(nums))):
            raise ValueError('launcher requires consecutive numbers starting at zero')
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    device_settings = options.get('devices', {})
    beam_options = dict(BEAM_DEFAULTS, **options.get('beam', {}))
    by_z = sorted(devices, key=lambda d: (d['position_mm'][2], d['kind'], d['number']))
    detector_rows, participants, mapping = [], [], []
    def label(d): return ('ALPIDE_plane_' if d['kind'] == 'alpide' else 'MSENSOR_') + str(d['number'])
    def detid(d): return ('P' if d['kind'] == 'alpide' else 'D') + str(d['number'])
    for d in by_z:
        detector_rows.append({'id': detid(d), 'pixels': d['pixels'], 'pitch_um': d['pitch_um'],
            'position_mm': d['position_mm'], 'orientation_deg': [0,0,0], 'active_um': 40, 'passive_um': 60,
            'response': {'model': 'boundary_cluster_mean', 'cluster_mean': 1, 'efficiency': 0,
                         'response_delay_ns': 0, 'jitter_ns': 0, 'dark_hit_rate_per_pixel_hz': 0},
            'readout': {'model': 'triggered', 'gate_ps': 1}})
    for d in devices:
        serial = 'V'+str(d['number']) if d['kind']=='alpide' else label(d)
        participants.append({'serial': serial, 'detector': detid(d)})
        mapping.append({'producer':label(d), 'serial':serial, 'detector':detid(d), 'plane':d['plane'],
                        'analysis_name':('ALPIDE_' if d['kind']=='alpide' else 'MSENSOR_')+str(d['plane'])})
    settings={'schema_version':4,'preset':'generic','backend':'fast_electron','writer':'json',
        'events':5000,'seed':0,'device_seed':0,'workers':1,
        'beam':{'particle':beam_options['particle'],'kinetic_energy_gev':beam_options['energy_gev'],'position_mm':[0,0,-20],
                'direction':[0,0,1],'phase_space_covariance':[[0]*5 for _ in range(5)]},
        'transport':{'scattering':False,'air':False,'extra_layers':[]},
        'timeline':{'rate_hz':10,'duration_s':10,'tail_padding_ps':100000000,'clock_hz':10000000},
        'detectors':detector_rows}
    geometry={'settings':settings,'participants':participants,'clock':'paced'}
    if options.get('trigger'): geometry['trigger'] = options['trigger']
    if mode=='operator':geometry['continuous']=True
    else:geometry['end_ns']=1500000000 if mode=='single' else 2500000000 if mode in ('two','off') else 5000000000
    files['geometry.json']=(json.dumps(geometry,indent=2)+'\n').encode()
    files['mapping.json']=(json.dumps(mapping,indent=2)+'\n').encode()
    configs=[Path('a.conf'),Path('b.conf')] if mode=='two' else [Path('a.conf')]
    joined=', '.join(label(d) for d in devices)
    trigger_config = options.get('trigger')
    more = ' TRIGGER_0' if trigger_config else ''
    ini=f'[RunControl]\ndataproducers = {joined}\nmoreproducers ={more}\nloggers = log\ncollectors = dc\nconfigs = '+', '.join(map(str,configs))+'\n'
    # The public launcher always starts the log collector named "log"; this
    # section follows the public ITS3 example initialisation file.
    ini+='[LogCollector.log]\nFILE_PATTERN = EUDAQLog$12D.log\nLOG_LEVEL_WRITE = 0\nLOG_LEVEL_PRINT = 1\n'
    ini+=f'[DataCollector.dc]\ndataproducers = {joined}\n'
    init_fragments=[]
    for d in devices:
        if d['kind']=='alpide':
            section=f"[Producer.{label(d)}]\nserial = V{d['number']}\nplane = {d['plane']}\ntriggermode = {'primary' if d['number']==0 else 'replica'}\n"
        else:
            tiles=dict(MSENSOR_DEFAULTS,**device_settings.get(label(d),{}))['tiles']
            grid=f"tiles_x = {tiles[0]}\ntiles_y = {tiles[1]}\n" if tiles!=[1,1] else ''
            section=f"[Producer.{label(d)}]\nplane = {d['plane']}\ncolumns = {d['pixels'][0]}\nrows = {d['pixels'][1]}\nregions = 1\n{grid}deterministic_ids = 1\nclock_mode = virtual\nsource = session\n"
        ini+=section; init_fragments.append((label(d),section))
    if trigger_config:
        ini += '[Producer.TRIGGER_0]\nport = auto\n'
        for channel in sorted(p['channel'] for p in trigger_config['scintillators']):
            ini += f'CH{channel}_GAIN = 0.8\nCH{channel}_THR = 1.0\n'
        ini += 'spill_counter_sampling_period_s = 180\nspill_counter_threshold = 5\n'
    files['init.ini']=ini.encode()
    response_path=Path(__file__).resolve().parents[1]/'msensor/response_default.json'
    if not response_path.exists(): response_path=Path('/opt/public-work/msensor/response_default.json')
    response=json.loads(response_path.read_text()); response['r0']=0
    files['noise-zero.json']=(json.dumps(response,sort_keys=True)+'\n').encode()
    for name,section in init_fragments:files['devices/'+name+'.ini']=section.encode()
    events=options.get('events',OPERATOR_EVENTS) if mode=='operator' else 999999
    for config in configs:
        high=config.name=='b.conf'; text='[RunControl]\nNEVENTS = '+str(events)+'\n'
        for d in devices:
            name=label(d)
            if d['kind']=='alpide':
                chosen=dict(ALPIDE_DEFAULTS,**device_settings.get(name,{}))
                section=f"[Producer.{name}]\nEUDAQ_DC = dc\nEUDAQ_ID = {d['plane']}\nITHR = {64 if high else chosen['ithr']}\nSTROBE_LENGTH = {chosen['strobe_length']}\n"
            else:
                chosen=dict(MSENSOR_DEFAULTS,**device_settings.get(name,{}))
                if mode=='two':chosen.update(readout='triggered',strobe_delay_ns=0,strobe_length_ns=2000)
                mode_text=(f"readout_mode = continuous\nstream_dir = .\nwindow_length_ns = {chosen['window_length_ns']}\nwindow_guard_before = 1\nwindow_guard_after = 1" if chosen['readout']=='continuous' else f"readout_mode = triggered\nstrobe_delay_ns = {chosen['strobe_delay_ns']}\nstrobe_length_ns = {chosen['strobe_length_ns']}")
                threshold=200 if high else chosen['threshold_dac']
                threshold=json.dumps(threshold) if isinstance(threshold,list) else threshold
                off=f"tiles_off = {json.dumps(sorted(chosen['tiles_off']))}\n" if chosen['tiles_off'] else ''
                section=f"[Producer.{name}]\nEUDAQ_DC = dc\nEUDAQ_ID = {d['plane']}\n{mode_text}\nthreshold_dac = {threshold}\n{off}response_file = noise-zero.json\n"
            text+=section; files['devices/'+name+'-'+config.name]=section.encode()
        if trigger_config:
            text += '[Producer.TRIGGER_0]\nEUDAQ_DC = dc\n' + ''.join(f'{key} = {trigger_config[key]}\n' for key in ('trg','veto','count')) + 'read_interval = 2\n'
        text+=f'[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = run$3R$X\nDISABLE_PRINT = 1\n'
        files[str(config)]=text.encode()
    if analysis:
        text=''
        roles=options.get('roles', {})
        reference=next((d for d in devices if d['kind']=='alpide' and d['number']==0), None)
        if reference is None:
            candidates=[d for d in devices if d['kind']=='msensor' and roles.get(label(d),'dut')=='reference']
            reference=min(candidates, key=lambda d:d['position_mm'][2]) if candidates else None
        for d in devices:
            name=('ALPIDE_' if d['kind']=='alpide' else 'MSENSOR_')+str(d['plane'])
            role='role = "reference"' if d is reference else 'role = "dut"' if d['kind']=='msensor' and roles.get(label(d),'dut')=='dut' else ''
            pos=', '.join(str(x)+'mm' for x in d['position_mm'])
            pitch=', '.join(str(x)+'um' for x in d['pitch_um'])
            mask=''
            chosen=dict(MSENSOR_DEFAULTS,**device_settings.get(label(d),{})) if d['kind']=='msensor' else None
            if chosen and chosen['tiles_off']:
                # Switched-off tiles are masked so the efficiency uses only the tiles that are on.
                ranges=tile_ranges(d,chosen['tiles'])
                lines=[f'p {c} {r}' for t in sorted(chosen['tiles_off']) for c in range(ranges[t][0],ranges[t][0]+ranges[t][1])
                       for r in range(ranges[t][2],ranges[t][2]+ranges[t][3])]
                files[name+'-tiles-off.txt']=('\n'.join(lines)+'\n').encode()
                mask=f'# tiles off: {", ".join(map(str,sorted(chosen["tiles_off"])))}\nmask_file = "{name}-tiles-off.txt"\n'
            text+=f'[{name}]\ntype = "{d["kind"]}"\nnumber_of_pixels = {d["pixels"][0]}, {d["pixels"][1]}\npixel_pitch = {pitch}\nposition = {pos}\norientation = 0deg, 0deg, 0deg\norientation_mode = "xyz"\nspatial_resolution = 10um, 10um\nmaterial_budget = 0.001\ntime_resolution = 100ns\n{mask}{role}\n'
        files['analysis-geometry.conf']=text.encode()
    files.update(extra or {})
    _write_all(root, files)
    return root


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--layout', help='layout file (devices only)')
    source.add_argument('--setup', help='setup file (devices, device settings, run length and beam)')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--analysis-geometry', action='store_true')
    parser.add_argument('--example', action='store_true', help='generate the finite one-pass example')
    args=parser.parse_args()
    layout=options=extra=None
    if args.setup:
        from session_setup.setupfile import SetupError, layout_and_options, load, normalized
        try:
            setup=load(args.setup)
        except SetupError as exc:
            raise SystemExit(args.setup+': '+str(len(exc.errors))+' problem(s) found:\n  '+'\n  '.join(exc.errors))
        layout,options=layout_and_options(setup)
        # The setup copy keeps the beam values for a later start with --layout.
        extra={'setup.json':(json.dumps(normalized(setup),indent=2)+'\n').encode(),
               'setup-source'+('.json' if args.setup.endswith('.json') else '.ini'):Path(args.setup).read_bytes()}
    else:
        layout=json.loads(Path(args.layout).read_text())
    try:
        print(generate(layout,args.directory,analysis=args.analysis_geometry,
                       mode='single' if args.example else 'operator',options=options,extra=extra))
    except EditedFilesError as exc:
        raise SystemExit(str(exc))

if __name__=='__main__': main()
