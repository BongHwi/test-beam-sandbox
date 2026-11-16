# SPDX-License-Identifier: LGPL-3.0-or-later
"""Diagnostic acquisition through the public launcher and all three producers."""
import json
import os
from pathlib import Path
import subprocess
import socket as socket_module
import signal
import time
import traceback
import pyeudaq

case = 'timed'
stop_kind = os.environ.get('ACCEPTED_STOP_KIND','running')
stop_delay = float(os.environ.get('ACCEPTED_STOP_DELAY','0'))
trace_dir = Path('/tmp/session-e2e/accepted-observations');trace_dir.mkdir(parents=True,exist_ok=True)
if stop_kind == 'joining': os.environ['SESSION_E2E_DELAY_JOIN']='V1'
fixed = case in ('fixed', 'threshold', 'timed', 'early', 'restart', 'guard')
auto_end = case in ('fixed', 'threshold', 'restart', 'guard')
root = Path('/tmp/session-e2e')
root.mkdir(exist_ok=True)
os.chdir('/opt/virtual-producer-cwd')


def detector(name, pixels, z):
    return {'id':name, 'pixels':pixels, 'pitch_um':[30,30], 'position_mm':[1,-2,z],
            'orientation_deg':[0,0,0], 'active_um':40, 'passive_um':60,
            'response':{'model':'boundary_cluster_mean','cluster_mean':1,'efficiency':0,
                        'response_delay_ns':0,'jitter_ns':0,'dark_hit_rate_per_pixel_hz':0},
            'readout':{'model':'triggered','gate_ps':1}}

settings = {'schema_version':4,'preset':'generic','backend':'fast_electron','writer':'json',
            'events':5000 if case in ('timed', 'early') else 1000,'seed':0,'device_seed':0,'workers':1,
            'beam':{'particle':'e-','kinetic_energy_gev':4,'position_mm':[0,0,-20],
                    'direction':[0,0,1],'phase_space_covariance':[[0]*5 for _ in range(5)]},
            'transport':{'scattering':False,'air':False,'extra_layers':[]},
            'timeline':{'rate_hz':10,'duration_s':10,'tail_padding_ps':100000000,'clock_hz':10000000},
            'detectors':[detector('P0',[1024,512],0),detector('P1',[1024,512],40),detector('D0',[128,128],80)]}
geometry={'settings':settings,'participants':[{'serial':'V0','detector':'P0'}, {'serial':'V1','detector':'P1'}, {'serial':'MSENSOR_0','detector':'D0'}], 'clock':os.environ.get('SESSION_CLOCK', 'paced'),'end_ns':(10000000000 if case == 'timed' else 4000000000 if case == 'early' else 200000000 if auto_end else 5000000000)}
(root/'geometry.json').write_text(json.dumps(geometry))
configs = f'{root}/a.conf' if case == 'timed' else f"{root}/a.conf, {root}/{'a' if case in ('fixed','early','restart','guard') else 'b'}.conf"
(root/'init.ini').write_text(f'''[RunControl]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\nmoreproducers =\nloggers =\ncollectors = dc\nconfigs = {configs}\n[DataCollector.dc]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\n[Producer.ALPIDE_plane_0]\nserial = V0\nplane = 0\ntriggermode = primary\n[Producer.ALPIDE_plane_1]\nserial = V1\nplane = 1\ntriggermode = replica\n[Producer.MSENSOR_0]\nplane = 2\ncolumns = 128\nrows = 128\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsource = session\n''')
response = json.loads(Path('/opt/public-work/msensor/response_default.json').read_text())
response['r0'] = 0
(root/'noise-zero.json').write_text(json.dumps(response))
for name in ('a','b'):
    ithr = 180 if case == 'threshold' and name == 'b' else 0
    mode = ('readout_mode = triggered\nstrobe_delay_ns = 0\nstrobe_length_ns = 500' if case == 'guard' else 'readout_mode = triggered\nstrobe_delay_ns = 0\nstrobe_length_ns = 2000' if name == 'a' and not fixed else f'readout_mode = continuous\nstream_dir = {root}\nwindow_length_ns = 1000000\nwindow_guard_before = 1\nwindow_guard_after = 1')
    (root/(name+'.conf')).write_text(f'''[RunControl]\nNEVENTS = {999999 if fixed else 5}\n[Producer.ALPIDE_plane_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 0\nITHR = {ithr}\nSTROBE_LENGTH = 40\n[Producer.ALPIDE_plane_1]\nEUDAQ_DC = dc\nEUDAQ_ID = 1\nITHR = 0\nSTROBE_LENGTH = 40\n[Producer.MSENSOR_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 2\n{mode}\nthreshold_dac = 100\nresponse_file = {root}/noise-zero.json\n[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = {root}/run$3R$X\nDISABLE_PRINT = 1\n''')
private={'seed':1857092461,'device_seed':1857092460,'beam':{'position_mm':[1.234567891,-2.345678912,-20], 'direction':[0.00123456789,0,1], 'phase_space_covariance':[[0.00345678912,0,0,0,0]]+[[0]*5 for _ in range(4)]},'rate_hz':237.654321987}
# Test-only beam schedule used to exercise a stop-drain transition.
if os.environ.get('SESSION_E2E_TAIL_BEAM_OFF'):
    private['beam_schedule'] = [{'time_ps': 0, 'on': True},
                                {'time_ps': 1389000000 * 1000, 'on': False},
                                {'time_ps': 1391000000 * 1000, 'on': True}]
socket=str(root/'session.sock')
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private), SESSION_SOURCE_SOCKET=socket,
         VIRTUAL_ALPIDE_SOURCE_SOCKET=socket,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
         SESSION_E2E_TRUTH=str(root/'truth'),
         RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
         RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'), ACCEPTED_TRACE=str(trace_dir),
         PYTHONPATH='/tmp/accepted_observer:'+os.environ['PYTHONPATH'])
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),'--python-exe','/tmp/accepted_python','--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44000',
     '--producer-env','SESSION_SOURCE_SOCKET','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET','--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--no-attach',
     '--session-geometry',str(root/'geometry.json'),'--session-socket',socket,'--session-truth-dir',str(root/'truth'),'--session-beam-on',
     '--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']
try:
    result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('launcher failed')
    deadline=time.monotonic()+45; status={}; stopped=set(); running_since={}; restarted=False
    requested_at=None; anchor_ns=None; cleanup_sent=False
    def rows(path):
        try:return [json.loads(line) for line in path.read_text().splitlines() if line]
        except (FileNotFoundError,ValueError):return []
    def request_stop(anchor, joined, observed_state):
        nonlocal_placeholder = None
        with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as control:
            control.settimeout(3);control.connect(str(root/'control.sock'))
            before=time.monotonic_ns();control.sendall(b'{"id":"accepted-stop","intent":"stop_next"}\n')
            result=json.loads(control.recv(4096));after=time.monotonic_ns()
        info={'kind':stop_kind,'requested_delay_s':stop_delay,'anchor_ns':anchor,
              'send_ns':before,'accepted_ns':after,'joined_at_request':joined,
              'state_at_request':observed_state,'response':result}
        (trace_dir/'request.json').write_text(json.dumps(info)+'\n')
        if not result.get('accepted') and not result.get('reason'):
            raise RuntimeError('rejection without a reason')
        return before

    while time.monotonic()<deadline:
        try:status=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        if status.get('rc_state') in ('TERMINATED','ERROR'):
            if status.get('rc_state') == 'TERMINATED' and requested_at is not None:
                observed=time.monotonic_ns()
                (trace_dir/'terminal.json').write_text(json.dumps({'observed_ns':observed,'status_updated_ns':status.get('updated_monotonic_ns')})+'\n')
                recorded=json.loads((trace_dir/'request.json').read_text())
                if recorded['response'].get('accepted') and observed-requested_at>15_000_000_000:
                    raise RuntimeError('accepted stop terminated after 15 seconds')
            break
        core=rows(trace_dir/'core.jsonl')
        joined=[x['serial'] for x in core if x['event']=='join_done']
        if stop_kind=='joining' and requested_at is None and 0<len(joined)<3:
            start_rows=rows(trace_dir/'runcontrol.jsonl')
            if start_rows:
                anchor_ns=start_rows[0]['monotonic_ns']
                if time.monotonic_ns()-anchor_ns >= int(stop_delay*1e9):
                    requested_at=request_stop(anchor_ns,joined,status.get('rc_state'));stopped.add(0)
        if stop_kind=='running' and requested_at is None and status.get('rc_state')=='RUNNING':
            journal=rows(root/'published.jsonl')
            runs=[x for x in journal if x.get('rc_state')=='RUNNING']
            if runs:
                anchor_ns=runs[0]['updated_monotonic_ns']
                if time.monotonic_ns()-anchor_ns >= int(stop_delay*1e9):
                    requested_at=request_stop(anchor_ns,joined,'RUNNING');stopped.add(0)
        if requested_at is not None:
            recorded=json.loads((trace_dir/'request.json').read_text())
            if recorded['response'].get('accepted') and time.monotonic_ns()-requested_at>15_000_000_000:
                raise RuntimeError('accepted stop did not terminate within 15 seconds')
            if not recorded['response'].get('accepted') and not cleanup_sent and status.get('rc_state')=='RUNNING':
                with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as control:
                    control.settimeout(3); control.connect(str(root/'control.sock'))
                    control.sendall(b'{\"id\":\"cleanup\",\"intent\":\"terminate\"}\n')
                    cleanup_sent=bool(json.loads(control.recv(4096)).get('accepted'))
        time.sleep(.003 if case in ('timed','early','restart') else .2)
    print('state',status.get('rc_state'),'raws',[x.name for x in root.glob('*.raw')],flush=True)
    for path in root.glob('*.raw'):
        reader=pyeudaq.FileReader('native',str(path)); groups=[]
        while True:
            event=reader.GetNextEvent()
            if event is None:break
            sub=event.GetSubEvents()
            if sub:groups.append([(s.GetDescription(),s.GetTriggerN()) for s in sub])
        print(path.name, 'groups', len(groups), 'aligned', all(len(g) == 3 and len({x[1] for x in g}) == 1 for g in groups), flush=True)
    if status.get('rc_state')!='TERMINATED':raise RuntimeError('run did not terminate')
    if requested_at is None:raise RuntimeError('no stop request was issued')
    # Keep the unmodified collector alive while the normal Terminate path
    # disconnects its producers, so disconnect ordering is observable.
    disconnect_deadline=time.monotonic()+8
    while time.monotonic()<disconnect_deadline:
        seen={x.get('producer') for x in rows(trace_dir/'collector.jsonl') if x.get('event')=='DoDisconnect_after'}
        if {'ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0'}<=seen:break
        time.sleep(.02)
    panes=subprocess.run(['tmux','list-panes','-a','-F','#{session_name}:#{window_index}.#{pane_index} #{pane_title}'],capture_output=True,text=True,check=True).stdout
    captures=[]
    for row in panes.splitlines():
        pane=row.split(' ',1)[0]
        screen=subprocess.run(['tmux','capture-pane','-p','-t',pane,'-S','-10000'],capture_output=True,text=True,check=True).stdout
        captures.append(row+'\n'+screen)
    (root/'panes.log').write_text('\n'.join(captures))
except Exception:
    traceback.print_exc()
    print('launcher log', (root/'launcher.log').read_text(errors='replace') if (root/'launcher.log').exists() else '',flush=True)
    for pane in ('rc','rp.0','rp.1','dp','dc'):
        panel=subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+pane,'-S','-500'],capture_output=True,text=True)
        print('PANE',pane,panel.stdout[-6000:],flush=True)
    raise
finally:
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
