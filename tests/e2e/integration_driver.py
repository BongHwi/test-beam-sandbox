# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the task-7 integrated acquisitions and checks in disposable containers."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE=Path.cwd();OUT=HERE/'artifacts/integration';OUT.mkdir(parents=True,exist_ok=True)
TEMP=Path(tempfile.mkdtemp(prefix='integration-e2e-'))
IMAGE='local/session-core:native'
IMAGE_ID=subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip()
COMMIT=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
REQUIRED=['I-E1','I-E2','I-E3','I-E4','I-E5','I-E6','I-E7','I-E8','I-E8a','I-E9','I-E10']
state={}

def command(args,timeout=300,allow_failure=False):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode and not allow_failure:raise RuntimeError(args[0]+' exit '+str(p.returncode)+'\n'+(p.stdout+p.stderr)[-4000:])
    return p

def result(name,status,measurement=None,reason=None):
    data={'id':name,'status':status,'input_conditions':{'architecture':platform.machine()},'measurement':measurement or {},
          'criterion':'documented end-to-end acceptance criteria; documented end-to-end acceptance criteria','image_id':IMAGE_ID,'repository_commit':COMMIT,
          'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'}
    if reason:data['reason']=reason
    state[name]=data;(OUT/(name+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    print(name,status,flush=True)

def scenario(name,mode,schedule=None):
    container='integration-e2e-'+uuid.uuid4().hex[:12];folder=TEMP/name;folder.mkdir()
    command(['docker','create','--name',container,'--network','none',IMAGE,'sleep','1800'])
    try:
        for f in ('integration_acquire.py','integration_analysis.py','integration_inspect.py','integration_beam_inspect.py','integration_secrecy.py','status_compare.py'):
            command(['docker','cp',str(HERE/'tests/e2e'/f),container+':/tmp/'+f])
        for d in ('runcontrol_observer','session_observer'):
            command(['docker','cp',str(HERE/'tests/e2e'/d),container+':/tmp/'+d])
        command(['docker','cp',str(HERE/'integration'),container+':/tmp/integration'])
        if schedule:command(['docker','cp',str(schedule),container+':/tmp/replay-schedule.json'])
        command(['docker','cp',str(HERE/'tests/e2e/eudaq_timing.cc'),container+':/tmp/eudaq_timing.cc'])
        command(['docker','start',container])
        command(['docker','exec',container,'c++','-std=c++17','-shared','-fPIC','/tmp/eudaq_timing.cc','-ldl','-o','/tmp/eudaq_timing.so'])
        if mode in ('two','manual'):
            command(['docker','exec',container,'bash','/tmp/integration/build_modules.sh'],timeout=120)
        env=['-e','INTEGRATION_CASE='+mode]
        if schedule:env+=['-e','INTEGRATION_SCHEDULE=/tmp/replay-schedule.json']
        acquisition=command(['docker','exec',*env,container,'python','/tmp/integration_acquire.py'],timeout=240)
        (folder/'acquire.log').write_text(acquisition.stdout+acquisition.stderr)
        if mode=='two':
            for i,label in enumerate(('a','b')):
                cmd='raws=(/tmp/integration-e2e/*.raw); python /tmp/integration_analysis.py "${raws['+str(i)+']}" /tmp/integration-e2e/analysis-'+label
                command(['docker','exec',container,'bash','-lc',cmd],timeout=300)
            command(['docker','exec',container,'python','/tmp/integration_inspect.py'],timeout=180)
        elif mode=='manual':
            command(['docker','exec',container,'bash','-lc','python /tmp/integration_analysis.py /tmp/integration-e2e/*.raw /tmp/integration-e2e/analysis'],timeout=300)
            command(['docker','exec',container,'python','/tmp/integration_beam_inspect.py','manual'],timeout=180)
            command(['docker','exec',container,'python','/tmp/integration_secrecy.py'],timeout=300,allow_failure=True)
        else:command(['docker','exec',container,'python','/tmp/integration_beam_inspect.py',mode],timeout=180)
        command(['docker','cp',container+':/tmp/integration-e2e/.',str(folder/'run')],timeout=180)
        command(['docker','cp',container+':/tmp/integration-results/.',str(folder/'results')],allow_failure=True)
        return folder
    finally:
        diagnostics=HERE/'artifacts/timing-observations/observations/integration';diagnostics.mkdir(parents=True,exist_ok=True)
        command(['docker','cp',container+':/tmp/integration-e2e/eudaq-timing.jsonl',str(diagnostics/(name+'-'+container+'.jsonl'))],allow_failure=True)
        if sys.exc_info()[0] is not None:
            failed=diagnostics/(name+'-'+container+'-failure');failed.mkdir(exist_ok=True)
            command(['docker','cp',container+':/tmp/integration-e2e/.',str(failed)],allow_failure=True)
        command(['docker','rm','-f',container],allow_failure=True)

def load(folder,name):return json.loads((folder/'results'/(name+'.json')).read_text())
def attempt(names,fn):
    try:fn()
    except Exception as exc:
        for name in names:
            if name not in state:result(name,'fail',reason=str(exc))

def two():
    folder=scenario('two','two')
    for name in ('I-E1','I-E2','I-E3','I-E4','I-E5','I-E6','I-E7'):
        row=load(folder,name);result(name,row['status'],row.get('measurement'))

def off():
    folder=scenario('off','off');row=load(folder,'I-E8');result('I-E8',row['status'],row.get('measurement'))

def manual_and_replay():
    manual=scenario('manual','manual')
    for name in ('I-E8a','I-E10'):
        row=load(manual,name);result(name,row['status'],row.get('measurement'))
    requested=json.loads((manual/'run/replay-schedule.json').read_text())
    a=scenario('replay1','replay',schedule=manual/'run/replay-schedule.json')
    b=scenario('replay2','replay',schedule=manual/'run/replay-schedule.json')
    compare=[json.loads((x/'run/comparison.json').read_text()) for x in (a,b)]
    equal={key:compare[0][key]==compare[1][key] for key in ('events','stream','status')}
    applied=[];ends=[]
    manual_truth=[json.loads(x.read_text()) for x in (manual/'run/truth').glob('run-*.json')]
    manual_end=manual_truth[0]['end_ps'] if len(manual_truth)==1 else None
    for folder in (a,b):
        rows=[json.loads(x) for x in (folder/'run/source.sock.beam.log').read_text().splitlines()]
        applied.append([{'time_ps':x['applied_run_ps'],'on':x['beam_on']} for x in rows if x['origin']=='schedule'])
        truth=[json.loads(x.read_text()) for x in (folder/'run/truth').glob('run-*.json')]
        ends.append(truth[0]['end_ps'] if len(truth)==1 else None)
    ok=all(equal.values()) and applied==[requested,requested] and manual_end is not None and ends==[manual_end,manual_end]
    result('I-E9','pass' if ok else 'fail',{'comparison':equal,'requested_schedule_applied':[x==requested for x in applied],
           'equal_end':manual_end is not None and ends==[manual_end,manual_end],'schedule':requested,'excluded_intermediate_status_counts':[x['excluded_intermediate_status_counts'] for x in compare]})
try:
    for name in REQUIRED:result(name,'not_run',reason='check has not run yet')
    state.clear()
    for name in ('I-E11','I-E12'):result(name,'not_run',reason='deferred; not checked')
    attempt(REQUIRED[:7],two)
    attempt(('I-E8',),off)
    attempt(('I-E8a','I-E9','I-E10'),manual_and_replay)
    failed=[name for name in REQUIRED if state.get(name,{}).get('status')!='pass']
    print(json.dumps({'required':len(REQUIRED),'not_passed':failed},ensure_ascii=False),flush=True)
    sys.exit(1 if failed else 0)
finally:shutil.rmtree(TEMP,ignore_errors=True)
