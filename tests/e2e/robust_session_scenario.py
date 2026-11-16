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

robust = os.environ['ROBUST_CASE']
case = 'timed' if robust in ('c7','c10a','c10b','c10resume','c10c','c11','c12','unknown','c15') else 'fixed'
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
if robust=='c8a':configs=f'{root}/a.conf, {root}/b.conf'
if robust=='c8b':configs=f'{root}/a.conf, {root}/b.conf, {root}/a.conf'
(root/'init.ini').write_text(f'''[RunControl]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\nmoreproducers =\nloggers =\ncollectors = dc\nconfigs = {configs}\n[DataCollector.dc]\ndataproducers = ALPIDE_plane_0, ALPIDE_plane_1, MSENSOR_0\n[Producer.ALPIDE_plane_0]\nserial = V0\nplane = 0\ntriggermode = primary\n[Producer.ALPIDE_plane_1]\nserial = V1\nplane = 1\ntriggermode = replica\n[Producer.MSENSOR_0]\nplane = 2\ncolumns = 128\nrows = 128\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsource = session\n''')
response = json.loads(Path('/opt/public-work/msensor/response_default.json').read_text())
response['r0'] = 0
(root/'noise-zero.json').write_text(json.dumps(response))
for name in ('a','b'):
    ithr = 180 if case == 'threshold' and name == 'b' else 0
    strobe = (40000 if robust=='c8a' and name=='b' else 20) if robust in ('c8a','c8b') else 40
    mode = ('readout_mode = triggered\nstrobe_delay_ns = 0\nstrobe_length_ns = 500' if case == 'guard' else 'readout_mode = triggered\nstrobe_delay_ns = 0\nstrobe_length_ns = 2000' if name == 'a' and not fixed else f'readout_mode = continuous\nstream_dir = {root}\nwindow_length_ns = 1000000\nwindow_guard_before = 1\nwindow_guard_after = 1')
    (root/(name+'.conf')).write_text(f'''[RunControl]\nNEVENTS = {999999 if fixed else 5}\n[Producer.ALPIDE_plane_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 0\nITHR = {ithr}\nSTROBE_LENGTH = {strobe}\n[Producer.ALPIDE_plane_1]\nEUDAQ_DC = dc\nEUDAQ_ID = 1\nITHR = 0\nSTROBE_LENGTH = 40\n[Producer.MSENSOR_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 2\n{mode}\nthreshold_dac = 100\nresponse_file = {root}/noise-zero.json\n[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = {root}/run$3R$X\nDISABLE_PRINT = 1\n''')
private={'seed':1857092461,'device_seed':1857092460,'beam':{'position_mm':[1.234567891,-2.345678912,-20], 'direction':[0.00123456789,0,1], 'phase_space_covariance':[[0.00345678912,0,0,0,0]]+[[0]*5 for _ in range(4)]},'rate_hz':237.654321987}
if case=='timed':
    settings['events']=50000
    settings['timeline']['duration_s']=100
    geometry['end_ns']=100_000_000_000
if robust in ('c8a','c8b'):
    settings['events']=15000 if robust=='c8b' else 5000
    geometry['end_ns']=5_000_000_000 if robust=='c8b' else 1_000_000_000
    private['rate_hz']=1000
if robust=='c12':
    settings['detectors'].append(detector('P2',[1024,512],120))
    geometry['participants'].append({'serial':'ABSENT','detector':'P2'})
if robust=='unknown':
    init=root/'init.ini';init.write_text(init.read_text().replace('serial = V1','serial = UNREGISTERED'))
if robust in ('events','intersections','pixels','beam_off'):
    key='events' if robust=='beam_off' else robust
    settings['limits']={key: 40 if key=='events' else 20}
(root/'geometry.json').write_text(json.dumps(geometry))
socket=str(root/'session.sock')
env=dict(os.environ, LD_PRELOAD='/tmp/eudaq_timing.so', EUDAQ_TIMING_TRACE=str(root/'eudaq-timing.jsonl'),SESSION_SOURCE_CONFIG_JSON=json.dumps(private), SESSION_SOURCE_SOCKET=socket,
         VIRTUAL_ALPIDE_SOURCE_SOCKET=socket,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
         PYTHONPATH='/tmp/session_observer:'+os.environ['PYTHONPATH'], SESSION_E2E_TRUTH=str(root/'truth'),
         RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
         RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
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
audit=root/'truth/protocol.jsonl'
if robust=='c7':env['ROBUST_DELAY_JOIN']='V1'
env['ROBUST_PROTOCOL_AUDIT']=str(audit)
source_command=['python','/tmp/robust_source_observer.py','--geometry',str(root/'geometry.json'),'--socket',socket,'--truth-dir',str(root/'truth')]
if robust!='beam_off':source_command.append('--beam-on')
source_process=subprocess.Popen(source_command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
deadline=time.monotonic()+15
while not Path(socket).exists():
    if source_process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('source failed to start')
    time.sleep(.02)

def find_producer():
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:cmdline=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        except OSError:continue
        if 'ALPIDEProducer.py' in cmdline and 'ALPIDE_plane_1' in cmdline:return int(p.name)
    return None

def send_stop(index):
    with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as control:
        control.settimeout(5);control.connect(str(root/'control.sock'))
        control.sendall((json.dumps({'id':'robust-'+str(index),'intent':'stop_next'})+'\n').encode())
        result=json.loads(control.recv(4096))
    if not result.get('accepted'):raise RuntimeError('stop request rejected')
    return time.monotonic_ns()

pid=None;paused_at=None;kill_at=None;stop_at=None;count_at_pause=None;collector_at_kill=None
resumed_at=None;count_at_error=None;error_isolated=False;stop_error=None;stop_attempted=False;disconnect_at=None;loss_counts=None
stalled=False;observation=[];delay_exercised=False;config_changed=False;changed_before_finish=False
try:
    result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('launcher failed')
    if robust=='c7':
        marker=root/'truth/paused-pid';until=time.monotonic()+20
        while not marker.exists() and time.monotonic()<until:time.sleep(.01)
        if not marker.exists():raise RuntimeError('participant was not paused before StartRun')
        pid=int(marker.read_text());paused_at=time.monotonic_ns()
        until=time.monotonic()+8
        while time.monotonic()<until:
            rows=[json.loads(x) for x in audit.read_text().splitlines()] if audit.exists() else []
            joined={x['serial'] for x in rows if x['op']=='join'}
            if 'V1' in joined:raise RuntimeError('producer joined before it could be delayed')
            if len(joined)>=2 and any(x['op']=='next' and x['kind']=='wait' for x in rows):
                delay_exercised=True;break
            time.sleep(.02)
        os.kill(pid,signal.SIGCONT)
        if not delay_exercised:raise RuntimeError('other participants did not reach the barrier')
    deadline=time.monotonic()+90; status={}; stopped=set(); running_since={}; restarted=False
    while time.monotonic()<deadline:
        try:status=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        if stop_at is not None and time.monotonic_ns()-stop_at >= 30_000_000_000:
            break
        if status.get('rc_state') in ('TERMINATED','ERROR'):
            if robust in ('c10a','c10b','c10resume') and (kill_at or resumed_at) and not stop_attempted:
                if robust=='c10resume' and pid is not None:
                    try:kill_at=time.monotonic_ns();os.kill(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                stop_attempted=True
                try:stop_at=send_stop(0)
                except (OSError,RuntimeError) as exc:stop_error=str(exc)
            if robust!='c11' or all(c.get('state')=='ERROR' for c in status.get('connections',[]) if c.get('role')=='producer'):
                break
        if fixed and status.get('rc_state') == 'RUNNING':
            index = status['config']['index']
            running_since.setdefault(index, time.monotonic())
            connections = status.get('connections', [])
            finished = any(c['name'] == 'MSENSOR_0' and c['message'] == 'source_finished' for c in connections)
            elapsed = time.monotonic() - running_since[index]
            requested_time = float(os.environ.get('SESSION_STOP_SECONDS', '.25'))
            should_stop = auto_end and finished
            c={item['name']:item for item in connections}
            if robust=='c8b' and index==0 and not config_changed:
                changed_before_finish=not any((root/'truth').glob('run-*.json'))
                config=root/'a.conf';data=config.read_text();needle='STROBE_LENGTH = 20'
                if needle not in data:raise RuntimeError('initial strobe setting missing')
                config.write_text(data.replace(needle,'STROBE_LENGTH = 40000',1));config_changed=True
            a=c.get('ALPIDE_plane_0',{}); victim=c.get('ALPIDE_plane_1',{}); collector=c.get('dc',{})
            if robust in ('c10a','c10b','c10resume','c10c','c11','c15'):
                should_stop=False
                if kill_at is None and paused_at is None and a.get('data_events',0)>=5:
                    if robust=='c11':
                        os.kill(int((root/'truth/session.pid').read_text()),signal.SIGKILL);kill_at=time.monotonic_ns()
                    else:
                        pid=find_producer()
                        if pid is not None:
                            count_at_pause=a['data_events']
                            if robust=='c10a':
                                collector_at_kill=collector.get('data_events',0);kill_at=time.monotonic_ns();os.kill(pid,signal.SIGKILL)
                            else:os.kill(pid,signal.SIGSTOP);paused_at=time.monotonic_ns()
                eventlog=Path(socket+'.session.log')
                event_text=eventlog.read_text() if eventlog.exists() else ''
                loss_observed=('disconnected' in event_text if robust=='c10a' else 'response_timeout' in event_text)
                if loss_observed and loss_counts is None:
                    loss_counts=(a.get('data_events',0),c.get('MSENSOR_0',{}).get('data_events',0))
                progress_seen=(loss_counts is not None and a.get('data_events',0)>loss_counts[0]+5 and
                               c.get('MSENSOR_0',{}).get('data_events',0)>loss_counts[1]+5)
                if paused_at is not None and robust in ('c10b','c10resume','c10c'):
                    stalled='response_timeout' in event_text
                    if stalled and (progress_seen or robust=='c10c' and a.get('data_events',0)>count_at_pause+5) and kill_at is None:
                        if robust=='c10b':
                            collector_at_kill=collector.get('data_events',0);kill_at=time.monotonic_ns();os.kill(pid,signal.SIGKILL)
                        elif robust=='c10resume':
                            if resumed_at is None:
                                os.kill(pid,signal.SIGCONT);resumed_at=time.monotonic_ns()
                            if victim.get('state')=='ERROR' and count_at_error is None:
                                count_at_error=a.get('data_events',0)
                            if count_at_error is not None and a.get('data_events',0)>count_at_error+5:
                                error_isolated=True;collector_at_kill=collector.get('data_events',0)
                                kill_at=time.monotonic_ns();os.kill(pid,signal.SIGKILL)
                        elif stop_at is None:stop_attempted=True;stop_at=send_stop(index)
                if robust in ('c10a','c10b','c10resume') and kill_at is not None and not victim.get('connected',True):
                    if disconnect_at is None:disconnect_at=time.monotonic()
                    latest=max(a.get('data_events',0),c.get('MSENSOR_0',{}).get('data_events',0))
                    if progress_seen and ((collector.get('data_events',0)>collector_at_kill and collector.get('data_events',0)>=latest) or time.monotonic()-disconnect_at>=5):
                        should_stop=True
                if robust=='c15' and paused_at is not None and stop_at is None:
                    rows=[json.loads(x) for x in audit.read_text().splitlines()] if audit.exists() else []
                    lead=[x for x in rows if x.get('slowest_ps') is not None and x['committed_ps']-x['slowest_ps']>=180_000_000_000 and x['monotonic_ns']>=paused_at]
                    if lead:
                        stop_attempted=True;stop_at=send_stop(index);os.kill(pid,signal.SIGCONT);stopped.add(index)
                if robust=='c10c' and stop_at is not None:
                    observation.append({'time_ns':time.monotonic_ns(),'state':status.get('rc_state'),
                        'producers':{n:{'state':v.get('state'),'data_events':v.get('data_events'),'connected':v.get('connected')} for n,v in c.items()}})
                    if any(v.get('state')=='ERROR' for n,v in c.items() if n!='ALPIDE_plane_1'):break
            elif robust in ('c7','c12'):
                should_stop=a.get('data_events',0)>=5
            elif robust=='unknown':should_stop=False
            if should_stop and index not in stopped:
                stop_attempted=True
                with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as control:
                    control.connect(str(root/'control.sock'))
                    control.sendall((json.dumps({'id':'finished-'+str(index),'intent':'stop_next'})+'\n').encode())
                    result=json.loads(control.recv(4096))
                if not result.get('accepted'): raise RuntimeError('stop request rejected')
                stopped.add(index)
                stop_at=time.monotonic_ns()
                with (root/'stop-times.jsonl').open('a') as f: f.write(json.dumps({'config':index,'elapsed_s':elapsed,'requested_s':requested_time})+'\n')
        time.sleep(.01 if case in ('timed','early','restart') else .2)
    print('state',status.get('rc_state'),'raws',[x.name for x in root.glob('*.raw')],flush=True)
    for path in root.glob('*.raw'):
        try:
            reader=pyeudaq.FileReader('native',str(path)); groups=[]
            while True:
                event=reader.GetNextEvent()
                if event is None:break
                sub=event.GetSubEvents()
                if sub:groups.append([(s.GetDescription(),s.GetTriggerN()) for s in sub])
            print(path.name, 'groups', len(groups), 'aligned', all(len(g) == 3 and len({x[1] for x in g}) == 1 for g in groups), flush=True)
            del reader
        except Exception as exc:print('raw inspection failed:',type(exc).__name__,str(exc),flush=True)
    import gc
    gc.collect()
    raw_open=[]
    raw_paths={str(p.resolve()) for p in root.glob('*.raw')}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            for fd in (process/'fd').iterdir():
                try:
                    target=os.readlink(fd)
                    if target in raw_paths:
                        command=(process/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                        raw_open.append({'pid':int(process.name),'fd':fd.name,'path':target,'command':command[:300]})
                except OSError:pass
        except OSError:pass
    (root/'robust-observation.json').write_text(json.dumps({'case':robust,'state':status.get('rc_state'),'status':status,
        'paused_at':paused_at,'kill_at':kill_at,'stop_at':stop_at,'delay_exercised':delay_exercised,
        'stalled':stalled,'count_at_pause':count_at_pause,'collector_at_kill':collector_at_kill,'observation':observation,
        'config_changed':config_changed,'changed_before_finish':changed_before_finish,
        'resumed_at':resumed_at,'error_isolated':error_isolated,'stop_error':stop_error,'stop_attempted':stop_attempted,'raw_open_fds':raw_open}))
    if robust not in ('c10a','c10b','c10resume','c10c','c11','unknown') and status.get('rc_state')!='TERMINATED':raise RuntimeError('run did not terminate')
    if robust in ('c11','unknown') and status.get('rc_state')!='ERROR':raise RuntimeError('expected error was not observed')
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
