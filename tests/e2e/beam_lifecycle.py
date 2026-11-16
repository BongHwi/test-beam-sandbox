# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the existing lifecycle producer beside an independent session source."""
import json,os,pathlib,subprocess,time
root=pathlib.Path('/tmp/runcontrol-e2e');root.mkdir(exist_ok=True)
session=pathlib.Path('/tmp/session-run');session.mkdir(exist_ok=True)
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/runcontrol-e2e/a.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
(root/'a.conf').write_text('[RunControl]\nNEVENTS = 1000000\n[Producer.TEST_0]\nVALUE = first\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/runcontrol-e2e/run$3R$X\n')
private={'seed':1857092461,'device_seed':1857092460,'beam':{'position_mm':[0,0,-20], 'direction':[0,0,1], 'phase_space_covariance':[[0]*5 for _ in range(4)]},'rate_hz':220}
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private))
source=subprocess.Popen(['python','-m','session_core.server','--geometry','/tmp/session_example_geometry.json','--socket','/tmp/session-run/source.sock'],env=env,stdout=subprocess.DEVNULL,stderr=open('/tmp/lifecycle-source.log','w'))
procs=[]
try:
 deadline=time.monotonic()+15
 while not (session/'source.sock').exists():
  if source.poll() is not None or time.monotonic()>deadline:raise RuntimeError('source did not start')
  time.sleep(.05)
 cmd='python -u /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py --address 44003 --ini /tmp/runcontrol-e2e/init.ini --status-file /tmp/session-run/status.json --control-socket /tmp/session-run/control.sock --one-pass'
 session_name=os.environ.get('BEAM_LIFECYCLE_SESSION','e2e')
 subprocess.run(['tmux','new-session','-d','-s',session_name,'-n','rc',cmd],check=True)
 time.sleep(1)
 for n,args in [('collector',['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003']),('producer',['python','/tmp/runcontrol_producer.py'])]:
  procs.append(subprocess.Popen(args,stdout=open(str(root/(n+'.log')),'w'),stderr=subprocess.STDOUT))
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  if (root/'finish').exists():break
  if not (root/'terminating').exists() and (source.poll() is not None or any(p.poll() is not None for p in procs)):raise RuntimeError('process exited')
  time.sleep(.1)
finally:
 for p in procs+[source]:
  if p.poll() is None:p.terminate()
 subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
