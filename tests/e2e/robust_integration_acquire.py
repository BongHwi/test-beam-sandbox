# SPDX-License-Identifier: LGPL-3.0-or-later
"""Acquire seven public-producer streams under the real launcher and RunControl."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import traceback

case=os.environ.get('INTEGRATION_CASE','two')
root=Path(os.environ.get('INTEGRATION_ROOT','/tmp/integration-e2e'));root.mkdir(parents=True,exist_ok=True)
sock=str(root/'source.sock');truth=str(root/'truth')

from integration.prepare import prepare
prepare(root,case)
os.chdir(root)
private={'seed':1857092461,'device_seed':1857092460,'beam':{'position_mm':[1.234567891,-2.345678912,-20],
         'direction':[0.00123456789,0,1],'phase_space_covariance':[[0.00345678912,0,0,0,0],[0,0.00456789123,0,0,0]]+[[0]*5 for _ in range(3)]},'rate_hz':237.654321987}
if case=='replay':private['beam_schedule']=json.loads(Path(os.environ['INTEGRATION_SCHEDULE']).read_text())
env=dict(os.environ, LD_PRELOAD='/tmp/eudaq_timing.so', EUDAQ_TIMING_TRACE=str(root/'eudaq-timing.jsonl'),SESSION_SOURCE_CONFIG_JSON=json.dumps(private),SESSION_SOURCE_SOCKET=sock,
         VIRTUAL_ALPIDE_SOURCE_SOCKET=sock,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
         GIT_DIR='/opt/virtual-producer-cwd/.git',GIT_WORK_TREE='/opt/virtual-producer-cwd',
         PYTHONPATH='/tmp/session_observer:'+os.environ['PYTHONPATH'],SESSION_E2E_TRUTH=truth,
         RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
         RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),
     '--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44000',
     '--producer-env','SESSION_SOURCE_SOCKET','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET',
     '--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--producer-env','LD_PRELOAD','--producer-env','EUDAQ_TIMING_TRACE','--producer-env','GIT_DIR','--producer-env','GIT_WORK_TREE','--no-attach','--session-geometry',str(root/'geometry.json'),
     '--session-socket',sock,'--session-truth-dir',truth,
     '--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']
# Some standalone and fault fixtures do not install the optional native observer.
# Avoid passing a missing preload library to their subprocesses.
if not Path('/tmp/eudaq_timing.so').is_file():
    for key in ('LD_PRELOAD','EUDAQ_TIMING_TRACE'):
        env.pop(key,None)
        index=cmd.index(key)
        del cmd[index-1:index+1]

if case in ('two','manual'):cmd.append('--session-beam-on')
def beam(action):
    p=subprocess.run(['python','-m','session_core.beamctl','--socket',sock,action],env=env,capture_output=True,text=True,timeout=10)
    if p.returncode:raise RuntimeError('beam command failed')
    with (root/'beam-commands.jsonl').open('a') as f:f.write(p.stdout.strip()+'\n')
samples=[]
def resource_sample():
    participants={}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            cmdline=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            patterns=('ITS3RunControl.py','ITS3DataCollector.py','ALPIDEProducer.py','msensor_producer.py','session_core.server')
            role=next((x for x in patterns if x in cmdline),None)
            if not role: continue
            stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
            participants[proc.name]={'role':role,'cpu_s':(int(stat[11])+int(stat[12]))/os.sysconf('SC_CLK_TCK'),
                                     'rss_bytes':int(stat[21])*os.sysconf('SC_PAGE_SIZE')}
        except (OSError,ValueError,IndexError): continue
    expected={'ITS3RunControl.py':1,'ITS3DataCollector.py':1,'ALPIDEProducer.py':6,'msensor_producer.py':1,'session_core.server':1}
    counts={role:sum(x['role']==role for x in participants.values()) for role in expected}
    if counts==expected:
        samples.append({'monotonic':time.monotonic(),'processes':participants,'rss_bytes':sum(x['rss_bytes'] for x in participants.values())})

try:
    p=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(p.stdout+p.stderr)
    if p.returncode:raise RuntimeError('launcher failed')
    deadline=time.monotonic()+180;status={};started={};stopped=set();sent=[]
    while time.monotonic()<deadline:
        try:status=json.loads((root/'status.json').read_text())
        except (OSError,ValueError):pass
        if status.get('rc_state') in ('TERMINATED','ERROR'):break
        if status.get('rc_state')=='RUNNING':
            resource_sample()
            index=status['config']['index'];started.setdefault(index,time.monotonic());elapsed=time.monotonic()-started[index]
            if case=='manual':
                for i,(at,action) in enumerate(((.8,'off'),(1.6,'on'))):
                    if elapsed>=at and i not in sent:beam(action);sent.append(i)
            done=any(c['name']=='MSENSOR_0' and c['message']=='source_finished' for c in status.get('connections',[]))
            if done and index not in stopped:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
                    s.settimeout(5);s.connect(str(root/'control.sock'));s.sendall((json.dumps({'id':'finished-'+str(index),'intent':'stop_next'})+'\n').encode())
                    if not json.loads(s.recv(4096)).get('accepted'):raise RuntimeError('stop rejected')
                stopped.add(index)
        time.sleep(.02)
    (root/'resource-samples.json').write_text(json.dumps(samples))
    if status.get('rc_state')!='TERMINATED' or len(stopped)!=(2 if case=='two' else 1):raise RuntimeError('run did not finish normally')
    if case=='manual' and len(sent)!=2:raise RuntimeError('beam actions incomplete')
    panes=subprocess.run(['tmux','list-panes','-a','-F','#{session_name}:#{window_index}.#{pane_index} #{pane_title}'],capture_output=True,text=True,check=True).stdout
    screens=[]
    for row in panes.splitlines():
        name=row.split(' ',1)[0];p=subprocess.run(['tmux','capture-pane','-p','-t',name,'-S','-10000'],capture_output=True,text=True,check=True);screens.append(row+'\n'+p.stdout)
    (root/'panes.log').write_text('\n'.join(screens))
    print(json.dumps({'case':case,'state':status['rc_state'],'raw_count':len(list(root.glob('*.raw'))),'stopped':len(stopped)}),flush=True)
except Exception:
    traceback.print_exc()
    print((root/'launcher.log').read_text(errors='replace') if (root/'launcher.log').exists() else '',flush=True)
    for pane in ('rc','rp.0','rp.1','rp.2','rp.3','rp.4','rp.5','dp','dc'):
        p=subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+pane,'-S','-200'],capture_output=True,text=True);print('PANE',pane,p.stdout[-4000:],flush=True)
    raise
finally:
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
