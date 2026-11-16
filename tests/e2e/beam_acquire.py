# SPDX-License-Identifier: LGPL-3.0-or-later
"""Acquire real beam scenarios through the public ITS3 launcher and producers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import traceback
import pyeudaq

case=os.environ.get('BEAM_E2E_CASE','off')
root=Path(os.environ.get('BEAM_E2E_ROOT','/tmp/beam-acquire'));root.mkdir(parents=True,exist_ok=True)
sock=os.environ.get('BEAM_E2E_SOCKET',str(root/'source.sock'))
truth=os.environ.get('BEAM_E2E_TRUTH',str(root/'truth'))
os.chdir('/opt/virtual-producer-cwd')

def detector(name,pixels,z):
    return {'id':name,'pixels':pixels,'pitch_um':[30,30],'position_mm':[1,-2,z],
            'orientation_deg':[0,0,0],'active_um':40,'passive_um':60,
            'response':{'model':'boundary_cluster_mean','cluster_mean':1,'efficiency':0,
                        'response_delay_ns':0,'jitter_ns':0,'dark_hit_rate_per_pixel_hz':0},
            'readout':{'model':'triggered','gate_ps':1}}
settings={'schema_version':4,'preset':'generic','backend':'fast_electron','writer':'json',
          'events':5000,'seed':0,'device_seed':0,'workers':1,
          'beam':{'particle':'e-','kinetic_energy_gev':4,'position_mm':[0,0,-20],
                  'direction':[0,0,1],'phase_space_covariance':[[0]*5 for _ in range(5)]},
          'transport':{'scattering':False,'air':False,'extra_layers':[]},
          'timeline':{'rate_hz':10,'duration_s':10,'tail_padding_ps':100000000,'clock_hz':10000000},
          'detectors':[detector('P0',[1024,512],0),detector('P1',[1024,512],40),detector('D0',[128,128],80)]}
geometry={'settings':settings,'participants':[{'serial':'V0','detector':'P0'},{'serial':'V1','detector':'P1'},
                                                {'serial':'MSENSOR_0','detector':'D0'}],
          'clock':'paced','end_ns':5000000000 if case in ('manual','replay') else 700000000}
(root/'geometry.json').write_text(json.dumps(geometry))
(root/'init.ini').write_text(f'''[RunControl]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\nmoreproducers =\nloggers =\ncollectors = dc\nconfigs = {root}/a.conf\n[DataCollector.dc]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\n[Producer.ALPIDE_plane_0]\nserial = V0\nplane = 0\ntriggermode = primary\n[Producer.ALPIDE_plane_1]\nserial = V1\nplane = 1\ntriggermode = replica\n[Producer.MSENSOR_0]\nplane = 2\ncolumns = 128\nrows = 128\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsource = session\n''')
response=json.loads(Path('/opt/public-work/msensor/response_default.json').read_text());response['r0']=0;response['response_delay_ns']=50000000
(root/'noise-zero.json').write_text(json.dumps(response))
(root/'a.conf').write_text(f'''[RunControl]\nNEVENTS = 999999\n[Producer.ALPIDE_plane_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 0\nITHR = 0\nSTROBE_LENGTH = 40\n[Producer.ALPIDE_plane_1]\nEUDAQ_DC = dc\nEUDAQ_ID = 1\nITHR = 0\nSTROBE_LENGTH = 40\n[Producer.MSENSOR_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 2\nreadout_mode = continuous\nstream_dir = {root}\nwindow_length_ns = 10000000\nwindow_guard_before = 1\nwindow_guard_after = 8\nthreshold_dac = 100\nresponse_file = {root}/noise-zero.json\n[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = {root}/run$3R$X\nDISABLE_PRINT = 1\n''')
private={'seed':1857092461,'device_seed':1857092460,
         'beam':{'position_mm':[1.234567891,-2.345678912,-20], 'direction':[0.00123456789,0,1],
                 'phase_space_covariance':[[0.00345678912,0,0,0,0]]+[[0]*5 for _ in range(4)]},
         'rate_hz':237.654321987}
if case=='edge':private['beam_schedule']=[{'time_ps':0,'on':True},{'time_ps':150,'on':False}]
if case=='replay':
    schedule_file=os.environ.get('BEAM_E2E_SCHEDULE_FILE')
    private['beam_schedule']=json.loads(Path(schedule_file).read_text() if schedule_file else os.environ['BEAM_E2E_SCHEDULE'])
env=dict(os.environ, LD_PRELOAD='/tmp/eudaq_timing.so', EUDAQ_TIMING_TRACE=str(root/'eudaq-timing.jsonl'),SESSION_SOURCE_CONFIG_JSON=json.dumps(private),SESSION_SOURCE_SOCKET=sock,
         VIRTUAL_ALPIDE_SOURCE_SOCKET=sock,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
         PYTHONPATH='/tmp/session_observer:'+os.environ['PYTHONPATH'], SESSION_E2E_TRUTH=truth,
         RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
         RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),
     '--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44000',
     '--producer-env','SESSION_SOURCE_SOCKET','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET',
     '--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--producer-env','LD_PRELOAD','--producer-env','EUDAQ_TIMING_TRACE','--no-attach','--session-geometry',str(root/'geometry.json'),
     '--session-socket',sock,'--session-truth-dir',truth,
     '--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']

def beam(action):
    result=subprocess.run(['python','-m','session_core.beamctl','--socket',sock,action],env=env,capture_output=True,text=True,timeout=10)
    if result.returncode:raise RuntimeError('beam command failed')
    value=json.loads(result.stdout)
    with (root/'beam-commands.jsonl').open('a') as stream:stream.write(json.dumps(value)+'\n')
    return value

# Some standalone and fault fixtures do not install the optional native observer.
# Avoid passing a missing preload library to their subprocesses.
if not Path('/tmp/eudaq_timing.so').is_file():
    for key in ('LD_PRELOAD','EUDAQ_TIMING_TRACE'):
        env.pop(key,None)
        index=cmd.index(key)
        del cmd[index-1:index+1]

try:
    launched=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(launched.stdout+launched.stderr)
    if launched.returncode:raise RuntimeError('launcher failed')
    deadline=time.monotonic()+90;status={};running_at=None;sent=[];stopped=False
    while time.monotonic()<deadline:
        try:status=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        if status.get('rc_state') in ('TERMINATED','ERROR'):break
        if status.get('rc_state')=='RUNNING':
            if running_at is None:running_at=time.monotonic()
            elapsed=time.monotonic()-running_at
            if case=='manual':
                for index,(at,action) in enumerate(((.30,'on'),(.65,'off'),(1.00,'on'))):
                    if elapsed>=at and index not in sent:
                        beam(action);sent.append(index)
            finished=any(c['name']=='MSENSOR_0' and c['message']=='source_finished' for c in status.get('connections',[]))
            if finished and not stopped:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as control:
                    control.settimeout(3);control.connect(str(root/'control.sock'))
                    control.sendall(b'{"id":"source-end","intent":"stop_next"}\n')
                    if not json.loads(control.recv(4096))['accepted']:raise RuntimeError('stop rejected')
                stopped=True
        time.sleep(.01)
    if status.get('rc_state')!='TERMINATED':raise RuntimeError('run did not terminate')
    if case=='manual' and len(sent)!=3:raise RuntimeError('not all beam actions were issued')
    raw=list(root.glob('*.raw'));streams=list(root.glob('*.msws'))
    print(json.dumps({'state':status.get('rc_state'),'raw_count':len(raw),'stream_count':len(streams),'actions':len(sent)}))
    panes=subprocess.run(['tmux','list-panes','-a','-F','#{session_name}:#{window_index}.#{pane_index} #{pane_title}'],capture_output=True,text=True,check=True).stdout
    captures=[]
    for row in panes.splitlines():
        pane=row.split(' ',1)[0]
        screen=subprocess.run(['tmux','capture-pane','-p','-t',pane,'-S','-10000'],capture_output=True,text=True,check=True).stdout
        captures.append(row+'\n'+screen)
    (root/'panes.log').write_text('\n'.join(captures))
except Exception:
    traceback.print_exc()
    print('launcher', (root/'launcher.log').read_text(errors='replace') if (root/'launcher.log').exists() else '')
    for pane in ('rc','rp.0','rp.1','dp','dc'):
        panel=subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+pane,'-S','-300'],capture_output=True,text=True)
        print('PANE',pane,panel.stdout[-5000:])
    raise
finally:
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
