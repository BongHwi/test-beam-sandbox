# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare original and hooked ITS3 behavior after a real producer exception."""
import json, os, pathlib, subprocess, time, traceback
root=pathlib.Path('/tmp/runcontrol-e2e'); out=pathlib.Path('/tmp/runcontrol-results'); root.mkdir(exist_ok=True); out.mkdir(exist_ok=True); os.chdir(root)
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/runcontrol-e2e/a.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
(root/'a.conf').write_text('[RunControl]\nNEVENTS = 1000000\n[Producer.TEST_0]\nVALUE = first\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/runcontrol-e2e/run$3R$X\n')
def wait(pred):
    end=time.monotonic()+25
    while time.monotonic()<end:
        if pred(): return True
        time.sleep(.1)
    raise RuntimeError('producer ERROR state not observed')
def run(original):
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
    for name in ('calls.jsonl','status.json','error.log'):
        p=root/name
        if p.exists(): p.unlink()
    binary='ITS3RunControl.original.py' if original else 'ITS3RunControl.py'
    options='' if original else ' --status-file /tmp/runcontrol-e2e/status.json'
    command=f'python -u /opt/eudaq-src/user/ITS3/python/{binary} --address 44003 --ini /tmp/runcontrol-e2e/init.ini{options} 2>/tmp/runcontrol-e2e/error.log'
    subprocess.run(['tmux','new-session','-d','-s','error',command],check=True); time.sleep(1)
    ps=[]
    try:
        for args,env in [(['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003'],None),(['python','/tmp/runcontrol_producer.py'],dict(os.environ,RUNCONTROL_TEST_ERROR='1'))]:
            ps.append(subprocess.Popen(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        if original:
            wait(lambda:(root/'error.log').exists() and 'in ERROR state' in (root/'error.log').read_text(errors='replace'))
            observed='ERROR (upstream log)'
        else:
            def state():
                try: return json.loads((root/'status.json').read_text()).get('rc_state')=='ERROR'
                except (FileNotFoundError,json.JSONDecodeError): return False
            try:
                wait(state); observed=json.loads((root/'status.json').read_text())
            except RuntimeError:
                observed=json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
        time.sleep(1)
        records=[json.loads(s) for s in (root/'calls.jsonl').read_text().splitlines()]
        lifecycle=[{k:v for k,v in c.items() if k!='count'} for c in records if c['call'] not in ('Event','Status')]
        return {'lifecycle':lifecycle,'observed_error':observed}
    finally:
        for p in ps:
            if p.poll() is None: p.terminate()
        subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
        time.sleep(.5)
try:
    original=run(True); hooked=run(False)
    status='pass' if original['lifecycle']==hooked['lifecycle'] and hooked['observed_error'].get('rc_state')=='ERROR' else 'fail'
    result={'status':status,'measurement':{'original':original,'hooked':hooked,'injection':'test producer raises in DoConfigure'}}
except Exception as exc: result={'status':'not_run','measurement':{'error':str(exc),'traceback':traceback.format_exc(),'reason':'error comparison did not reach its criterion'}}
(out/'H-E8.json').write_text(json.dumps(result,indent=2)+'\n')
