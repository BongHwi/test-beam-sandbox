# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check rejection reasons using real public RunControl and a live producer."""
import json, pathlib, socket, subprocess, sys, time, traceback
root=pathlib.Path('/tmp/accepted-rejections');root.mkdir(exist_ok=True)
fixture=pathlib.Path('/tmp/runcontrol-e2e');fixture.mkdir(exist_ok=True)
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/accepted-rejections/a.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
(root/'a.conf').write_text('[RunControl]\nNEVENTS = 1000000\n[Producer.TEST_0]\nVALUE = first\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/accepted-rejections/run$3R$X\n')
command='python -u /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py --address 44003 --ini /tmp/accepted-rejections/init.ini --status-file /tmp/accepted-rejections/status.json --control-socket /tmp/accepted-rejections/control.sock --one-pass'
processes=[]; responses={}
def wait(predicate,timeout=40):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  value=predicate()
  if value:return value
  time.sleep(.003)
 raise RuntimeError('deadline while awaiting RunControl state')
def state():
 try:return json.loads((root/'status.json').read_text())
 except (OSError,ValueError):return {}
def request(identifier,intent):
 with socket.socket(socket.AF_UNIX) as client:
  client.settimeout(4);client.connect(str(root/'control.sock'));client.sendall((json.dumps({'id':identifier,'intent':intent})+'\n').encode());return json.loads(client.recv(4096))
try:
 subprocess.run(['tmux','new-session','-d','-s','accepted-rejections',command],check=True)
 wait(lambda:(root/'control.sock').exists())
 for name,args in [('collector',['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003']),('producer',['python','/tmp/runcontrol_producer.py'])]:
  processes.append(subprocess.Popen(args,stdout=open(root/(name+'.log'),'w'),stderr=subprocess.STDOUT))
 wait(lambda:state().get('rc_state')=='RUNNING')
 responses['first']=request('first','terminate')
 responses['overlapping']=request('overlapping','repeat')
 wait(lambda:state().get('rc_state')=='TERMINATED')
 responses['terminal']=request('terminal','stop_next')
 good=bool(responses['first'].get('accepted')) and all(responses[x].get('accepted') is False and bool(responses[x].get('reason')) for x in ('overlapping','terminal'))
 result={'status':'pass' if good else 'fail','responses':responses,'terminal_state':state().get('rc_state')}
except Exception as exc:
 result={'status':'fail','reason':str(exc),'traceback':traceback.format_exc(),'responses':responses,'state':state()}
finally:
 for p in processes:
  if p.poll() is None:p.terminate()
 subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
pathlib.Path('/tmp/accepted-rejections.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));sys.exit(0 if result['status']=='pass' else 1)
