# SPDX-License-Identifier: LGPL-3.0-or-later
"""Additional beam E2Es through the public session and Unix sockets."""
import json
import pathlib
import subprocess
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGE = 'local/public-session:native'


def run(*args, timeout=240, check=True):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(' '.join(map(str, args)) + ': ' + (p.stdout+p.stderr)[-2000:])
    return p


def wait(fn, timeout=240):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            value = fn()
            if value: return value
        except (ValueError, OSError, KeyError): pass
        time.sleep(.05)
    raise RuntimeError('timed out waiting for session condition')


def file(name, path):
    return run('docker','exec',name,'cat',path,check=False).stdout


def status(name):
    return json.loads(file(name, '/tmp/session-run/status.json'))


def beam_status(name):
    return json.loads(run('scripts/session_control.sh',name,'beam-status','--json').stdout)['status']


def malformed(name):
    code = '''import json,socket
p='/tmp/session-run/source.sock'
def ask(data):
 with socket.socket(socket.AF_UNIX) as s:
  s.settimeout(5);s.connect(p);s.sendall(data+b'\\n')
  with s.makefile('rb') as f:return json.loads(f.readline())
def status():return ask(b'{"op":"beam","action":"status"}')['status']
cases=[b'hello',b'[]',b'{"op":"beam"}',b'{"op":"beam","action":"toggle","origin":"command"}',b'{"op":"beam","action":"on","origin":"root"}',b'{"op":"beam","action":"on","origin":"command","id":"a b"}',b'{"op":"beam","action":"on","origin":"command","id":123}',b'{"op":"explode"}']
rows=[]
for case in cases:
 before=status();reply=ask(case);after=status()
 rows.append({'request':case.decode(),'reply':reply,'before':[before['beam_on'],before['seq']],'after':[after['beam_on'],after['seq']]})
print(json.dumps({'rows':rows,'final_status':ask(b'{"op":"beam","action":"status"}')}))'''
    observed=json.loads(run('docker','exec',name,'python','-c',code).stdout)
    ok=(len(observed['rows'])==8 and all(not r['reply'].get('accepted',False) and r['before']==r['after'] for r in observed['rows']) and observed['final_status'].get('accepted') is True)
    return ok,observed


def boundary():
    name='beam-boundary-'+uuid.uuid4().hex[:8]
    try:
        run('scripts/start_session.sh','--setup','configs/setups/default.ini','--name',name,'--no-attach')
        before=status(name)
        if before['rc_state']=='RUNNING': raise RuntimeError('RUNNING reached before early on request')
        early=run('scripts/session_control.sh',name,'on');early_state=beam_status(name)
        wait(lambda: status(name)['rc_state']=='RUNNING')
        wait(lambda: status(name)['run']['events']>=30)
        first=status(name)['run']['number']
        stop=run('scripts/session_control.sh',name,'stop');response=json.loads(stop.stdout)
        during=status(name)
        off=run('scripts/session_control.sh',name,'off','--json');after_off=json.loads(off.stdout)['status']
        on=run('scripts/session_control.sh',name,'on','--json');after_on=json.loads(on.stdout)['status']
        after_commands=status(name)
        raw='/tmp/session-run/run%06d.raw'%first
        fd_probe='''import glob,os,sys
target=sys.argv[1];found=False
for p in glob.glob('/proc/[0-9]*/fd/*'):
 try:found |= os.readlink(p)==target
 except OSError:pass
print('open' if found else 'closed')'''
        raw_open=run('docker','exec',name,'python','-c',fd_probe,raw).stdout.strip()=='open'
        wait(lambda: status(name)['rc_state']=='RUNNING' and status(name)['run']['number'] != first)
        records=[json.loads(x) for x in file(name,'/tmp/session-run/source.sock.beam.log').splitlines()]
        starts=[r for r in records if r['origin']=='run_start']
        inspector='''import json,pyeudaq,sys
r=pyeudaq.FileReader('native',sys.argv[1]);groups=[]
while True:
 e=r.GetNextEvent()
 if e is None:break
 if e.GetDescription()=='ITS3global': groups.append([(x.GetDescription(),x.GetTriggerN()) for x in e.GetSubEvents()])
counts={}
for g in groups:
 for n,t in g:counts[n]=counts.get(n,0)+1
print(json.dumps({'groups':len(groups),'counts':counts,'aligned':all(len(g)==len(counts) and len({t for n,t in g})==1 for g in groups)}))'''
        info=json.loads(run('docker','exec',name,'python','-c',inspector,raw,timeout=180).stdout)
        checks={'early_before_run_clock':early_state['beam_on'] is True and early_state['applied_run_ps'] is None,
                'first_and_next_start_on':len(starts)>=2 and all(x['beam_on'] and x['applied_run_ps']==0 for x in starts[:2]),
                'stop_accepted':response.get('accepted') is True,
                'actions_before_close':during['run']['number']==first and after_commands['rc_state']=='RUNNING' and after_commands['run']['number']==first and raw_open,
                'actions_applied':not after_off['beam_on'] and after_on['beam_on'],
                'aligned_device_events':info['groups']>0 and set(info['counts'])=={'ALPIDE_plane_0','ALPIDE_plane_1','ALPIDE_plane_2','ALPIDE_plane_3','ALPIDE_plane_4','ALPIDE_plane_5','msensor'} and set(info['counts'].values())=={info['groups']} and info['aligned']}
        return all(checks.values()),{'checks':checks,'before_state':before['rc_state'],'early':early_state,'stop_response':response,'state_after_stop':during['rc_state'],'state_after_commands':after_commands['rc_state'],'raw_open_after_commands':raw_open,'off':after_off,'on':after_on,'run_starts':starts[:2],'raw':info}
    finally:
        run('docker','rm','-f',name,check=False)


def invalid(image=IMAGE):
    name='beam-invalid-'+uuid.uuid4().hex[:8]
    try:
        run('scripts/start_session.sh','--setup','configs/setups/default.ini','--name',name,'--no-attach','--image',image)
        return malformed(name)
    finally:
        run('docker','rm','-f',name,check=False)


def lifecycle(toggle):
    name='beam-calls-'+uuid.uuid4().hex[:8]
    proc=None
    try:
        run('docker','create','--name',name,'--network','none',IMAGE,'sleep','600')
        for src,dst in [('tests/e2e/beam_lifecycle.py','/tmp/beam_lifecycle.py'),('tests/e2e/runcontrol_producer.py','/tmp/runcontrol_producer.py'),('configs/session/geometry.json','/tmp/session_example_geometry.json')]:
            run('docker','cp',src,name+':'+dst)
        run('docker','start',name)
        proc=subprocess.Popen(['docker','exec',name,'python','/tmp/beam_lifecycle.py'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        wait(lambda: status(name)['rc_state']=='RUNNING')
        run('scripts/session_control.sh',name,'on')
        if toggle:
            for _ in range(3):
                run('scripts/session_control.sh',name,'off');run('scripts/session_control.sh',name,'on')
        else: time.sleep(1)
        run('docker','exec',name,'touch','/tmp/runcontrol-e2e/terminating')
        run('scripts/session_control.sh',name,'terminate')
        wait(lambda:status(name)['rc_state']=='TERMINATED')
        calls=wait(lambda: [json.loads(x)['call'] for x in file(name,'/tmp/runcontrol-e2e/calls.jsonl').splitlines() if x])
        calls=[x for x in calls if x in ('Initialise','Configure','StartRun','StopRun','Reset','Terminate')]
        run('docker','exec',name,'touch','/tmp/runcontrol-e2e/finish')
        proc.communicate(timeout=30)
        if proc.returncode:raise RuntimeError('lifecycle fixture failed')
        return calls
    finally:
        run('docker','rm','-f',name,check=False)
        if proc and proc.poll() is None:proc.terminate();proc.communicate(timeout=10)


def calls_comparison():
    toggled=lifecycle(True);steady=lifecycle(False)
    return bool(toggled) and toggled==steady and all(x in toggled for x in ('Initialise','Configure','StartRun','StopRun','Terminate')),{'toggled':toggled,'steady':steady}
