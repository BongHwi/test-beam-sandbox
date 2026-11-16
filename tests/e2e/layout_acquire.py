# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise generated layout through public launcher and installed converters."""
import json, os, subprocess, socket, time, traceback
from pathlib import Path
import pyeudaq
from msensor.stream import read
from integration.layout import generate
root=Path('/tmp/layout-e2e');root.mkdir()
layout=json.loads(Path('/tmp/layout-input.json').read_text())
generate(layout,root)
if os.environ.get('LAYOUT_HIGH_THRESHOLD'):
    p=root/'a.conf';s=p.read_text();start=s.index('[Producer.MSENSOR_0]');end=s.find('[',start+1)
    end=len(s) if end<0 else end
    s=s[:start]+s[start:end].replace('threshold_dac = 100','threshold_dac = 200')+s[end:];p.write_text(s)
sock=str(root/'source.sock')
private={'seed':1857092461,'device_seed':1857092460,'beam':{'position_mm':[1.234567891,-2.345678912,-20], 'direction':[0.00123456789,0,1], 'phase_space_covariance':[[0.00345678912,0,0,0,0],[0,0.00456789123,0,0,0]]+[[0]*5 for _ in range(3)]},'rate_hz':237.654321987}
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private),SESSION_SOURCE_SOCKET=sock,
 VIRTUAL_ALPIDE_SOURCE_SOCKET=sock,MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':86753091234}),
 GIT_DIR='/opt/virtual-producer-cwd/.git',GIT_WORK_TREE='/opt/virtual-producer-cwd',
 RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'),RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'),
 PYTHONPATH='/tmp:'+os.environ['PYTHONPATH'])
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),
 '--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44000',
 '--producer-env','SESSION_SOURCE_SOCKET','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET',
 '--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--producer-env','GIT_DIR','--producer-env','GIT_WORK_TREE','--no-attach','--session-geometry',str(root/'geometry.json'),
 '--session-socket',sock,'--session-beam-on','--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']
os.chdir(root)
try:
 p=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=30)
 (root/'launcher.log').write_text(p.stdout+p.stderr)
 if p.returncode:raise RuntimeError('launcher failed '+p.stderr[-1200:])
 status={};start=None;stopped=False;deadline=time.monotonic()+120
 has_msensor=any(d['kind']=='msensor' for d in layout['devices'])
 while time.monotonic()<deadline:
  try:status=json.loads((root/'status.json').read_text())
  except (OSError,ValueError):pass
  if status.get('rc_state') in ('TERMINATED','ERROR'):break
  if status.get('rc_state')=='RUNNING':
   if start is None:start=time.monotonic()
   elapsed=time.monotonic()-start
   done=any(c['name'].startswith('MSENSOR_') and c['message']=='source_finished' for c in status.get('connections',[])) if has_msensor else elapsed>5
   if done and not stopped:
    with socket.socket(socket.AF_UNIX) as s:
     s.settimeout(5);s.connect(str(root/'control.sock'));s.sendall(b'{"id":"layout-stop","intent":"stop_next"}\n')
     if not json.loads(s.recv(4096)).get('accepted'):raise RuntimeError('stop rejected')
    stopped=True
  time.sleep(.02)
 if status.get('rc_state')!='TERMINATED' or not stopped:raise RuntimeError('run did not close normally: '+str(status.get('rc_state')))
 raws=list(root.glob('*.raw'))
 if len(raws)!=1:raise RuntimeError('expected one raw')
 mapping=json.loads((root/'mapping.json').read_text()); expected={x['plane'] for x in mapping};status_names={x['producer']+'_status' for x in mapping}
 reader=pyeudaq.FileReader('native',str(raws[0]));bore=set();eore=set();groups=[]
 while True:
  ev=reader.GetNextEvent()
  if ev is None:break
  if ev.IsBORE():bore.add(ev.GetDescription())
  if ev.IsEORE():eore.add(ev.GetDescription())
  if ev.GetDescription()=='ITS3global':
   subs=ev.GetSubEvents();groups.append([(x.GetDescription(),x.GetDeviceN(),x.GetTriggerN()) for x in subs])
 aligned=bool(groups) and all(len(g)==len(expected) and {x[1] for x in g}==expected and len({x[2] for x in g})==1 for g in groups)
 p=subprocess.run(['/tmp/layout-convert',str(raws[0])],text=True,capture_output=True,timeout=120)
 if p.returncode:raise RuntimeError('converter failed: '+str(p.returncode)+' '+p.stderr[-1000:])
 counts={str(i):{'events':0,'hits':0,'sum_columns':0.0,'min_column':None,'max_column':None} for i in expected}
 for line in p.stdout.splitlines():
  name,number,trigger,hits,total,lo,hi=line.split();item=counts[number];item['events']+=1;item['hits']+=int(hits);item['sum_columns']+=float(total)
  if int(hits):
   item['min_column']=int(lo) if item['min_column'] is None else min(item['min_column'],int(lo))
   item['max_column']=int(hi) if item['max_column'] is None else max(item['max_column'],int(hi))
 streams=list(root.glob('*.msws'));stream_names=[]
 for f in streams:
  stream=read(f)
  if stream['status']!='closed_clean':raise RuntimeError('stream not closed cleanly')
  stream_names.append(f.name.split('_run')[0])
 errors=[]
 if (root/'published.jsonl').exists():
  for line in (root/'published.jsonl').read_text().splitlines():
   row=json.loads(line)
   if row.get('rc_state')=='ERROR' or any(c.get('state')=='ERROR' for c in row.get('connections',[])):errors.append('error state')
 result={'closed':status_names<=bore and status_names<=eore,'aligned':aligned,'events':len(groups),
         'counts':counts,'stream_names':sorted(stream_names),'errors':errors,
         'converter_events':sum(x['events'] for x in counts.values())}
 result['common_pass']=result['closed'] and aligned and not errors and all(x['events']==len(groups) for x in counts.values())
 (root/'measurement.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result),flush=True)
 if not result['common_pass']:raise RuntimeError('common E2E condition failed')
except Exception:
 traceback.print_exc()
 for pane in ('rc','rp.0','dp.0','dc'):
  p=subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+pane,'-S','-120'],capture_output=True,text=True);print('PANE',pane,p.stdout[-2500:],flush=True)
 raise
finally:subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
