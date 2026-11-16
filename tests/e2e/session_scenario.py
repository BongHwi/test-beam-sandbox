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

case = os.environ.get('SESSION_CASE', 'default')
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
(root/'init.ini').write_text(f'''[RunControl]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\nmoreproducers =\nloggers = log\ncollectors = dc\nconfigs = {configs}\n[LogCollector.log]\nFILE_PATTERN = EUDAQLog$12D.log\nLOG_LEVEL_WRITE = 0\nLOG_LEVEL_PRINT = 1\n[DataCollector.dc]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\n[Producer.ALPIDE_plane_0]\nserial = V0\nplane = 0\ntriggermode = primary\n[Producer.ALPIDE_plane_1]\nserial = V1\nplane = 1\ntriggermode = replica\n[Producer.MSENSOR_0]\nplane = 2\ncolumns = 128\nrows = 128\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsource = session\n''')
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
if case == 'guard':
    import copy
    from tracksim.specs import normalize_config
    from tracksim.stream import TriggerScheduler
    candidate=copy.deepcopy(settings)
    candidate['seed']=private['seed'];candidate['device_seed']=private['device_seed']
    candidate['beam'].update(private['beam']);candidate['timeline']['rate_hz']=private['rate_hz']
    scheduler=TriggerScheduler(normalize_config(candidate))
    for i in range(16): last=scheduler.next(i)
    # End 500 ns after a trigger: the 1 us frame gate extends past source end.
    geometry['end_ns']=last.origin_ps//1000 + 500
    (root/'geometry.json').write_text(json.dumps(geometry))
socket=str(root/'session.sock')
env=dict(os.environ, LD_PRELOAD='/tmp/eudaq_timing.so', EUDAQ_TIMING_TRACE=str(root/'eudaq-timing.jsonl'),SESSION_SOURCE_CONFIG_JSON=json.dumps(private), SESSION_SOURCE_SOCKET=socket,
         VIRTUAL_ALPIDE_SOURCE_SOCKET=socket,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
         PYTHONPATH='/tmp/session_observer:'+os.environ['PYTHONPATH'], SESSION_E2E_TRUTH=str(root/'truth'),
         RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
         RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
target_marker=root/'truth/target-trigger-seen'
if case=='early' and os.environ.get('SESSION_EARLY_TARGET_TRIGGER'):
    env['SESSION_E2E_TARGET_MARKER']=str(target_marker)
    subprocess.Popen(['python','/tmp/session_target_source.py','--geometry',str(root/'geometry.json'),
                      '--socket',socket,'--truth-dir',str(root/'truth'),'--beam-on'],env=env,
                     start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    until=time.monotonic()+15
    while not Path(socket).exists():
        if time.monotonic()>until:raise RuntimeError('test source did not start')
        time.sleep(.01)
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),'--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44000',
     '--producer-env','SESSION_SOURCE_SOCKET','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET','--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--producer-env','LD_PRELOAD','--producer-env','EUDAQ_TIMING_TRACE','--no-attach',
     '--session-geometry',str(root/'geometry.json'),'--session-socket',socket,'--session-truth-dir',str(root/'truth'),'--session-beam-on',
     '--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']

# Some standalone and fault fixtures do not install the optional native observer.
# Avoid passing a missing preload library to their subprocesses.
if not Path('/tmp/eudaq_timing.so').is_file():
    for key in ('LD_PRELOAD','EUDAQ_TIMING_TRACE'):
        env.pop(key,None)
        index=cmd.index(key)
        del cmd[index-1:index+1]
try:
    result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('launcher failed')
    deadline=time.monotonic()+90; status={}; stopped=set(); running_since={}; restarted=False
    while time.monotonic()<deadline:
        try:status=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        if status.get('rc_state') in ('TERMINATED','ERROR'):break
        if case == 'restart' and not restarted and (root/'published.jsonl').exists():
            rows = (root/'published.jsonl').read_text().splitlines()
            seen = [json.loads(line) for line in rows if line]
            if any(row['rc_state']=='STOPPED' and row['config']['index']==0 for row in seen):
                if status.get('rc_state') == 'RUNNING' and status['config']['index']==1:
                    raise RuntimeError('restart point missed')
                pid = int((root/'truth/session.pid').read_text())
                os.kill(pid, signal.SIGTERM)
                Path(socket).unlink(missing_ok=True)
                command=['python','-m','session_core.server','--geometry',str(root/'geometry.json'),
                         '--socket',socket,'--truth-dir',str(root/'truth'),'--beam-on']
                subprocess.Popen(command, env=env, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                until=time.monotonic()+10
                while not Path(socket).exists():
                    if time.monotonic()>until: raise RuntimeError('source restart failed')
                    time.sleep(.01)
                restarted=True
        if fixed and status.get('rc_state') == 'RUNNING':
            index = status['config']['index']
            running_since.setdefault(index, time.monotonic())
            connections = status.get('connections', [])
            finished = any(c['name'] == 'MSENSOR_0' and c['message'] == 'source_finished' for c in connections)
            elapsed = time.monotonic() - running_since[index]
            requested_time = float(os.environ.get('SESSION_STOP_SECONDS', '.25'))
            should_stop = ((auto_end and finished) or
                           (case == 'timed' and elapsed >= requested_time) or
                           (case == 'early' and ((index == 0 and elapsed >= float(os.environ.get('SESSION_EARLY_STOP_SECONDS','.25')) and (not os.environ.get('SESSION_EARLY_TARGET_TRIGGER') or target_marker.exists())) or (index == 1 and finished))))
            if should_stop and index not in stopped:
                with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as control:
                    control.connect(str(root/'control.sock'))
                    control.sendall((json.dumps({'id':'finished-'+str(index),'intent':'stop_next'})+'\n').encode())
                    result=json.loads(control.recv(4096))
                if not result.get('accepted'): raise RuntimeError('stop request rejected')
                stopped.add(index)
                with (root/'stop-times.jsonl').open('a') as f: f.write(json.dumps({'config':index,'elapsed_s':elapsed,'requested_s':requested_time})+'\n')
        time.sleep(.01 if case in ('timed','early','restart') else .2)
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
    if case=='restart' and not restarted:raise RuntimeError('source was not restarted')
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
