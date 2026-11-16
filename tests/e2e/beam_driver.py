# SPDX-License-Identifier: LGPL-3.0-or-later
"""Beam-control E2Es using isolated public DAQ and source processes."""
import json
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
import beam_additional

HERE=pathlib.Path.cwd();OUT=HERE/'artifacts/beam';OUT.mkdir(parents=True,exist_ok=True)
TEMP=pathlib.Path(tempfile.mkdtemp(prefix='beam-e2e-driver-'))
IMAGE='local/session-core:native'
IMAGE_ID=subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip()
COMMIT=(pathlib.Path('SOURCE_REVISION').read_text().strip())
REQUIRED=['M-E1','M-E2','M-E3','M-E4','M-E5','M-E6','M-E7','M-E9','M-E10','M-E13','M-E15','M-E16']
DEFERRED=['M-E8','M-E11','M-E12','M-E14']
state={}


def command(args,*,timeout=180,allow_failure=False):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode and not allow_failure:
        raise RuntimeError(args[0]+' exit '+str(p.returncode)+'\n'+(p.stdout+p.stderr)[-5000:])
    return p


def result(name,status,measurement=None,reason=None):
    image_id = (subprocess.check_output(['docker','image','inspect','local/public-session:native','--format','{{.Id}}'],text=True).strip()
                if name in ('M-E7','M-E10','M-E13') else IMAGE_ID)
    data={'id':name,'status':status,'input_conditions':{'scenario':name,'architecture':platform.machine()},
          'measurement':measurement or {},'criterion':'documented end-to-end acceptance criteria',
          'image_id':image_id,'repository_commit':COMMIT}
    if reason:data['reason']=reason
    if name=='M-E15':data['corryvreckan_source']='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'
    state[name]=data;(OUT/(name+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')


def scenario(name,mode,*,schedule=None,secrecy=False):
    container='beam-e2e-'+uuid.uuid4().hex[:12];folder=TEMP/name;folder.mkdir()
    command(['docker','create','--name',container,'--network','none',IMAGE,'sleep','600'])
    try:
        for f in ('beam_controls.py','beam_acquire.py','beam_inspect.py','beam_restart.py','beam_secrecy.py','session_compare.py','status_compare.py','alpide_convert.cc'):
            command(['docker','cp',str(HERE/'tests/e2e'/f),container+':/tmp/'+f])
        for d in ('runcontrol_observer','session_observer'):
            command(['docker','cp',str(HERE/'tests/e2e'/d),container+':/tmp/'+d])
        command(['docker','cp',str(HERE/'configs/session/geometry.json'),container+':/tmp/session_example_geometry.json'])
        if schedule is not None:
            command(['docker','cp',str(schedule),container+':/tmp/beam-schedule.json'])
        command(['docker','cp',str(HERE/'tests/e2e/eudaq_timing.cc'),container+':/tmp/eudaq_timing.cc'])
        command(['docker','start',container])
        command(['docker','exec',container,'c++','-std=c++17','-shared','-fPIC','/tmp/eudaq_timing.cc','-ldl','-o','/tmp/eudaq_timing.so'])
        if mode=='controls':
            run=command(['docker','exec',container,'python','/tmp/beam_controls.py'],timeout=60)
        elif mode=='restart':
            run=command(['docker','exec',container,'python','/tmp/beam_restart.py'],timeout=180)
        else:
            args=['docker','exec','-e','BEAM_E2E_CASE='+mode]
            if schedule is not None:args+=['-e','BEAM_E2E_SCHEDULE_FILE=/tmp/beam-schedule.json']
            run=command(args+[container,'python','/tmp/beam_acquire.py'],timeout=150)
            command(['docker','exec',container,'python','/tmp/beam_inspect.py',mode],timeout=120)
            if secrecy:command(['docker','exec',container,'python','/tmp/beam_secrecy.py'],timeout=180)
            command(['docker','cp',container+':/tmp/beam-acquire/.',str(folder/'run')])
        (folder/'execution.log').write_text(run.stdout+run.stderr)
        command(['docker','cp',container+':/tmp/beam-results/.',str(folder/'results')])
        return folder
    finally:
        diagnostics=HERE/'artifacts/timing-observations/observations/beam';diagnostics.mkdir(parents=True,exist_ok=True)
        command(['docker','cp',container+':/tmp/beam-acquire/eudaq-timing.jsonl',str(diagnostics/(name+'-'+container+'.jsonl'))],allow_failure=True)
        if sys.exc_info()[0] is not None:
            failed=diagnostics/(name+'-'+container+'-failure');failed.mkdir(exist_ok=True)
            command(['docker','cp',container+':/tmp/beam-acquire/.',str(failed)],allow_failure=True)
        command(['docker','rm','-f',container],allow_failure=True)


def load(folder,name):return json.loads((folder/'results'/(name+'.json')).read_text())


def attempt(names,action):
    try:action()
    except Exception as exc:
        for name in names:
            if name not in state:result(name,'fail',reason=str(exc))
        print('FAILED',','.join(names),str(exc),flush=True)


def controls():
    f=scenario('controls','controls')
    for name in ('M-E1','M-E5'):
        r=load(f,name);result(name,r['status'],r.get('measurement'))


def off():
    f=scenario('off','off');r=load(f,'M-E2');result('M-E2',r['status'],r.get('measurement'))


def manual_replay():
    manual=scenario('manual','manual',secrecy=True)
    edge=scenario('edge','edge')
    m3=load(manual,'M-E3');boundary=load(edge,'M-E3-edge')
    result('M-E3','pass' if m3['status']=='pass' and boundary['status']=='pass' else 'fail',
           {'manual':m3.get('measurement'),'crossing_boundary':boundary.get('measurement')})
    for name in ('M-E4','M-E15'):
        r=load(manual,name);result(name,r['status'],r.get('measurement'))
    schedule=manual/'run/replay-schedule.json'
    requested=json.loads(schedule.read_text())
    replay=scenario('replay','replay',schedule=schedule)
    a=json.loads((manual/'run/comparison.json').read_text())
    b=json.loads((replay/'run/comparison.json').read_text())
    rows=[json.loads(x) for x in (replay/'run/source.sock.beam.log').read_text().splitlines()]
    actual=[{'time_ps':x['applied_run_ps'],'on':x['beam_on']} for x in rows if x['origin']=='schedule']
    same={key:a[key]==b[key] for key in ('events','stream','status')}
    manual_truth=[json.loads(x.read_text()) for x in (manual/'run/truth').glob('run-*.json')]
    replay_truth=[json.loads(x.read_text()) for x in (replay/'run/truth').glob('run-*.json')]
    endpoints=len(manual_truth)==len(replay_truth)==1 and manual_truth[0]['end_ps']==replay_truth[0]['end_ps']
    ok=all(same.values()) and requested==actual and endpoints
    result('M-E9','pass' if ok else 'fail',{'comparison':same,'schedule_applied_exactly':requested==actual,
           'same_end':endpoints,'schedule':requested,'excluded_intermediate_status_counts':{'manual':a['excluded_intermediate_status_counts'],'replay':b['excluded_intermediate_status_counts']}})


def restart():
    f=scenario('restart','restart');r=load(f,'M-E6');result('M-E6',r['status'],r.get('measurement'),r.get('reason'))


def previous():
    p=command(['bash','tests/scripts/run_session_core_e2e.sh'],timeout=1800,allow_failure=True)
    names=['C-E1','C-E2','C-E3','C-E4','C-E5','C-E6','C-E8','C-E9','C-E14','C-E17']
    statuses={}
    for name in names:
        path=HERE/'artifacts/session-core'/(name+'.json')
        statuses[name]=json.loads(path.read_text()).get('status','missing') if path.exists() else 'missing'
    (OUT/'session-core-e2e.log').write_text(p.stdout+p.stderr)
    ok=p.returncode==0 and all(x=='pass' for x in statuses.values())
    result('M-E16','pass' if ok else 'fail',{'exit_code':p.returncode,'checks':statuses})

def additional(name, fn):
    ok, measurement = fn()
    result(name, 'pass' if ok else 'fail', measurement)

try:
    for name in REQUIRED:result(name,'not_run',reason='check has not run yet')
    state.clear()
    for name in DEFERRED:result(name,'not_run',reason='deferred; not checked')
    attempt(('M-E1','M-E5'),controls)
    attempt(('M-E2',),off)
    attempt(('M-E3','M-E4','M-E9','M-E15'),manual_replay)
    attempt(('M-E6',),restart)
    attempt(('M-E7',),lambda:additional('M-E7',beam_additional.calls_comparison))
    attempt(('M-E10',),lambda:additional('M-E10',beam_additional.boundary))
    attempt(('M-E13',),lambda:additional('M-E13',beam_additional.invalid))
    attempt(('M-E16',),previous)
    missing=[x for x in REQUIRED if state.get(x,{}).get('status')!='pass']
    print(json.dumps({'required':len(REQUIRED),'not_passed':missing},ensure_ascii=False),flush=True)
    sys.exit(1 if missing else 0)
finally:
    shutil.rmtree(TEMP,ignore_errors=True)
