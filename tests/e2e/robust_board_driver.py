#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Measure idle CPU, delayed stop, and Git worktree behavior with public EUDAQ."""
import json,os,platform,subprocess,sys,tempfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/robustness';OUT.mkdir(parents=True,exist_ok=True)
image='local/virtual-alpide:native'
revision=(ROOT/'SOURCE_REVISION').read_text().strip()
image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
name='robust-board-'+uuid.uuid4().hex[:10]
seen=set()

def command(*args,allow=False,timeout=180):
 p=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow: raise RuntimeError('command failed: '+args[0]+': '+(p.stdout+p.stderr)[-3000:])
 return p

def result(id,status,measurement,reason=None):
 doc={'id':id,'status':status,'input_conditions':{'architecture':platform.machine(),'public_producer':'pinned ITS3'},'measurement':measurement,'criterion':'documented end-to-end acceptance criteria','repository_commit':revision,'image_id':image_id}
 if reason:doc['reason']=reason
 (OUT/(id+'.json')).write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n');print(id,status,flush=True)
 seen.add(id)
 return status=='pass'

try:
 command('docker','create','--name',name,'--network','none',image,'sleep','600')
 for src,dst in [('tests/e2e/robust_board_main.py','alpide_main.py'),('tests/e2e/robust_board_scenarios.py','alpide_scenarios.py'),('tests/e2e/alpide_test_source.py','alpide_test_source.py'),('tests/e2e/alpide_convert.cc','alpide_convert.cc')]:
  command('docker','cp',str(ROOT/src),name+':/tmp/'+dst)
 command('docker','cp',str(ROOT/'tests/e2e/alpide_observer'),name+':/tmp/alpide-observer')
 command('docker','start',name)
 command('docker','exec',name,'python','/tmp/alpide_main.py')
 for case in ('idle','boundary','outside'):command('docker','exec',name,'python','/tmp/alpide_scenarios.py',case)
 with tempfile.TemporaryDirectory(prefix='robust-board-results-') as temp:
  command('docker','cp',name+':/tmp/alpide-results/.',temp)
  d=Path(temp)
  def load(x):return json.loads((d/x).read_text())
  outcomes=[]
  for i in (9,12):
   row=load('V-E%d.json'%i);outcomes.append(result('V-E%d'%i,row['status'],row['measurement'],row.get('reason')))
  inside=load('V-E14.json');outside=load('outside-observation.json')
  ok=inside.get('measurement',{}).get('inside_tree_matches') is True and outside.get('status')=='pass'
  outcomes.append(result('V-E14','pass' if ok else 'fail',{'fixed_tree':inside.get('measurement'),'outside':{'state':outside.get('state'),'status':outside.get('status')}}))
  sys.exit(0 if all(outcomes) else 1)
except Exception as exc:
 for i in (9,12,14):
  if 'V-E%d'%i not in seen:result('V-E%d'%i,'fail',{},str(exc))
 print(str(exc),file=sys.stderr);sys.exit(1)
finally:
 command('docker','rm','-f',name,allow=True)
