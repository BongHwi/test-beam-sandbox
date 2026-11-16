#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Measure all ten acquisition processes during real seven-device runs."""
import json,platform,subprocess,sys,tempfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/robustness';OUT.mkdir(parents=True,exist_ok=True)
image='local/session-core:native';name='robust-resources-'+uuid.uuid4().hex[:10]
revision=(ROOT/'SOURCE_REVISION').read_text().strip()
image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()

def command(*args,allow=False,timeout=300):
 p=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(args[0]+' failed: '+(p.stdout+p.stderr)[-3000:])
 return p

def write(status,measurement,reason=None):
 d={'id':'I-E12','status':status,'input_conditions':{'architecture':platform.machine(),'producers':7,'processes_expected':10},'measurement':measurement,'criterion':'documented end-to-end acceptance criteria','repository_commit':revision,'image_id':image_id}
 if reason:d['reason']=reason
 (OUT/'I-E12.json').write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print('I-E12',status,flush=True)
try:
 command('docker','create','--name',name,'--network','none',image,'sleep','600')
 command('docker','cp',str(ROOT/'tests/e2e/robust_integration_acquire.py'),name+':/tmp/integration_acquire.py')
 for folder in ('integration','tests/e2e/session_observer','tests/e2e/runcontrol_observer'):
  command('docker','cp',str(ROOT/folder),name+':/tmp/'+Path(folder).name)
 command('docker','start',name)
 acquisition=command('docker','exec','-e','INTEGRATION_CASE=two',name,'python','/tmp/integration_acquire.py',timeout=240)
 with tempfile.TemporaryDirectory(prefix='resource-results-') as d:
  path=Path(d)/'samples.json';command('docker','cp',name+':/tmp/integration-e2e/resource-samples.json',str(path))
  samples=json.loads(path.read_text())
  windows=[]
  for i,a in enumerate(samples):
   for b in samples[i+1:]:
    duration=b['monotonic']-a['monotonic']
    if duration<1:continue
    if duration>1.3:break
    if set(a['processes'])!=set(b['processes']):break
    by_role={}
    for pid,process in a['processes'].items():
     role=process['role'];by_role[role]=by_role.get(role,0)+(b['processes'][pid]['cpu_s']-process['cpu_s'])/duration
    windows.append({'elapsed_s':duration,'cpu_cores':sum(by_role.values()),'by_role':by_role});break
  peak_rss=max((s['rss_bytes'] for s in samples),default=None)
  peak_cpu=max((w['cpu_cores'] for w in windows),default=None)
  ok=bool(windows) and peak_rss is not None and peak_cpu<=4 and peak_rss<=8*1024**3
  measurement={'acquisition_exit':acquisition.returncode,'samples':len(samples),'one_second_windows':len(windows),'peak_cpu_cores':peak_cpu,'peak_window':max(windows,key=lambda w:w['cpu_cores']) if windows else None,'peak_rss_bytes':peak_rss,'cpu_limit_cores':4,'rss_limit_gib':8,'sample_roles':{k:sum(x['role']==k for x in samples[0]['processes'].values()) for k in sorted({x['role'] for x in samples[0]['processes'].values()})} if samples else {}}
  write('pass' if ok else 'fail',measurement)
  sys.exit(0 if ok else 1)
except Exception as exc:
 write('fail',{},str(exc));sys.exit(1)
finally:command('docker','rm','-f',name,allow=True)
