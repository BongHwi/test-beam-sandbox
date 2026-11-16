# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise immediate repeat and terminate requests against real RunControl."""
import json, os, pathlib, socket, subprocess, sys, time, traceback
root=pathlib.Path('/tmp/accepted-intents');root.mkdir(exist_ok=True);os.chdir(root)
out=pathlib.Path('/tmp/accepted-intents.json');kind=os.environ['ACCEPTED_INTENT']
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/accepted-intents/a.conf,/tmp/accepted-intents/b.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
for name,value in [('a','first'),('b','second')]:
 (root/(name+'.conf')).write_text(f'[RunControl]\nNEVENTS = 1000000\n[Producer.TEST_0]\nVALUE = {value}\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/accepted-intents/run$3R$X\n')
# The public fixture logs its callbacks in this fixed path.
fixture=pathlib.Path('/tmp/runcontrol-e2e');fixture.mkdir(exist_ok=True)
cmd='python -u /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py --address 44003 --ini /tmp/accepted-intents/init.ini --status-file /tmp/accepted-intents/status.json --control-socket /tmp/accepted-intents/control.sock --one-pass'
procs=[];requests=[]
def calls():
 try:return [json.loads(x) for x in (fixture/'calls.jsonl').read_text().splitlines() if x]
 except (OSError,ValueError):return []
def wait(predicate,timeout=35):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  value=predicate()
  if value:return value
  time.sleep(.003)
 raise RuntimeError('deadline waiting for RunControl lifecycle')
def state():
 try:return json.loads((root/'status.json').read_text())
 except (OSError,ValueError):return {}
def running_entries():
 try:
  rows=[json.loads(line) for line in (root/'published.jsonl').read_text().splitlines() if line]
 except (OSError,ValueError):return []
 entries=[]; previous=None
 for row in rows:
  current=row.get('rc_state')
  if current=='RUNNING' and previous!='RUNNING':entries.append(row)
  previous=current
 return entries

def send(intent,run_index):
 def ready():
  entries=running_entries()
  if state().get('rc_state')=='RUNNING' and len(entries)>=run_index and sum(x.get('call')=='StartRun' for x in calls())>=run_index:
   return entries[run_index-1]
 published=wait(ready)
 with socket.socket(socket.AF_UNIX) as c:
  c.settimeout(4);c.connect(str(root/'control.sock')); before=time.monotonic_ns()
  c.sendall((json.dumps({'id':str(run_index),'intent':intent})+'\n').encode());response=json.loads(c.recv(4096));after=time.monotonic_ns()
 delay=(before-published['updated_monotonic_ns'])/1e9
 requests.append({'kind':intent,'response':response,'sent_ns':before,'reply_ns':after,'running_published_ns':published['updated_monotonic_ns'],'delay_s':delay})
 if not 0<=delay<=.5:raise RuntimeError('request was not sent immediately after RUNNING: '+str(delay))
 if not response.get('accepted'):raise RuntimeError('request rejected: '+str(response))
try:
 observer=dict(os.environ,PYTHONPATH='/tmp/runcontrol_observer:'+os.environ.get('PYTHONPATH',''),RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
 subprocess.run(['tmux','new-session','-d','-s','accepted-intents',cmd],check=True,env=observer)
 wait(lambda:(root/'control.sock').exists())
 for name,args in [('collector',['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003']),('producer',['python','/tmp/runcontrol_producer.py'])]:
  procs.append(subprocess.Popen(args,stdout=open(name+'.log','w'),stderr=subprocess.STDOUT))
 send(kind,1)
 if kind=='repeat':
  wait(lambda:sum(c.get('call')=='Configure' and c.get('value')=='first' for c in calls())>=2)
  send('terminate',2)
 wait(lambda:any(x.get('call')=='Terminate' for x in calls()))
 lifecycle=[x for x in calls() if x.get('call') not in ('Event','Status')]
 ok=(kind=='terminate' or sum(c.get('call')=='Configure' and c.get('value')=='first' for c in lifecycle)>=2)
 result={'status':'pass' if ok else 'fail','kind':kind,'requests':requests,'lifecycle':lifecycle}
except Exception as exc:
 result={'status':'fail','kind':kind,'requests':requests,'reason':str(exc),'traceback':traceback.format_exc(),'lifecycle':[x for x in calls() if x.get('call') not in ('Event','Status')]}
finally:
 for p in procs:
  if p.poll() is None:p.terminate()
 subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));sys.exit(0 if result['status']=='pass' else 1)
