# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise accepted requests through isolated public-DAQ sessions."""
import argparse, concurrent.futures, gzip, json, os, platform, subprocess, threading, uuid
from pathlib import Path

ROOT=Path.cwd();OUT=ROOT/'artifacts/accepted-requests';TRIALS=OUT/'trials';TRIALS.mkdir(parents=True,exist_ok=True)
IMAGE='local/session-core:native';BASE='local/runcontrol:native'
COMMIT=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
IMAGE_ID=subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip()
LOCK=threading.Lock()

def cmd(args,timeout=120,allow=False):
 p=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(str(args[0])+' exit '+str(p.returncode)+': '+(p.stdout+p.stderr)[-2000:])
 return p

def outcome(identifier,status,measurement,criterion):
 data={'id':identifier,'status':status,'input_conditions':{'architecture':platform.machine(),'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'},'measurement':measurement,'criterion':criterion,'repository_commit':COMMIT,'image_id':IMAGE_ID}
 (OUT/(identifier+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
 print(identifier,status,flush=True);return status

def trial(kind,index):
 name='accepted-e2e-'+uuid.uuid4().hex[:12];folder=TRIALS/kind/f'{index:03d}';folder.mkdir(parents=True,exist_ok=True)
 delays=(0,.005,.015,.05,.1,.2,.3,.35);delay=delays[index%len(delays)] if kind=='running' else 0
 result={'kind':kind,'id':index,'requested_delay_s':delay,'repository_commit':COMMIT,'image_id':IMAGE_ID}
 try:
  cmd(['docker','create','--name',name,'--network','none',IMAGE,'sleep','300'])
  for file in ('accepted_python','accepted_core.py','accepted_collector.py','accepted_scenario.py','accepted_inspect.py'):
   cmd(['docker','cp',ROOT/'tests/e2e'/file,name+':/tmp/'+file])
  for directory in ('accepted_observer','session_observer','runcontrol_observer'):
   cmd(['docker','cp',ROOT/'tests/e2e'/directory,name+':/tmp/'+directory])
  cmd(['docker','start',name])
  extra=[]
  if os.environ.get('ACCEPTED_SINK_HOLD_SECONDS'):
   extra=['-e','ACCEPTED_SINK_HOLD_SECONDS='+os.environ['ACCEPTED_SINK_HOLD_SECONDS']]
  acquired=cmd(['docker','exec',*extra,'-e','ACCEPTED_STOP_KIND='+kind,'-e','ACCEPTED_STOP_DELAY='+str(delay),name,'python','/tmp/accepted_scenario.py'],timeout=90,allow=True)
  inspected=cmd(['docker','exec',name,'python','/tmp/accepted_inspect.py'],timeout=60,allow=True)
  copied=cmd(['docker','cp',name+':/tmp/session-e2e/accepted-observations/result.json',folder/'observed.json'],allow=True)
  if copied.returncode:raise RuntimeError('inspection result unavailable: '+(inspected.stdout+inspected.stderr)[-1200:])
  observation=json.loads((folder/'observed.json').read_text());(folder/'observed.json').unlink()
  timeline=observation.pop('timeline',[])
  with gzip.open(folder/'timeline.json.gz','wt',encoding='utf-8') as stream:json.dump(timeline,stream)
  result.update(observation,acquisition_exit=acquired.returncode,inspection_exit=inspected.returncode,timeline_records=len(timeline))
  accepted=bool((observation.get('request') or {}).get('response',{}).get('accepted'))
  if acquired.returncode and accepted:result['status']='fail';result['reason']='accepted request did not complete; '+(acquired.stdout+acquired.stderr)[-1600:]
  if acquired.returncode and not accepted and not (observation.get('request') or {}).get('response',{}).get('reason'):
   result['status']='fail';result['reason']='no valid rejection; '+(acquired.stdout+acquired.stderr)[-1600:]
  if observation.get('raw',{}).get('invalid_count'):
   for raw in observation['raw'].get('files',[]):cmd(['docker','cp',name+':/tmp/session-e2e/'+raw,folder/raw],allow=True)
   cmd(['docker','cp',name+':/tmp/session-e2e/accepted-observations/.',folder/'observations'],allow=True)
 except Exception as exc:
  result.update(status='fail',reason='trial could not be completed: '+str(exc))
 finally:cmd(['docker','rm','-f',name],allow=True)
 (folder/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 with LOCK:print(kind,index,result['status'],'invalid=',result.get('raw',{}).get('invalid_count'),flush=True)
 return result

def intents():
 rows={}
 for kind in ('repeat','terminate'):
  name='accepted-intent-'+uuid.uuid4().hex[:12]
  try:
   cmd(['docker','create','--name',name,'--network','none',BASE,'sleep','180'])
   for file in ('accepted_intents.py','runcontrol_producer.py'):cmd(['docker','cp',ROOT/'tests/e2e'/file,name+':/tmp/'+file])
   cmd(['docker','cp',ROOT/'tests/e2e/runcontrol_observer',name+':/tmp/runcontrol_observer'])
   cmd(['docker','start',name]);run=cmd(['docker','exec','-e','ACCEPTED_INTENT='+kind,name,'python','/tmp/accepted_intents.py'],timeout=90,allow=True)
   p=cmd(['docker','cp',name+':/tmp/accepted-intents.json',OUT/(kind+'.json')],allow=True)
   if p.returncode:raise RuntimeError('intent result missing: '+(run.stdout+run.stderr)[-2000:])
   rows[kind]=json.loads((OUT/(kind+'.json')).read_text());rows[kind]['exit_code']=run.returncode
  except Exception as exc:rows[kind]={'status':'fail','reason':str(exc)}
  finally:cmd(['docker','rm','-f',name],allow=True)
 return rows

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--running',type=int,default=100);parser.add_argument('--joining',type=int,default=40);parser.add_argument('--workers',type=int,default=2);args=parser.parse_args()
 if not 0<=args.running<=100 or not 0<=args.joining<=40 or not 1<=args.workers<=2:raise SystemExit('invalid trial counts or worker count')
 cases=intents();ok3=all(x.get('status')=='pass' and x.get('exit_code')==0 for x in cases.values())
 s3=outcome('T-E3','pass' if ok3 else 'fail',cases,'accepted repeat and terminate immediately after RUNNING take effect')
 rows={'running':[],'joining':[]}
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
  jobs={pool.submit(trial,kind,i):kind for kind,count in [('running',args.running),('joining',args.joining)] for i in range(count)}
  for future in concurrent.futures.as_completed(jobs):rows[jobs[future]].append(future.result())
 statuses=[]
 for kind,identifier,required in [('running','T-E1',100),('joining','T-E2',40)]:
  values=rows[kind];accepted=sum(bool((x.get('request') or {}).get('response',{}).get('accepted')) for x in values)
  rejected=sum(not (x.get('request') or {}).get('response',{}).get('accepted',False) and bool((x.get('request') or {}).get('response',{}).get('reason')) for x in values)
  failures=[{'id':x['id'],'reason':x.get('reason'),'checks':x.get('checks'),'raw':x.get('raw')} for x in values if x.get('status')!='pass']
  missing=[x['id'] for x in values if x.get('raw',{}).get('invalid_count',0)]
  status='not_run' if len(values)<required else 'fail' if failures else 'pass'
  statuses.append(outcome(identifier,status,{'attempted':len(values),'required':required,'accepted':accepted,'rejected_with_reason':rejected,'failed_trials':failures,'producer_missing_trials':missing,'delays_s':sorted({x['requested_delay_s'] for x in values})},'each request is applied or properly rejected; accepted raw closes with complete producer events'))
 return 0 if s3=='pass' and all(s=='pass' for s in statuses) else 1
if __name__=='__main__':raise SystemExit(main())
